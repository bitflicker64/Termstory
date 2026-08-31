"""Tests for the streak tracker module (termstory.streak_tracker).

Exercises all pure-computation helpers with hand-crafted Session objects.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import List, Optional

import pytest

from termstory.models import Command, Session
from termstory.streak_tracker import (
    _active_dates,
    _compute_streaks,
    _earned_milestones,
    _heatmap_data,
    _activity_levels,
    _monthly_streaks,
    _next_milestone,
    _peak_hours,
    _project_streak_breakdown,
    _streak_risk,
    _weekday_distribution,
    _weekly_streaks,
    build_streak_report,
)


# ── fixtures ────────────────────────────────────────────────────────

def _ts(y: int, m: int, d: int, h: int = 9, mi: int = 0) -> int:
    return int(datetime(y, m, d, h, mi).timestamp())


def _session(d: date, dur: int = 3600, project_id: Optional[int] = None, sid: int = 1) -> Session:
    ts = int(datetime(d.year, d.month, d.day, 9).timestamp())
    return Session(id=sid, start_time=ts, end_time=ts + dur, duration_seconds=dur, project_id=project_id)


def _cmd(d: date, h: int, text: str = "git status", exit_code: int = 0, pid: Optional[int] = None) -> Command:
    return Command(timestamp=_ts(d.year, d.month, d.day, h), command=text, exit_code=exit_code, project_id=pid)


# ── tests: _active_dates ────────────────────────────────────────────


class TestActiveDates:
    def test_basic(self):
        s1 = _session(date(2026, 6, 1))
        s2 = _session(date(2026, 6, 1), sid=2)
        s3 = _session(date(2026, 6, 3), sid=3)
        active = _active_dates([s1, s2, s3])
        assert active == {date(2026, 6, 1), date(2026, 6, 3)}

    def test_empty(self):
        assert _active_dates([]) == set()


# ── tests: _compute_streaks ────────────────────────────────────────


class TestComputeStreaks:
    def test_empty(self):
        result = _compute_streaks(set())
        assert result["current"] == 0
        assert result["longest"] == 0

    def test_single_day(self):
        result = _compute_streaks({date(2026, 6, 1)}, ref_date=date(2026, 6, 1))
        assert result["current"] == 1
        assert result["longest"] == 1

    def test_5_day_streak(self):
        active = {date(2026, 6, 1) + timedelta(days=i) for i in range(5)}
        result = _compute_streaks(active, ref_date=date(2026, 6, 5))
        assert result["current"] == 5
        assert result["longest"] == 5

    def test_broken_streak(self):
        active = {date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 5)}
        result = _compute_streaks(active, ref_date=date(2026, 6, 5))
        assert result["current"] == 1  # only 5th
        assert result["longest"] == 2   # 1-2

    def test_grace_period_yesterday(self):
        """If ref_date is not active but yesterday is, current streak includes yesterday."""
        active = {date(2026, 6, 1), date(2026, 6, 2)}
        result = _compute_streaks(active, ref_date=date(2026, 6, 3))
        assert result["current"] == 2

    def test_no_grace_two_days_gap(self):
        active = {date(2026, 6, 1), date(2026, 6, 2)}
        result = _compute_streaks(active, ref_date=date(2026, 6, 4))
        assert result["current"] == 0

    def test_longest_not_current(self):
        """Longest streak is independent of current."""
        active = {
            date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3), date(2026, 1, 4),
            date(2026, 6, 5),
        }
        result = _compute_streaks(active, ref_date=date(2026, 6, 5))
        assert result["longest"] == 4
        assert result["current"] == 1

    def test_all_streaks_populated(self):
        active = {date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 5)}
        result = _compute_streaks(active, ref_date=date(2026, 6, 5))
        assert len(result["all_streaks"]) == 2

    def test_total_active_days(self):
        active = {date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 5)}
        result = _compute_streaks(active)
        assert result["total_active_days"] == 3


# ── tests: _weekly_streaks ─────────────────────────────────────────


class TestWeeklyStreaks:
    def test_empty(self):
        result = _weekly_streaks(set())
        assert result["current"] == 0

    def test_single_week(self):
        active = {date(2026, 6, 1), date(2026, 6, 3)}  # Mon, Wed same ISO week
        result = _weekly_streaks(active)
        assert result["current"] == 1
        assert result["total_weeks"] == 1

    def test_two_consecutive_weeks(self):
        # Week 22: June 1 (Mon) + Week 23: June 8 (Mon)
        active = {date(2026, 6, 1), date(2026, 6, 8)}
        result = _weekly_streaks(active)
        assert result["current"] == 2

    def test_gap_breaks_weekly(self):
        active = {date(2026, 6, 1), date(2026, 6, 15)}  # gap of ~2 weeks
        result = _weekly_streaks(active)
        assert result["current"] == 1
        assert result["total_weeks"] == 2


# ── tests: _monthly_streaks ────────────────────────────────────────


class TestMonthlyStreaks:
    def test_empty(self):
        result = _monthly_streaks(set())
        assert result["current"] == 0

    def test_three_consecutive_months(self):
        active = {date(2026, 4, 1), date(2026, 5, 1), date(2026, 6, 1)}
        result = _monthly_streaks(active)
        assert result["current"] == 3
        assert result["longest"] == 3
        assert result["total_months"] == 3

    def test_gap_breaks_monthly(self):
        active = {date(2026, 1, 1), date(2026, 3, 1)}  # Feb missing
        result = _monthly_streaks(active)
        assert result["current"] == 1

    def test_year_boundary(self):
        active = {date(2025, 12, 15), date(2026, 1, 5)}
        result = _monthly_streaks(active)
        assert result["current"] == 2


# ── tests: _heatmap_data ───────────────────────────────────────────


class TestHeatmapData:
    def test_length(self):
        active = {date(2026, 6, 1)}
        records = _heatmap_data(active, ref_date=date(2026, 6, 30), lookback=7)
        assert len(records) == 7

    def test_active_flag(self):
        active = {date(2026, 6, 28)}
        records = _heatmap_data(active, ref_date=date(2026, 6, 30), lookback=7)
        active_recs = [r for r in records if r["active"]]
        assert len(active_recs) == 1
        assert active_recs[0]["date"] == date(2026, 6, 28)


# ── tests: _activity_levels ────────────────────────────────────────


class TestActivityLevels:
    def test_sets_levels(self):
        s = _session(date(2026, 6, 1), dur=10800)  # 3 hours → level 2
        heatmap = [{"date": date(2026, 6, 1), "active": True, "weekday": "Mon", "week_num": 22, "level": 0}]
        result = _activity_levels([s], heatmap)
        assert result[0]["level"] == 2
        assert result[0]["duration"] == 10800


# ── tests: _peak_hours ─────────────────────────────────────────────


class TestPeakHours:
    def test_morning_dominant(self):
        s1 = _session(date(2026, 6, 1), dur=7200)
        # Override start_time to morning
        s1.start_time = _ts(2026, 6, 1, 10)
        s1.end_time = s1.start_time + 7200
        peak = _peak_hours([s1])
        assert peak[0][0] == "morning"

    def test_empty(self):
        peak = _peak_hours([])
        assert all(v == 0 for _, v in peak)


# ── tests: _project_streak_breakdown ───────────────────────────────


class TestProjectStreakBreakdown:
    def test_basic(self):
        s1 = _session(date(2026, 6, 1), project_id=1)
        s2 = _session(date(2026, 6, 2), project_id=1)
        s3 = _session(date(2026, 6, 1), project_id=2, sid=2)
        result = _project_streak_breakdown([s1, s2, s3], {1: "proj-a", 2: "proj-b"})
        assert len(result) == 2
        # proj-a: 2 consecutive days
        proj_a = next(r for r in result if r["project_name"] == "proj-a")
        assert proj_a["longest_streak"] == 2
        assert proj_a["active_days"] == 2

    def test_unknown_project(self):
        s = _session(date(2026, 6, 1), project_id=99)
        result = _project_streak_breakdown([s], {})
        assert result[0]["project_name"] == "Other"


# ── tests: _streak_risk ────────────────────────────────────────────


class TestStreakRisk:
    def test_safe_when_active_today(self):
        active = {date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 3)}
        risk = _streak_risk(active, ref_date=date(2026, 6, 3))
        assert risk["status"] == "safe"

    def test_at_risk_when_not_active_today(self):
        active = {date(2026, 6, 1), date(2026, 6, 2)}
        risk = _streak_risk(active, ref_date=date(2026, 6, 3))
        assert risk["status"] == "at_risk"

    def test_broken_when_gap(self):
        active = {date(2026, 6, 1), date(2026, 6, 2)}
        risk = _streak_risk(active, ref_date=date(2026, 6, 5))
        assert risk["status"] == "broken"

    def test_broken_when_empty(self):
        risk = _streak_risk(set(), ref_date=date(2026, 6, 1))
        assert risk["status"] == "broken"


# ── tests: _earned_milestones ──────────────────────────────────────


class TestEarnedMilestones:
    def test_none_earned(self):
        assert _earned_milestones(0) == []

    def test_bronze_only(self):
        ms = _earned_milestones(5)
        assert len(ms) == 1
        assert ms[0]["days"] == 3

    def test_multiple(self):
        ms = _earned_milestones(25)
        titles = [m["title"] for m in ms]
        assert "🥉 Bronze Streaker" in titles
        assert "🔥 One-Week Warrior" in titles
        assert "⚡ Fortnight Fighter" in titles
        assert "💎 Three-Week Titan" not in titles


# ── tests: _next_milestone ─────────────────────────────────────────


class TestNextMilestone:
    def test_first_milestone(self):
        result = _next_milestone(0)
        assert result is not None
        assert result["days"] == 3
        assert result["remaining"] == 3

    def test_all_earned(self):
        result = _next_milestone(400)
        assert result is None


# ── tests: _weekday_distribution ───────────────────────────────────


class TestWeekdayDistribution:
    def test_basic(self):
        s1 = _session(date(2026, 6, 1), dur=3600)  # Monday
        s2 = _session(date(2026, 6, 2), dur=7200)  # Tuesday
        dist = _weekday_distribution([s1, s2])
        assert dist["Monday"] == 3600
        assert dist["Tuesday"] == 7200

    def test_empty(self):
        assert _weekday_distribution([]) == {}


# ── tests: build_streak_report (integration) ────────────────────────


class TestBuildStreakReport:
    def test_basic_structure(self):
        sessions = [
            _session(date(2026, 6, 1)),
            _session(date(2026, 6, 2)),
            _session(date(2026, 6, 3)),
        ]
        report = build_streak_report(sessions, {1: "proj"}, ref_date=date(2026, 6, 3))
        assert report["current_streak"] == 3
        assert report["longest_streak"] == 3
        assert report["total_active_days"] == 3
        assert report["total_sessions"] == 3
        assert "heatmap" in report
        assert "peak_hours" in report
        assert "project_streaks" in report
        assert "milestones" in report
        assert "risk" in report
        assert "weekday_dist" in report

    def test_empty_sessions(self):
        report = build_streak_report([], {}, ref_date=date(2026, 6, 1))
        assert report["current_streak"] == 0
        assert report["longest_streak"] == 0
        assert report["total_sessions"] == 0

    def test_milestones_populated(self):
        sessions = [_session(date(2026, 6, 1) + timedelta(days=i)) for i in range(10)]
        report = build_streak_report(sessions, {}, ref_date=date(2026, 6, 10))
        assert len(report["milestones"]) >= 2  # bronze + one-week
        assert report["next_milestone"] is not None
        assert report["next_milestone"]["days"] == 14

    def test_all_milestones(self):
        sessions = [_session(date(2026, 1, 1) + timedelta(days=i)) for i in range(400)]
        report = build_streak_report(sessions, {}, ref_date=date(2026, 1, 1) + timedelta(days=399))
        assert report["next_milestone"] is None  # all earned

    def test_project_streaks(self):
        s1 = _session(date(2026, 6, 1), project_id=1)
        s2 = _session(date(2026, 6, 2), project_id=1)
        s3 = _session(date(2026, 6, 1), project_id=2, sid=2)
        report = build_streak_report([s1, s2, s3], {1: "alpha", 2: "beta"}, ref_date=date(2026, 6, 2))
        assert len(report["project_streaks"]) == 2
        # alpha has longest project streak
        alpha = next(p for p in report["project_streaks"] if p["project_name"] == "alpha")
        assert alpha["longest_streak"] == 2

    def test_heatmap_length(self):
        sessions = [_session(date(2026, 6, 1))]
        report = build_streak_report(sessions, {}, ref_date=date(2026, 6, 30), heatmap_lookback=14)
        assert len(report["heatmap"]) == 14

    def test_risk_status(self):
        # Active today
        sessions = [_session(date(2026, 6, 3))]
        report = build_streak_report(sessions, {}, ref_date=date(2026, 6, 3))
        assert report["risk"]["status"] == "safe"

    def test_best_weekday(self):
        sessions = [
            _session(date(2026, 6, 1), dur=7200),  # Monday 2h
            _session(date(2026, 6, 2), dur=3600),  # Tuesday 1h
            _session(date(2026, 6, 3), dur=7200),  # Wednesday 2h
        ]
        report = build_streak_report(sessions, {}, ref_date=date(2026, 6, 3))
        assert report["best_weekday"] in ("Monday", "Wednesday")


# ── tests: format_streak_output (smoke) ─────────────────────────────


class TestFormatStreakOutput:
    def test_empty_report(self):
        from termstory.formatter import format_streak_output
        report = build_streak_report([], {}, ref_date=date(2026, 6, 1))
        output = format_streak_output(report)
        assert "Developer Streak Tracker" in output

    def test_full_report_has_sections(self):
        from termstory.formatter import format_streak_output
        sessions = [_session(date(2026, 6, 1) + timedelta(days=i)) for i in range(7)]
        report = build_streak_report(sessions, {1: "proj"}, ref_date=date(2026, 6, 7))
        output = format_streak_output(report)
        assert "Streak Status" in output
        assert "Streak Calendar" in output
        assert "Activity Heatmap" in output
        assert "Peak Hours" in output
        assert "Weekday Distribution" in output
        assert "Milestones" in output
        assert "Overall Stats" in output
