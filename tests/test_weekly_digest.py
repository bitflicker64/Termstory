"""Tests for the weekly digest module (termstory.weekly_digest).

These tests exercise the pure-computation helpers with hand-crafted Session
and Command objects so we never touch the database.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional

import pytest

from termstory.models import Command, Project, Session, format_duration
from termstory.weekly_digest import (
    _category_frequency,
    _daily_command_counts,
    _daily_commit_counts,
    _daily_focus_scores,
    _daily_session_counts,
    _daily_session_totals,
    _error_rate,
    _longest_streak_within_range,
    _project_time_distribution,
    _time_of_day_split,
    _top_achievements,
    _week_over_week_delta,
    build_weekly_digest,
)


# ── fixtures ────────────────────────────────────────────────────────


def _ts(y: int, m: int, d: int, h: int = 9, mi: int = 0) -> int:
    """Convenience: build a Unix timestamp for a specific date/time."""
    return int(datetime(y, m, d, h, mi).timestamp())


def _make_session(
    start_ts: int,
    duration: int,
    project_id: Optional[int] = None,
    commands: Optional[List[Command]] = None,
    commits: Optional[List[dict]] = None,
    session_id: int = 1,
) -> Session:
    """Build a minimal Session with the required fields."""
    return Session(
        id=session_id,
        start_time=start_ts,
        end_time=start_ts + duration,
        duration_seconds=duration,
        project_id=project_id,
        commands=commands or [],
        commits=commits or [],
    )


def _make_command(
    timestamp: int,
    command: str,
    exit_code: int = 0,
    session_id: int = 1,
    project_id: Optional[int] = None,
) -> Command:
    """Build a minimal Command."""
    return Command(
        timestamp=timestamp,
        command=command,
        exit_code=exit_code,
        session_id=session_id,
        project_id=project_id,
    )


def _sample_sessions() -> List[Session]:
    """A realistic week of sessions: Mon 2026-06-01 → Sun 2026-06-07."""
    sessions: List[Session] = []

    # Monday — 3 hours on project 1
    s1 = _make_session(_ts(2026, 6, 1, 9), 3600, project_id=1, commands=[
        _make_command(_ts(2026, 6, 1, 9), "git push origin main"),
        _make_command(_ts(2026, 6, 1, 9, 10), "docker compose up -d", exit_code=0),
        _make_command(_ts(2026, 6, 1, 10), "python manage.py test", exit_code=0),
    ])
    s1.commits = [
        {"hash": "abc1234567890", "timestamp": _ts(2026, 6, 1, 9, 5), "message": "feat(auth): add login", "cleaned_message": "feat(auth): add login"},
    ]
    sessions.append(s1)

    # Tuesday — 2 hours on project 1, 1 hour on project 2
    s2 = _make_session(_ts(2026, 6, 2, 10), 7200, project_id=1, commands=[
        _make_command(_ts(2026, 6, 2, 10), "pytest tests/ -v", exit_code=0),
        _make_command(_ts(2026, 6, 2, 10, 30), "grep ERROR logs/app.log"),
    ])
    s2.commits = [
        {"hash": "def2345678901", "timestamp": _ts(2026, 6, 2, 11), "message": "fix(auth): token refresh", "cleaned_message": "fix(auth): token refresh"},
    ]
    sessions.append(s2)

    s3 = _make_session(_ts(2026, 6, 2, 14), 3600, project_id=2, commands=[
        _make_command(_ts(2026, 6, 2, 14), "npm run build", exit_code=1),
        _make_command(_ts(2026, 6, 2, 14, 10), "npm run build", exit_code=0),
    ], session_id=2)
    sessions.append(s3)

    # Wednesday — 1 hour
    s4 = _make_session(_ts(2026, 6, 3, 13), 3600, project_id=1, commands=[
        _make_command(_ts(2026, 6, 3, 13), "git commit -m 'refactor(core)': clean up"),
    ])
    s4.commits = [
        {"hash": "ghi3456789012", "timestamp": _ts(2026, 6, 3, 13, 30), "message": "refactor(core): clean up", "cleaned_message": "refactor(core): clean up"},
    ]
    sessions.append(s4)

    # Thursday — no sessions (gap day)

    # Friday — 4 hours on project 1
    s5 = _make_session(_ts(2026, 6, 5, 9), 14400, project_id=1, commands=[
        _make_command(_ts(2026, 6, 5, 9), "docker compose logs -f"),
        _make_command(_ts(2026, 6, 5, 10), "python manage.py migrate"),
        _make_command(_ts(2026, 6, 5, 11), "git push", exit_code=0),
    ], session_id=3)
    sessions.append(s5)

    # Saturday — 2 hours on project 3
    s6 = _make_session(_ts(2026, 6, 6, 15), 7200, project_id=3, commands=[
        _make_command(_ts(2026, 6, 6, 15), "cargo build --release", exit_code=0),
    ], session_id=4)
    sessions.append(s6)

    return sessions


def _sample_projects() -> List[Project]:
    return [
        Project(id=1, name="termstory", path="~/termstory", first_seen=_ts(2026, 5, 1), last_seen=_ts(2026, 6, 5), session_count=4, total_time=25200),
        Project(id=2, name="frontend-app", path="~/frontend", first_seen=_ts(2026, 5, 15), last_seen=_ts(2026, 6, 2), session_count=1, total_time=3600),
        Project(id=3, name="rust-utils", path="~/rust-utils", first_seen=_ts(2026, 6, 1), last_seen=_ts(2026, 6, 6), session_count=1, total_time=7200),
    ]


# ── tests: _daily_session_totals ────────────────────────────────────


class TestDailySessionTotals:
    def test_basic_totals(self):
        sessions = _sample_sessions()
        totals = _daily_session_totals(sessions)
        assert totals["2026-06-01"] == 3600
        # Tuesday: 7200 + 3600 = 10800
        assert totals["2026-06-02"] == 10800
        assert totals["2026-06-06"] == 7200

    def test_empty(self):
        assert _daily_session_totals([]) == {}

    def test_no_double_counting(self):
        """Each session's duration is counted exactly once per day."""
        s1 = _make_session(_ts(2026, 6, 1, 9), 1800, project_id=1)
        s2 = _make_session(_ts(2026, 6, 1, 14), 1800, project_id=1)
        totals = _daily_session_totals([s1, s2])
        assert totals["2026-06-01"] == 3600


