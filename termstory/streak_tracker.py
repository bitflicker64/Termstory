"""Streak Tracker — detailed developer streak analytics for TermStory.

Computes daily/weekly/monthly coding streaks, streak history heatmap data,
milestone achievements, risk assessment, and per-project streak breakdown.

Design: pure-computation module — no I/O, no DB access. Takes lists of
Session objects and returns structured dicts for the formatter.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from termstory.models import Session, format_duration


# ── Constants ───────────────────────────────────────────────────────

MILESTONES = [
    (3, "🥉 Bronze Streaker"),
    (7, "🔥 One-Week Warrior"),
    (14, "⚡ Fortnight Fighter"),
    (21, "💎 Three-Week Titan"),
    (30, "🏆 Monthly Master"),
    (60, "🌟 Seasoned Sprinter"),
    (90, "👑 Quarter King"),
    (180, "🎖️ Half-Year Hero"),
    (365, "🏅 Year-Long Legend"),
]

TIME_BUCKETS = [
    ("early_morning", 5, 8),   # 5:00 – 8:00
    ("morning", 8, 12),        # 8:00 – 12:00
    ("afternoon", 12, 17),     # 12:00 – 17:00
    ("evening", 17, 21),       # 17:00 – 21:00
    ("night", 21, 5),          # 21:00 – 5:00 (wraps midnight)
]

BUCKET_LABELS = {
    "early_morning": "🌄 Early AM (5-8) ",
    "morning":       "☀️  Morning (8-12)",
    "afternoon":     "🌤️  Afternoon(12-5)",
    "evening":       "🌆 Evening (5-9) ",
    "night":         "🌙 Night   (9-5) ",
}


# ── Pure helpers (testable) ─────────────────────────────────────────

def _active_dates(sessions: List[Session]) -> set[date]:
    """Return the set of calendar dates on which the developer was active."""
    return {datetime.fromtimestamp(s.start_time).date() for s in sessions}


def _compute_streaks(active: set[date], ref_date: Optional[date] = None) -> Dict[str, Any]:
    """Compute current streak, longest streak, and full streak history.

    Parameters
    ----------
    active:
        Set of dates with at least one session.
    ref_date:
        Reference date (today). Defaults to the most recent active date.
    """
    if not active:
        return {
            "current": 0,
            "longest": 0,
            "longest_start": None,
            "longest_end": None,
            "all_streaks": [],
            "total_active_days": 0,
        }

    sorted_dates = sorted(active)
    ref = ref_date or sorted_dates[-1]

    # Current streak: count backwards from ref_date (allow gap of 1 day —
    # if "today" hasn't happened yet, yesterday counts as current).
    current = 0
    cursor = ref
    # Walk forward from most recent date if cursor isn't active
    if cursor not in active:
        # Check if the day before ref is active (grace period)
        prev = cursor - timedelta(days=1)
        if prev in active:
            cursor = prev
        else:
            current = 0
            cursor = None

    if cursor is not None:
        while cursor in active:
            current += 1
            cursor -= timedelta(days=1)

    # All streaks: contiguous runs of active dates
    all_streaks: List[Dict[str, Any]] = []
    streak_start = sorted_dates[0]
    streak_len = 1

    for i in range(1, len(sorted_dates)):
        if (sorted_dates[i] - sorted_dates[i - 1]).days == 1:
            streak_len += 1
        else:
            all_streaks.append({
                "start": streak_start,
                "end": sorted_dates[i - 1],
                "length": streak_len,
            })
            streak_start = sorted_dates[i]
            streak_len = 1
    all_streaks.append({
        "start": streak_start,
        "end": sorted_dates[-1],
        "length": streak_len,
    })

    longest = max(all_streaks, key=lambda s: s["length"])

    # Current streak window (start date)
    current_start = None
    if current > 0:
        current_start = ref - timedelta(days=current - 1)

    return {
        "current": current,
        "current_start": current_start,
        "longest": longest["length"],
        "longest_start": longest["start"],
        "longest_end": longest["end"],
        "all_streaks": all_streaks,
        "total_active_days": len(active),
    }


def _weekly_streaks(active: set[date]) -> Dict[str, Any]:
    """Compute consecutive weeks with activity (Mon–Sun weeks)."""
    if not active:
        return {"current": 0, "longest": 0, "total_weeks": 0}

    # Map each active date to its ISO week key
    weeks: set[tuple[int, int]] = set()
    for d in active:
        iso = d.isocalendar()
        weeks.add((iso[1], iso[0]))  # (week_number, year)

    sorted_weeks = sorted(weeks)
    total_weeks = len(sorted_weeks)

    # Current weekly streak
    # Find the most recent week and count backwards
    if not sorted_weeks:
        return {"current": 0, "longest": 0, "total_weeks": 0}

    most_recent = sorted_weeks[-1]
    current = 1
    for i in range(len(sorted_weeks) - 2, -1, -1):
        yr, wk = sorted_weeks[i]
        prev_yr, prev_wk = sorted_weeks[i + 1]
        # Check if consecutive: same year and wk+1, or year boundary
        if prev_yr == yr and prev_wk - wk == 1:
            current += 1
        elif prev_yr - yr == 1 and prev_wk == 1 and wk >= 52:
            current += 1
        else:
            break

    # Longest weekly streak
    longest = 1
    run = 1
    for i in range(1, len(sorted_weeks)):
        yr, wk = sorted_weeks[i]
        prev_yr, prev_wk = sorted_weeks[i - 1]
        if prev_yr == yr and wk - prev_wk == 1:
            run += 1
            longest = max(longest, run)
        elif yr - prev_yr == 1 and wk == 1 and prev_wk >= 52:
            run += 1
            longest = max(longest, run)
        else:
            run = 1

    return {"current": current, "longest": longest, "total_weeks": total_weeks}


def _monthly_streaks(active: set[date]) -> Dict[str, Any]:
    """Compute consecutive months with at least one active day."""
    if not active:
        return {"current": 0, "longest": 0, "total_months": 0}

    months: set[tuple[int, int]] = set()
    for d in active:
        months.add((d.year, d.month))

    sorted_months = sorted(months)
    total_months = len(sorted_months)

    # Current monthly streak
    current = 1
    for i in range(len(sorted_months) - 1, 0, -1):
        yr, mo = sorted_months[i - 1]
        next_yr, next_mo = sorted_months[i]
        if next_yr == yr and next_mo - mo == 1:
            current += 1
        elif next_yr - yr == 1 and next_mo == 1 and mo == 12:
            current += 1
        else:
            break

    # Longest monthly streak
    longest = 1
    run = 1
    for i in range(1, len(sorted_months)):
        yr, mo = sorted_months[i]
        prev_yr, prev_mo = sorted_months[i - 1]
        if yr == prev_yr and mo - prev_mo == 1:
            run += 1
            longest = max(longest, run)
        elif yr - prev_yr == 1 and mo == 1 and prev_mo == 12:
            run += 1
            longest = max(longest, run)
        else:
            run = 1

    return {"current": current, "longest": longest, "total_months": total_months}


def _heatmap_data(
    active: set[date], ref_date: Optional[date] = None, lookback: int = 90
) -> List[Dict[str, Any]]:
    """Build a list of daily activity records for the last N days.

    Each record: {"date", "active", "weekday", "week_num", "level"}.
    level: 0=inactive, 1=light (<2h), 2=moderate (2-4h), 3=heavy (4-8h), 4=intense (8h+)
    """
    ref = ref_date or date.today()
    records: List[Dict[str, Any]] = []
    for i in range(lookback - 1, -1, -1):
        d = ref - timedelta(days=i)
        is_active = d in active
        records.append({
            "date": d,
            "active": is_active,
            "weekday": d.strftime("%a"),
            "week_num": d.isocalendar()[1],
            "level": 0,  # will be updated if sessions are provided
        })
    return records


def _activity_levels(
    sessions: List[Session], heatmap: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Annotate heatmap records with activity intensity levels."""
    # Sum durations per date
    daily_dur: Dict[date, int] = defaultdict(int)
    for s in sessions:
        d = datetime.fromtimestamp(s.start_time).date()
        daily_dur[d] += s.duration_seconds

    for rec in heatmap:
        d = rec["date"]
        secs = daily_dur.get(d, 0)
        hours = secs / 3600
        if hours == 0:
            rec["level"] = 0
        elif hours < 2:
            rec["level"] = 1
        elif hours < 4:
            rec["level"] = 2
        elif hours < 8:
            rec["level"] = 3
        else:
            rec["level"] = 4
        rec["duration"] = secs

    return heatmap


