"""Tests for the flow analyzer module (termstory.flow_analyzer).

Exercises all pure-computation helpers with hand-crafted Session objects.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import List, Optional

import pytest

from termstory.models import Command, Session
from termstory.flow_analyzer import (
    _command_gaps,
    _classify_productivity,
    _daily_flow_summary,
    _detect_flow_blocks,
    _distraction_patterns,
    _distraction_score,
    _productivity_score,
    _session_flow_analysis,
    _top_flow_blocks,
    build_flow_report,
)


# ── fixtures ────────────────────────────────────────────────────────

def _ts(y: int, m: int, d: int, h: int = 9, mi: int = 0) -> int:
    return int(datetime(y, m, d, h, mi).timestamp())


def _cmd(ts: int, text: str = "git push", exit_code: int = 0, pid: Optional[int] = None) -> Command:
    return Command(timestamp=ts, command=text, exit_code=exit_code, project_id=pid)


def _session(d: date, dur: int = 3600, project_id: Optional[int] = None,
             commands: Optional[List[Command]] = None, sid: int = 1) -> Session:
    ts = int(datetime(d.year, d.month, d.day, 9).timestamp())
    return Session(
        id=sid, start_time=ts, end_time=ts + dur,
        duration_seconds=dur, project_id=project_id,
        commands=commands or [],
    )


# ── tests: _command_gaps ───────────────────────────────────────────


class TestCommandGaps:
    def test_empty(self):
        assert _command_gaps([]) == []

    def test_single(self):
        assert _command_gaps([_cmd(_ts(2026, 6, 1, 9))]) == []

    def test_gaps(self):
        cmds = [
            _cmd(_ts(2026, 6, 1, 9, 0)),
            _cmd(_ts(2026, 6, 1, 9, 5)),
            _cmd(_ts(2026, 6, 1, 9, 15)),
        ]
        gaps = _command_gaps(cmds)
        assert gaps == [300, 600]


# ── tests: _detect_flow_blocks ─────────────────────────────────────


class TestDetectFlowBlocks:
    def test_empty(self):
        assert _detect_flow_blocks([]) == []

    def test_no_flow_too_short(self):
        """Commands spanning < 10 minutes don't form a flow block."""
        cmds = [_cmd(_ts(2026, 6, 1, 9, i)) for i in range(5)]
        blocks = _detect_flow_blocks(cmds, min_seconds=600)
        assert len(blocks) == 0

    def test_flow_detected(self):
        """Commands spanning > 10 minutes with small gaps form a flow block."""
        base = _ts(2026, 6, 1, 9, 0)
        cmds = [_cmd(base + i * 60) for i in range(15)]  # 15 minutes
        blocks = _detect_flow_blocks(cmds, min_seconds=600, gap_tolerance=300)
        assert len(blocks) == 1
        assert blocks[0]["duration"] >= 600

    def test_gap_breaks_flow(self):
        """A large gap splits flow into separate blocks."""
        base = _ts(2026, 6, 1, 9, 0)
        cmds1 = [_cmd(base + i * 60) for i in range(12)]  # 12 min
        cmds2 = [_cmd(base + 1200 + i * 60) for i in range(12)]  # 12 min, 8 min gap
        blocks = _detect_flow_blocks(cmds1 + cmds2, min_seconds=600, gap_tolerance=300)
        assert len(blocks) == 2

    def test_flow_block_fields(self):
        base = _ts(2026, 6, 1, 9, 0)
        cmds = [_cmd(base + i * 60) for i in range(15)]
        blocks = _detect_flow_blocks(cmds, min_seconds=600)
        b = blocks[0]
        assert "start" in b
        assert "end" in b
        assert "duration" in b
        assert "commands" in b
        assert "avg_gap" in b
        assert b["commands"] == 15

    def test_min_gap_tolerance(self):
        """Gap exactly at tolerance is included."""
        base = _ts(2026, 6, 1, 9, 0)
        cmds = [_cmd(base), _cmd(base + 300)]  # exactly 300s gap
        blocks = _detect_flow_blocks(cmds, min_seconds=100, gap_tolerance=300)
        assert len(blocks) == 1


# ── tests: _distraction_score ──────────────────────────────────────