# ── tests: _daily_session_counts ────────────────────────────────────


class TestDailySessionCounts:
    def test_counts(self):
        sessions = _sample_sessions()
        counts = _daily_session_counts(sessions)
        assert counts["2026-06-01"] == 1
        assert counts["2026-06-02"] == 2
        assert counts["2026-06-05"] == 1

    def test_empty(self):
        assert _daily_session_counts([]) == {}


# ── tests: _daily_command_counts ────────────────────────────────────


class TestDailyCommandCounts:
    def test_counts_commands_per_day(self):
        sessions = _sample_sessions()
        counts = _daily_command_counts(sessions)
        # Monday: 3 commands
        assert counts["2026-06-01"] == 3
        # Tuesday: 2 + 2 = 4
        assert counts["2026-06-02"] == 4

    def test_empty(self):
        assert _daily_command_counts([]) == {}


# ── tests: _daily_commit_counts ─────────────────────────────────────


class TestDailyCommitCounts:
    def test_unique_commits(self):
        sessions = _sample_sessions()
        counts = _daily_commit_counts(sessions)
        assert counts["2026-06-01"] == 1
        assert counts["2026-06-02"] == 1
        assert counts["2026-06-03"] == 1

    def test_deduplication(self):
        """Same commit hash appearing in two sessions counts once."""
        c = {"hash": "same1234567", "timestamp": _ts(2026, 6, 1, 10), "message": "m", "cleaned_message": "m"}
        s1 = _make_session(_ts(2026, 6, 1, 9), 3600, commits=[c])
        s2 = _make_session(_ts(2026, 6, 1, 14), 3600, commits=[c])
        counts = _daily_commit_counts([s1, s2])
        assert counts["2026-06-01"] == 1