def _peak_hours(sessions: List[Session]) -> List[Tuple[str, int]]:
    """Return time buckets sorted by total seconds DESC."""
    bucket_secs: Dict[str, int] = {name: 0 for name, _, _ in TIME_BUCKETS}
    for s in sessions:
        dt = datetime.fromtimestamp(s.start_time)
        h = dt.hour
        for name, start_h, end_h in TIME_BUCKETS:
            if start_h < end_h:
                if start_h <= h < end_h:
                    bucket_secs[name] += s.duration_seconds
                    break
            else:  # wraps midnight (night: 21-5)
                if h >= start_h or h < end_h:
                    bucket_secs[name] += s.duration_seconds
                    break
    return sorted(bucket_secs.items(), key=lambda x: x[1], reverse=True)


def _project_streak_breakdown(
    sessions: List[Session], project_names: Dict[int, str]
) -> List[Dict[str, Any]]:
    """Per-project streak info: longest streak and total active days."""
    by_project: Dict[int, List[date]] = defaultdict(list)
    for s in sessions:
        if s.project_id is not None:
            d = datetime.fromtimestamp(s.start_time).date()
            by_project[s.project_id].append(d)

    results = []
    for pid, dates in by_project.items():
        active = set(dates)
        streaks = _compute_streaks(active)
        name = project_names.get(pid, "Other")
        if not name or name == "General / No Project":
            name = "Other"
        results.append({
            "project_id": pid,
            "project_name": name,
            "active_days": len(active),
            "longest_streak": streaks["longest"],
            "current_streak": streaks["current"],
            "total_time": sum(
                s.duration_seconds for s in sessions if s.project_id == pid
            ),
        })

    results.sort(key=lambda r: r["total_time"], reverse=True)
    return results