class TestDistractionScore:
    def test_empty(self):
        assert _distraction_score([]) == 0.0

    def test_clean_session(self):
        cmds = [_cmd(_ts(2026, 6, 1, 9, i), "python script.py", 0) for i in range(10)]
        score = _distraction_score(cmds)
        assert score < 30  # low distraction

    def test_noisy_session(self):
        cmds = [_cmd(_ts(2026, 6, 1, 9, i), "ls", 0) for i in range(10)]
        score = _distraction_score(cmds)
        assert score > 30  # noise commands increase score

    def test_error_heavy(self):
        cmds = [_cmd(_ts(2026, 6, 1, 9, i), "pytest", 1) for i in range(10)]
        score = _distraction_score(cmds)
        assert score > 20


# ── tests: _productivity_score ─────────────────────────────────────


class TestProductivityScore:
    def test_empty(self):
        assert _productivity_score([], 0, []) == 0.0

    def test_high_productivity(self):
        """Many commands in short time with flow blocks = high score."""
        cmds = [_cmd(_ts(2026, 6, 1, 9, i), "python script.py", 0) for i in range(20)]
        flow_blocks = [{"duration": 1000}]
        score = _productivity_score(cmds, 1200, flow_blocks)
        assert score > 60

    def test_low_productivity(self):
        """Few commands, no flow, errors = low score."""
        cmds = [_cmd(_ts(2026, 6, 1, 9), "ls", 0)]
        score = _productivity_score(cmds, 3600, [])
        assert score < 40

    def test_score_range(self):
        cmds = [_cmd(_ts(2026, 6, 1, 9, i), "git push", 0) for i in range(10)]
        score = _productivity_score(cmds, 600, [{"duration": 300}])
        assert 0.0 <= score <= 100.0


# ── tests: _classify_productivity ──────────────────────────────────


class TestClassifyProductivity:
    def test_tiers(self):
        assert _classify_productivity(90) == "🚀 Hyperproductivity"
        assert _classify_productivity(70) == "⚡ Flow State"
        assert _classify_productivity(50) == "🔥 Solid Focus"
        assert _classify_productivity(30) == "😴 Warming Up"
        assert _classify_productivity(10) == "🌊 Drifting"

    def test_boundary(self):
        assert _classify_productivity(80) == "🚀 Hyperproductivity"
        assert _classify_productivity(79) == "⚡ Flow State"


# ── tests: _daily_flow_summary ─────────────────────────────────────


class TestDailyFlowSummary:
    def test_basic(self):
        s = _session(date(2026, 6, 1), dur=3600,
                     commands=[_cmd(_ts(2026, 6, 1, 9, i), "python run.py") for i in range(10)])
        result = _daily_flow_summary([s])
        assert len(result) == 1
        assert result[0]["date"] == "2026-06-01"
        assert result[0]["total_time"] == 3600
        assert "productivity_score" in result[0]
        assert "distraction_score" in result[0]
        assert "tier" in result[0]

    def test_empty(self):
        assert _daily_flow_summary([]) == []


# ── tests: _session_flow_analysis ──────────────────────────────────


class TestSessionFlowAnalysis:
    def test_sorted_by_productivity(self):
        s1 = _session(date(2026, 6, 1), dur=3600, commands=[
            _cmd(_ts(2026, 6, 1, 9), "ls", 0),
        ])
        s2 = _session(date(2026, 6, 2), dur=3600, commands=[
            _cmd(_ts(2026, 6, 2, 9, i), "python deploy.py", 0) for i in range(20)
        ])
        result = _session_flow_analysis([s1, s2])
        # s2 should be higher productivity
        assert result[0]["productivity_score"] >= result[1]["productivity_score"]

    def test_fields(self):
        s = _session(date(2026, 6, 1), commands=[_cmd(_ts(2026, 6, 1, 9))])
        result = _session_flow_analysis([s])
        assert len(result) == 1
        r = result[0]
        assert "session_id" in r
        assert "date" in r
        assert "time" in r
        assert "flow_blocks" in r
        assert "flow_details" in r


# ── tests: _top_flow_blocks ────────────────────────────────────────


class TestTopFlowBlocks:
    def test_basic(self):
        base = _ts(2026, 6, 1, 9, 0)
        cmds = [_cmd(base + i * 60, "python run.py") for i in range(20)]
        s = _session(date(2026, 6, 1), commands=cmds)
        result = _top_flow_blocks([s], limit=3)
        assert len(result) >= 1
        assert "dominant_category" in result[0]

    def test_limit(self):
        sessions = []
        for day in range(5):
            base = _ts(2026, 6, 1 + day, 9, 0)
            cmds = [_cmd(base + i * 60, "git push") for i in range(15)]
            sessions.append(_session(date(2026, 6, 1 + day), commands=cmds))
        result = _top_flow_blocks(sessions, limit=3)
        assert len(result) <= 3