# ── tests: _error_rate ─────────────────────────────────────────────


class TestErrorRate:
    def test_zero_commands(self):
        assert _error_rate([]) == 0.0

    def test_all_pass(self):
        s = _make_session(_ts(2026, 6, 1, 9), 3600, commands=[
            _make_command(_ts(2026, 6, 1, 9), "echo hello", exit_code=0),
            _make_command(_ts(2026, 6, 1, 9, 1), "ls", exit_code=0),
        ])
        assert _error_rate([s]) == 0.0

    def test_half_errors(self):
        s = _make_session(_ts(2026, 6, 1, 9), 3600, commands=[
            _make_command(_ts(2026, 6, 1, 9), "cmd1", exit_code=0),
            _make_command(_ts(2026, 6, 1, 9, 1), "cmd2", exit_code=1),
            _make_command(_ts(2026, 6, 1, 9, 2), "cmd3", exit_code=0),
            _make_command(_ts(2026, 6, 1, 9, 3), "cmd4", exit_code=2),
        ])
        assert _error_rate([s]) == 50.0


# ── tests: _longest_streak_within_range ────────────────────────────


class TestLongestStreak:
    def test_empty(self):
        assert _longest_streak_within_range([]) == 0

    def test_single_day(self):
        s = _make_session(_ts(2026, 6, 1, 9), 3600)
        assert _longest_streak_within_range([s]) == 1

    def test_three_day_streak(self):
        sessions = [
            _make_session(_ts(2026, 6, 1, 9), 3600),
            _make_session(_ts(2026, 6, 2, 9), 3600),
            _make_session(_ts(2026, 6, 3, 9), 3600),
        ]
        assert _longest_streak_within_range(sessions) == 3

    def test_gap_breaks_streak(self):
        sessions = [
            _make_session(_ts(2026, 6, 1, 9), 3600),
            _make_session(_ts(2026, 6, 2, 9), 3600),
            _make_session(_ts(2026, 6, 5, 9), 3600),  # gap
            _make_session(_ts(2026, 6, 6, 9), 3600),
        ]
        assert _longest_streak_within_range(sessions) == 2


# ── tests: _project_time_distribution ───────────────────────────────


class TestProjectTimeDistribution:
    def test_sorted_by_time(self):
        sessions = _sample_sessions()
        project_names = {1: "termstory", 2: "frontend-app", 3: "rust-utils"}
        dist = _project_time_distribution(sessions, project_names)
        # termstory: 3600+7200+3600+14400 = 28800
        # frontend-app: 3600
        # rust-utils: 7200
        assert dist[0][0] == "termstory"
        assert dist[0][1] == 28800
        assert dist[1][0] == "rust-utils"
        assert dist[2][0] == "frontend-app"

    def test_unknown_project_becomes_other(self):
        s = _make_session(_ts(2026, 6, 1, 9), 3600, project_id=99)
        dist = _project_time_distribution([s], {1: "known"})
        assert dist[0][0] == "Other"


# ── tests: _category_frequency ──────────────────────────────────────


class TestCategoryFrequency:
    def test_sorted_by_count(self):
        s = _make_session(_ts(2026, 6, 1, 9), 3600, commands=[
            _make_command(_ts(2026, 6, 1, 9), "git push"),
            _make_command(_ts(2026, 6, 1, 9, 1), "git commit -m 'x'"),
            _make_command(_ts(2026, 6, 1, 9, 2), "docker ps"),
        ])
        freq = _category_frequency([s])
        assert freq[0][0] == "Git"
        assert freq[0][1] == 2
        assert freq[1][0] == "Docker"
        assert freq[1][1] == 1


# ── tests: _time_of_day_split ──────────────────────────────────────


class TestTimeOfDaySplit:
    def test_morning_and_night(self):
        s1 = _make_session(_ts(2026, 6, 1, 8), 3600)    # morning
        s2 = _make_session(_ts(2026, 6, 1, 23), 3600)    # night
        result = _time_of_day_split([s1, s2])
        assert result["morning"] == 3600
        assert result["night"] == 3600
        assert result["afternoon"] == 0
        assert result["evening"] == 0