def _streak_risk(
    active: set[date], ref_date: Optional[date] = None
) -> Dict[str, Any]:
    """Assess whether the current streak is at risk.

    Returns info about what the developer needs to do today to keep the streak.
    """
    ref = ref_date or date.today()
    streak_info = _compute_streaks(active, ref)
    current = streak_info["current"]

    if current == 0:
        return {
            "status": "broken",
            "message": "No active streak. Start coding today to begin a new one!",
            "days_since_last": (ref - max(active)).days if active else None,
            "needed": 1,
        }

    last_active = max(d for d in active if d <= ref)
    days_since = (ref - last_active).days

    if days_since == 0:
        # Active today — streak is safe
        return {
            "status": "safe",
            "message": f"Streak of {current} day(s) is safe! Already active today.",
            "days_since_last": 0,
            "needed": 0,
        }
    elif days_since == 1:
        # Active yesterday but not today — at risk
        return {
            "status": "at_risk",
            "message": f"Streak of {current} day(s) at risk! Code today to keep it alive.",
            "days_since_last": 1,
            "needed": 1,
        }
    else:
        # Streak broken
        return {
            "status": "broken",
            "message": f"Streak of {current} day(s) broken. {days_since} day(s) since last activity.",
            "days_since_last": days_since,
            "needed": 1,
        }


def _earned_milestones(longest_streak: int) -> List[Dict[str, str]]:
    """Return milestones achieved based on longest streak."""
    earned = []
    for days, title in MILESTONES:
        if longest_streak >= days:
            earned.append({"days": days, "title": title})
    return earned


def _next_milestone(longest_streak: int) -> Optional[Dict[str, Any]]:
    """Return the next unearned milestone and days remaining."""
    for days, title in MILESTONES:
        if longest_streak < days:
            return {"days": days, "title": title, "remaining": days - longest_streak}
    return None  # All milestones earned


def _weekday_distribution(sessions: List[Session]) -> Dict[str, int]:
    """Total seconds per weekday (Mon=0 … Sun=6) for the given sessions."""
    dist: Dict[str, int] = defaultdict(int)
    for s in sessions:
        dow = datetime.fromtimestamp(s.start_time).strftime("%A")
        dist[dow] += s.duration_seconds
    return dict(dist)


# ── Main entry point ────────────────────────────────────────────────


def build_streak_report(
    sessions: List[Session],
    project_names: Dict[int, str],
    ref_date: Optional[date] = None,
    heatmap_lookback: int = 90,
) -> Dict[str, Any]:
    """Build a complete streak report dict from session data.

    Parameters
    ----------
    sessions:
        All sessions to analyse (typically last 365 days).
    project_names:
        Mapping of ``project_id → display_name``.
    ref_date:
        Reference date (today). Defaults to ``date.today()``.
    heatmap_lookback:
        Number of days of heatmap history to include (default 90).

    Returns
    -------
    dict
        Full streak report consumed by ``format_streak_output``.
    """
    active = _active_dates(sessions)
    ref = ref_date or date.today()

    streaks = _compute_streaks(active, ref)
    weekly = _weekly_streaks(active)
    monthly = _monthly_streaks(active)
    risk = _streak_risk(active, ref)
    heatmap = _heatmap_data(active, ref, heatmap_lookback)
    heatmap = _activity_levels(sessions, heatmap)
    peak = _peak_hours(sessions)
    project_streaks = _project_streak_breakdown(sessions, project_names)
    milestones = _earned_milestones(streaks["longest"])
    next_ms = _next_milestone(streaks["longest"])
    weekday_dist = _weekday_distribution(sessions)

    total_time = sum(s.duration_seconds for s in sessions)
    total_commands = sum(len(s.commands) for s in sessions)

    # Best weekday (most active)
    best_weekday = max(weekday_dist.items(), key=lambda x: x[1])[0] if weekday_dist else "N/A"

    return {
        "current_streak": streaks["current"],
        "current_start": streaks["current_start"],
        "longest_streak": streaks["longest"],
        "longest_start": streaks["longest_start"],
        "longest_end": streaks["longest_end"],
        "total_active_days": streaks["total_active_days"],
        "weekly_streak": weekly,
        "monthly_streak": monthly,
        "risk": risk,
        "heatmap": heatmap,
        "peak_hours": peak,
        "project_streaks": project_streaks,
        "milestones": milestones,
        "next_milestone": next_ms,
        "weekday_dist": weekday_dist,
        "best_weekday": best_weekday,
        "total_time": total_time,
        "total_commands": total_commands,
        "total_sessions": len(sessions),
    }
