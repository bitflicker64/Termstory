"""
test_predict.py — Unit tests for termstory/predict.py (Pre-Cognitive Workspace)

Tests cover:
  - Noise filtering
  - Time-of-day bucketing
  - Signal computation (recency, affinity, interrupted detection)
  - predict() return shape and ranking logic
  - format_predict_output() render contract
  - Empty / legacy-only history edge cases
"""

import sqlite3
import tempfile
import os
import time
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from termstory.predict import (
    Predictor,
    format_predict_output,
    _is_noise,
    _hour_bucket,
    _day_label,
)


# ─── Helpers ────────────────────────────────────────────────────────────────

def _make_db(sessions_spec: list) -> str:
    """
    Create a temp SQLite DB populated from a list of session specs.

    Each spec is a dict:
        {
            "start":    datetime,
            "end":      datetime,       # optional, defaults to start + 30m
            "project":  str,            # project name
            "path":     str,            # optional project path
            "commands": ["cmd1", ...],  # optional
            "legacy":   bool,           # all commands legacy? default False
        }

    Returns the path to the temp DB file.
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_path = tmp.name
    tmp.close()

    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    c.execute("""
        CREATE TABLE projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            path TEXT UNIQUE,
            first_seen INTEGER,
            last_seen INTEGER,
            project_context TEXT,
            created_at INTEGER DEFAULT (strftime('%s', 'now'))
        )
    """)
    c.execute("""
        CREATE TABLE sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            start_time INTEGER NOT NULL,
            end_time INTEGER NOT NULL,
            duration_seconds INTEGER,
            project_id INTEGER,
            ai_summary TEXT
        )
    """)
    c.execute("""
        CREATE TABLE commands (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER NOT NULL,
            command TEXT NOT NULL,
            exit_code INTEGER DEFAULT 0,
            session_id INTEGER,
            project_id INTEGER,
            recovery_source TEXT,
            is_legacy BOOLEAN DEFAULT 0
        )
    """)
    conn.commit()

    project_ids: dict = {}

    for spec in sessions_spec:
        proj_name = spec.get("project", "Other")
        proj_path = spec.get("path", f"/home/user/{proj_name}")

        if proj_name not in project_ids:
            c.execute(
                "INSERT INTO projects (name, path, first_seen, last_seen) VALUES (?, ?, ?, ?)",
                (proj_name, proj_path, 0, int(time.time()))
            )
            project_ids[proj_name] = c.lastrowid

        p_id = project_ids[proj_name]
        start_dt: datetime = spec["start"]
        end_dt: datetime = spec.get("end", start_dt + timedelta(minutes=30))
        start_ts = int(start_dt.timestamp())
        end_ts = int(end_dt.timestamp())
        dur = end_ts - start_ts

        c.execute(
            "INSERT INTO sessions (start_time, end_time, duration_seconds, project_id) VALUES (?, ?, ?, ?)",
            (start_ts, end_ts, dur, p_id)
        )
        s_id = c.lastrowid

        cmds = spec.get("commands", ["python -m pytest", "git commit -m 'fix'"])
        is_legacy = 1 if spec.get("legacy", False) else 0
        for cmd in cmds:
            c.execute(
                "INSERT INTO commands (timestamp, command, session_id, project_id, is_legacy) VALUES (?, ?, ?, ?, ?)",
                (start_ts, cmd, s_id, p_id, is_legacy)
            )

    conn.commit()
    conn.close()
    return db_path


# ─── Noise filter tests ──────────────────────────────────────────────────────

class TestIsNoise:
    def test_exact_noise(self):
        for cmd in ["ls", "pwd", "clear", "exit", "history", "cd"]:
            assert _is_noise(cmd), f"Expected '{cmd}' to be noise"

    def test_prefix_noise(self):
        assert _is_noise("cd /home/user/projects")
        assert _is_noise("git status")
        assert _is_noise("docker ps -a")
        assert _is_noise("grep -r 'TODO'")

    def test_not_noise(self):
        assert not _is_noise("python -m pytest")
        assert not _is_noise("cargo build --release")
        assert not _is_noise("npm run dev")
        assert not _is_noise("git commit -m 'feat: add predict'")
        assert not _is_noise("docker build -t myapp .")

    def test_whitespace_stripped(self):
        assert _is_noise("  ls  ")
        assert not _is_noise("  npm install  ")


# ─── Time bucketing tests ────────────────────────────────────────────────────

class TestHourBucket:
    def test_buckets(self):
        assert _hour_bucket(2)  == "night"
        assert _hour_bucket(7)  == "early-morning"
        assert _hour_bucket(10) == "morning"
        assert _hour_bucket(13) == "midday"
        assert _hour_bucket(16) == "afternoon"
        assert _hour_bucket(19) == "evening"
        assert _hour_bucket(23) == "late-night"

    def test_boundaries(self):
        assert _hour_bucket(0)  == "night"
        assert _hour_bucket(6)  == "early-morning"
        assert _hour_bucket(9)  == "morning"
        assert _hour_bucket(12) == "midday"
        assert _hour_bucket(14) == "afternoon"
        assert _hour_bucket(18) == "evening"
        assert _hour_bucket(22) == "late-night"


class TestDayLabel:
    def test_all_days(self):
        expected = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        for i, label in enumerate(expected):
            assert _day_label(i) == label


# ─── Predictor integration tests ─────────────────────────────────────────────

class TestPredictorEmptyHistory:
    def test_empty_db(self):
        db_path = _make_db([])
        try:
            p = Predictor(db_path)
            result = p.predict()
            assert result["top_projects"] == []
            assert result["total_sessions_analysed"] == 0
            assert "message" in result
        finally:
            os.unlink(db_path)

    def test_legacy_only_db(self):
        """All legacy sessions should be excluded; result should reflect empty history."""
        now = datetime.now()
        specs = [
            {"start": now - timedelta(hours=2), "project": "old-proj", "legacy": True},
            {"start": now - timedelta(hours=5), "project": "old-proj", "legacy": True},
        ]
        db_path = _make_db(specs)
        try:
            p = Predictor(db_path)
            result = p.predict()
            assert result["total_sessions_analysed"] == 0
            assert result["top_projects"] == []
        finally:
            os.unlink(db_path)


class TestPredictorRanking:
    def test_recency_drives_top_rank(self):
        """Project with sessions in the last 24h should outrank older projects."""
        now = datetime.now()
        specs = [
            # Project A: very recent
            {"start": now - timedelta(hours=2), "project": "ProjectA",
             "commands": ["npm run dev", "git add ."]},
            {"start": now - timedelta(hours=4), "project": "ProjectA",
             "commands": ["npm run dev"]},
            # Project B: 8 days ago
            {"start": now - timedelta(days=8), "project": "ProjectB",
             "commands": ["cargo build", "cargo test"]},
            {"start": now - timedelta(days=8, hours=2), "project": "ProjectB",
             "commands": ["cargo build"]},
        ]
        db_path = _make_db(specs)
        try:
            p = Predictor(db_path)
            result = p.predict(top_n=2, now=now)
            assert result["top_projects"][0]["project_name"] == "ProjectA"
            assert result["top_projects"][0]["rank"] == 1
        finally:
            os.unlink(db_path)

    def test_interrupted_session_boost(self):
        """A project with an interrupted session (>12h gap) should get score boost."""
        now = datetime.now()
        # Project A: interrupted 14h ago with no followup
        interrupted_start = now - timedelta(hours=14, minutes=30)
        interrupted_end = now - timedelta(hours=14)
        # Project B: older, no recent sessions
        specs = [
            {
                "start": interrupted_start,
                "end": interrupted_end,
                "project": "DebugProject",
                "commands": ["python debug.py", "pdb script.py"],
            },
            {
                "start": now - timedelta(days=5),
                "project": "OtherProject",
                "commands": ["make build"],
            },
        ]
        db_path = _make_db(specs)
        try:
            p = Predictor(db_path)
            result = p.predict(top_n=2, now=now)
            # DebugProject should be ranked #1 due to interrupted boost
            top = result["top_projects"][0]
            assert top["project_name"] == "DebugProject"
            assert "interrupted" in top["signals"]
            assert top["interrupted_at"] is not None
        finally:
            os.unlink(db_path)

    def test_top_n_capped(self):
        """Result should contain at most top_n projects."""
        now = datetime.now()
        specs = [
            {"start": now - timedelta(hours=i), "project": f"Proj{i}",
             "commands": ["python run.py"]}
            for i in range(1, 8)
        ]
        db_path = _make_db(specs)
        try:
            p = Predictor(db_path)
            result = p.predict(top_n=3, now=now)
            assert len(result["top_projects"]) <= 3
        finally:
            os.unlink(db_path)

    def test_result_structure(self):
        """Every top_projects entry must have the expected keys."""
        now = datetime.now()
        specs = [
            {"start": now - timedelta(hours=1), "project": "StructTest",
             "commands": ["cargo build"]},
        ]
        db_path = _make_db(specs)
        required_keys = {
            "rank", "project_name", "project_path", "score",
            "signals", "suggested_commands", "interrupted_at"
        }
        try:
            p = Predictor(db_path)
            result = p.predict(top_n=1, now=now)
            assert "now" in result
            assert "time_context" in result
            assert "total_sessions_analysed" in result
            for entry in result["top_projects"]:
                assert required_keys.issubset(entry.keys()), (
                    f"Missing keys: {required_keys - entry.keys()}"
                )
        finally:
            os.unlink(db_path)

    def test_suggested_commands_excludes_noise(self):
        """Suggested commands should not contain noise commands."""
        now = datetime.now()
        specs = [
            {
                "start": now - timedelta(hours=1),
                "project": "NoiseTest",
                "commands": ["ls", "cd /tmp", "pwd", "python -m pytest", "git commit -m 'fix'"],
            }
        ]
        db_path = _make_db(specs)
        try:
            p = Predictor(db_path)
            result = p.predict(top_n=1, now=now)
            if result["top_projects"]:
                for cmd in result["top_projects"][0]["suggested_commands"]:
                    assert not _is_noise(cmd), f"Noise cmd in suggestions: {cmd}"
        finally:
            os.unlink(db_path)

    def test_time_context_format(self):
        """time_context should be 'DayName bucket-label' formatted string."""
        now = datetime(2026, 6, 15, 10, 0, 0)  # Monday 10:00 → morning
        specs = [
            {"start": now - timedelta(hours=2), "project": "CtxTest",
             "commands": ["python run.py"]},
        ]
        db_path = _make_db(specs)
        try:
            p = Predictor(db_path)
            result = p.predict(top_n=1, now=now)
            assert "Mon" in result["time_context"]
            assert "morning" in result["time_context"]
        finally:
            os.unlink(db_path)

    def test_scores_positive(self):
        """All combined scores should be >= 0."""
        now = datetime.now()
        specs = [
            {"start": now - timedelta(hours=i * 3), "project": f"P{i}",
             "commands": ["make test"]}
            for i in range(1, 5)
        ]
        db_path = _make_db(specs)
        try:
            p = Predictor(db_path)
            result = p.predict(top_n=4, now=now)
            for entry in result["top_projects"]:
                assert entry["score"] >= 0.0
        finally:
            os.unlink(db_path)


# ─── Formatter tests ─────────────────────────────────────────────────────────

class TestFormatPredictOutput:
    def _make_result(self, top_projects=None, message=None):
        base = {
            "now": datetime(2026, 6, 15, 9, 30),
            "time_context": "Mon morning",
            "total_sessions_analysed": 120,
        }
        if message:
            base["message"] = message
            base["top_projects"] = []
        else:
            base["top_projects"] = top_projects or []
        return base

    def test_header_always_present(self):
        result = self._make_result(message="No history found")
        out = format_predict_output(result)
        assert "Pre-Cognitive Workspace" in out

    def test_message_shown_when_empty(self):
        result = self._make_result(message="No session history found.")
        out = format_predict_output(result)
        assert "No session history found" in out

    def test_project_name_in_output(self):
        result = self._make_result(top_projects=[
            {
                "rank": 1,
                "project_name": "termstory",
                "project_path": "/home/user/termstory",
                "score": 3.75,
                "signals": ["recency", "morning affinity"],
                "suggested_commands": ["python -m pytest", "git add ."],
                "interrupted_at": None,
            }
        ])
        out = format_predict_output(result)
        assert "termstory" in out
        assert "3.75" in out

    def test_interrupted_marker_shown(self):
        result = self._make_result(top_projects=[
            {
                "rank": 1,
                "project_name": "DebugProj",
                "project_path": "",
                "score": 5.0,
                "signals": ["interrupted"],
                "suggested_commands": [],
                "interrupted_at": "Friday 18:42",
            }
        ])
        out = format_predict_output(result)
        assert "Friday 18:42" in out
        assert "Interrupted" in out or "interrupted" in out.lower()

    def test_suggested_commands_shown(self):
        result = self._make_result(top_projects=[
            {
                "rank": 1,
                "project_name": "MyApp",
                "project_path": "",
                "score": 2.1,
                "signals": ["recency"],
                "suggested_commands": ["cargo build", "cargo test"],
                "interrupted_at": None,
            }
        ])
        out = format_predict_output(result)
        assert "cargo build" in out
        assert "cargo test" in out

    def test_empty_top_projects_message(self):
        result = self._make_result(top_projects=[])
        out = format_predict_output(result)
        assert "Insufficient" in out or "No session" in out or "predict" in out.lower()

    def test_footer_tip_present(self):
        result = self._make_result(top_projects=[
            {
                "rank": 1,
                "project_name": "X",
                "project_path": "",
                "score": 1.0,
                "signals": ["recency"],
                "suggested_commands": [],
                "interrupted_at": None,
            }
        ])
        out = format_predict_output(result)
        assert "termstory predict" in out


class TestPredictorDaysFilter:
    def test_days_filter_excludes_older_sessions(self):
        now = datetime.now()
        specs = [
            # Session 1: 5 days ago (should be included if days=7)
            {"start": now - timedelta(days=5), "project": "ProjectA", "duration": 3600, "commands": ["python run.py"]},
            # Session 2: 10 days ago (should be excluded if days=7)
            {"start": now - timedelta(days=10), "project": "ProjectB", "duration": 3600, "commands": ["git status"]},
        ]
        db_path = _make_db(specs)
        try:
            p = Predictor(db_path)
            # With days=7, only ProjectA should be analyzed
            res = p.predict(now=now, days=7)
            projects = [x["project_name"] for x in res["top_projects"]]
            assert "ProjectA" in projects
            assert "ProjectB" not in projects
            
            # With days=15, both should be analyzed
            res_all = p.predict(now=now, days=15)
            projects_all = [x["project_name"] for x in res_all["top_projects"]]
            assert "ProjectA" in projects_all
            assert "ProjectB" in projects_all
        finally:
            os.unlink(db_path)