# ── tests: _week_over_week_delta ────────────────────────────────────


class TestWeekOverWeekDelta:
    def test_improvement(self):
        current = {"total_time": 20000, "total_commits": 10, "total_sessions": 8, "focus_score": 7.0}
        previous = {"total_time": 10000, "total_commits": 5, "total_sessions": 5, "focus_score": 5.0}
        delta = _week_over_week_delta(current, previous)
        assert delta["time_delta"] == 10000
        assert delta["time_pct"] == 100.0
        assert delta["commit_delta"] == 5
        assert delta["commit_pct"] == 100.0
        assert delta["session_delta"] == 3
        assert delta["focus_delta"] == 2.0

    def test_decline(self):
        current = {"total_time": 5000, "total_commits": 2, "total_sessions": 3, "focus_score": 4.0}
        previous = {"total_time": 15000, "total_commits": 8, "total_sessions": 6, "focus_score": 6.0}
        delta = _week_over_week_delta(current, previous)
        assert delta["time_delta"] == -10000
        assert delta["time_pct"] < 0
        assert delta["focus_delta"] == -2.0

    def test_previous_zero(self):
        current = {"total_time": 5000, "total_commits": 0, "total_sessions": 3, "focus_score": 5.0}
        previous = {"total_time": 0, "total_commits": 0, "total_sessions": 0, "focus_score": 0.0}
        delta = _week_over_week_delta(current, previous)
        assert delta["time_pct"] is None
        assert delta["commit_pct"] is None


# ── tests: _top_achievements ────────────────────────────────────────


class TestTopAchievements:
    def test_commits_preferred(self):
        c = {
            "hash": "abc1234567890",
            "timestamp": _ts(2026, 6, 1, 10),
            "message": "feat: add login",
            "cleaned_message": "feat: add login",
        }
        s = _make_session(_ts(2026, 6, 1, 9), 3600, commands=[
            _make_command(_ts(2026, 6, 1, 9), "git push origin main"),
        ], commits=[c])
        ach = _top_achievements([s], limit=3)
        assert ach[0]["type"] == "commit"
        assert ach[0]["hash"] == "abc1234"

    def test_limit(self):
        sessions = []
        for i in range(10):
            sessions.append(_make_session(_ts(2026, 6, 1, 9, i * 10), 300, commits=[
                {"hash": f"hash{i:010d}", "timestamp": _ts(2026, 6, 1, 9, i * 10), "message": f"commit {i}", "cleaned_message": f"commit {i}"},
            ]))
        ach = _top_achievements(sessions, limit=3)
        assert len(ach) <= 3


# ── tests: _daily_focus_scores ─────────────────────────────────────


class TestDailyFocusScores:
    def test_single_session_day(self):
        s = _make_session(_ts(2026, 6, 1, 9), 7200, project_id=1)
        scores = _daily_focus_scores([s])
        assert "2026-06-01" in scores
        assert 0.0 <= scores["2026-06-01"] <= 10.0

    def test_empty(self):
        assert _daily_focus_scores([]) == {}


# ── tests: build_weekly_digest (integration) ────────────────────────