# ── tests: _distraction_patterns ───────────────────────────────────


class TestDistractionPatterns:
    def test_empty(self):
        result = _distraction_patterns([])
        assert result["noise_commands"] == []
        assert result["error_hotspots"] == []
        assert result["context_switches"] == 0

    def test_basic(self):
        s = _session(date(2026, 6, 1), commands=[
            _cmd(_ts(2026, 6, 1, 9), "git push", 0),
            _cmd(_ts(2026, 6, 1, 9, 1), "ls", 0),      # noise
            _cmd(_ts(2026, 6, 1, 9, 2), "docker ps", 1), # error
            _cmd(_ts(2026, 6, 1, 9, 3), "git commit", 0),
        ])
        result = _distraction_patterns([s])
        assert result["context_switches"] > 0


# ── tests: build_flow_report (integration) ─────────────────────────


class TestBuildFlowReport:
    def test_basic_structure(self):
        cmds = [_cmd(_ts(2026, 6, 1, 9, i), "python run.py", 0) for i in range(20)]
        s = _session(date(2026, 6, 1), dur=3600, commands=cmds)
        report = build_flow_report([s])

        assert report["total_sessions"] == 1
        assert report["total_commands"] == 20
        assert report["total_time"] == 3600
        assert "total_flow_blocks" in report
        assert "flow_coverage" in report
        assert "avg_productivity" in report
        assert "best_session" in report
        assert "worst_session" in report
        assert "daily" in report
        assert "top_flow_blocks" in report
        assert "distractions" in report
        assert "session_analysis" in report

    def test_empty(self):
        report = build_flow_report([])
        assert report["total_sessions"] == 0
        assert report["avg_productivity"] == 0.0
        assert report["flow_coverage"] == 0.0

    def test_flow_coverage(self):
        """Flow coverage should be the fraction of time in flow blocks."""
        base = _ts(2026, 6, 1, 9, 0)
        cmds = [_cmd(base + i * 60, "python deploy.py") for i in range(20)]
        s = _session(date(2026, 6, 1), dur=1500, commands=cmds)
        report = build_flow_report([s])
        assert report["flow_coverage"] >= 0.0

    def test_distractions_populated(self):
        cmds = [_cmd(_ts(2026, 6, 1, 9, i), "ls" if i % 3 == 0 else "python run.py") for i in range(15)]
        s = _session(date(2026, 6, 1), commands=cmds)
        report = build_flow_report([s])
        assert "noise_commands" in report["distractions"]

    def test_daily_populated(self):
        s1 = _session(date(2026, 6, 1), commands=[_cmd(_ts(2026, 6, 1, 9))])
        s2 = _session(date(2026, 6, 2), commands=[_cmd(_ts(2026, 6, 2, 9))])
        report = build_flow_report([s1, s2])
        assert len(report["daily"]) == 2


# ── tests: format_flow_output (smoke) ──────────────────────────────


class TestFormatFlowOutput:
    def test_empty(self):
        from termstory.formatter import format_flow_output
        report = build_flow_report([])
        output = format_flow_output(report)
        assert "Flow State Analysis" in output

    def test_full_render(self):
        from termstory.formatter import format_flow_output
        cmds = [_cmd(_ts(2026, 6, 1, 9, i), "python run.py", 0) for i in range(20)]
        s = _session(date(2026, 6, 1), dur=3600, commands=cmds)
        report = build_flow_report([s])
        output = format_flow_output(report)
        assert "Overview" in output
        assert "Flow Coverage" in output
        assert "Avg Productivity" in output

    def test_with_flow_blocks(self):
        from termstory.formatter import format_flow_output
        base = _ts(2026, 6, 1, 9, 0)
        cmds = [_cmd(base + i * 60, "python deploy.py") for i in range(20)]
        s = _session(date(2026, 6, 1), dur=1500, commands=cmds)
        report = build_flow_report([s])
        output = format_flow_output(report)
        if report["top_flow_blocks"]:
            assert "Top Flow Blocks" in output