class TestBuildWeeklyDigest:
    def test_basic_structure(self):
        sessions = _sample_sessions()
        projects = _sample_projects()
        project_names = {1: "termstory", 2: "frontend-app", 3: "rust-utils"}

        digest = build_weekly_digest(sessions, projects, project_names)

        assert digest["total_time"] == 28800 + 3600 + 7200  # 39600
        assert digest["total_sessions"] == 6
        assert digest["total_commands"] == 9
        assert digest["total_commits"] == 3
        assert digest["active_days"] == 5  # Mon, Tue, Wed, Fri, Sat
        assert digest["streak"] == 3  # Mon, Tue, Wed
        assert "daily_totals" in digest
        assert "project_distribution" in digest
        assert "category_frequency" in digest
        assert "time_of_day" in digest
        assert "top_achievements" in digest

    def test_with_previous_week(self):
        sessions = _sample_sessions()
        projects = _sample_projects()
        project_names = {1: "termstory", 2: "frontend-app", 3: "rust-utils"}

        # Previous week: 2 sessions, less time
        prev_sessions = [
            _make_session(_ts(2026, 5, 25, 9), 3600, project_id=1),
            _make_session(_ts(2026, 5, 27, 9), 3600, project_id=1),
        ]

        digest = build_weekly_digest(
            sessions, projects, project_names, previous_sessions=prev_sessions
        )
        comp = digest["comparison"]
        assert comp is not None
        assert comp["time_delta"] > 0  # This week has more time
        assert comp["commit_delta"] >= 0

    def test_without_previous_week(self):
        sessions = _sample_sessions()
        projects = _sample_projects()
        project_names = {1: "termstory", 2: "frontend-app", 3: "rust-utils"}

        digest = build_weekly_digest(sessions, projects, project_names)
        assert digest["comparison"] is None

    def test_empty_week(self):
        digest = build_weekly_digest([], [], {})
        assert digest["total_time"] == 0
        assert digest["total_sessions"] == 0
        assert digest["total_commands"] == 0
        assert digest["total_commits"] == 0
        assert digest["active_days"] == 0

    def test_error_rate_populated(self):
        sessions = [
            _make_session(_ts(2026, 6, 1, 9), 3600, commands=[
                _make_command(_ts(2026, 6, 1, 9), "cmd1", exit_code=0),
                _make_command(_ts(2026, 6, 1, 9, 1), "cmd2", exit_code=1),
                _make_command(_ts(2026, 6, 1, 9, 2), "cmd3", exit_code=0),
            ]),
        ]
        digest = build_weekly_digest(sessions, [], {})
        assert digest["error_rate"] == pytest.approx(33.3, abs=0.1)


# ── tests: format_weekly_digest_output ──────────────────────────────


class TestFormatWeeklyDigestOutput:
    """Smoke tests that the formatter does not crash and contains key sections."""

    def test_empty_week(self):
        from termstory.formatter import format_weekly_digest_output
        start_ts = _ts(2026, 6, 1)
        end_ts = _ts(2026, 6, 7, 23, 59)
        digest = build_weekly_digest([], [], {})
        output = format_weekly_digest_output(digest, start_ts, end_ts)
        assert "No sessions" in output

    def test_full_week_has_all_sections(self):
        from termstory.formatter import format_weekly_digest_output
        sessions = _sample_sessions()
        projects = _sample_projects()
        project_names = {1: "termstory", 2: "frontend-app", 3: "rust-utils"}

        digest = build_weekly_digest(sessions, projects, project_names)
        start_ts = _ts(2026, 6, 1)
        end_ts = _ts(2026, 6, 7, 23, 59)
        output = format_weekly_digest_output(digest, start_ts, end_ts)

        assert "Summary" in output
        assert "Daily Breakdown" in output
        assert "Project Focus" in output
        assert "Active Hours" in output
        assert "Top Command Categories" in output
        assert "Highlights" in output

    def test_with_comparison(self):
        from termstory.formatter import format_weekly_digest_output
        sessions = _sample_sessions()
        projects = _sample_projects()
        project_names = {1: "termstory", 2: "frontend-app", 3: "rust-utils"}
        prev_sessions = [
            _make_session(_ts(2026, 5, 25, 9), 3600, project_id=1),
        ]
        digest = build_weekly_digest(
            sessions, projects, project_names, previous_sessions=prev_sessions
        )
        start_ts = _ts(2026, 6, 1)
        end_ts = _ts(2026, 6, 7, 23, 59)
        output = format_weekly_digest_output(digest, start_ts, end_ts)
        assert "vs. Previous Week" in output


# ── tests: format_duration edge cases (regression guard) ────────────


class TestFormatDurationEdge:
    def test_zero(self):
        assert format_duration(0) == "0s"

    def test_seconds_only(self):
        assert format_duration(45) == "45s"

    def test_minutes_only(self):
        assert format_duration(120) == "2m"

    def test_hours_and_minutes(self):
        assert format_duration(3720) == "1h 2m"

    def test_days_format(self):
        assert format_duration(90000) == "1d 1h"
