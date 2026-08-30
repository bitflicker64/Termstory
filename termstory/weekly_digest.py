"""Weekly Digest — comprehensive weekly analytics for TermStory.

Collects per-day session breakdown, project time distribution, commit
velocity, command-category frequency, focus-score trend, week-over-week
comparison, and top-achievement highlights into a single structured dict
that the formatter renders for the ``termstory weekly`` CLI command.

Design notes
------------
* Pure-computation module: no I/O, no formatting.  The formatter owns
  all Rich rendering so this module stays testable.
* Operates on raw SQLite rows via the Database helper methods (same
  pattern as ``insights.py``).
* All public helpers accept plain data, not DB connections, so unit tests
  can exercise them with hand-crafted inputs.
"""

from __future__ import annotations

import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from termstory.formatter import classify_command, DISPLAY_NAMES, _is_noise_command
from termstory.models import Command, Project, Session, format_duration


# ── helpers (public, testable) ──────────────────────────────────────


def _classify_command_category(cmd_text: str) -> str:
    """Return a human-friendly display name for a command category."""
    cat = classify_command(cmd_text)
    return DISPLAY_NAMES.get(cat, cat.capitalize())


def _daily_session_totals(sessions: List[Session]) -> Dict[str, int]:
    """Map ``YYYY-MM-DD`` → total seconds across all sessions on that day."""
    totals: Dict[str, int] = defaultdict(int)
    for s in sessions:
        day = datetime.fromtimestamp(s.start_time).strftime("%Y-%m-%d")
        totals[day] += s.duration_seconds
    return dict(totals)


def _daily_session_counts(sessions: List[Session]) -> Dict[str, int]:
    """Map ``YYYY-MM-DD`` → number of sessions on that day."""
    counts: Dict[str, int] = defaultdict(int)
    for s in sessions:
        day = datetime.fromtimestamp(s.start_time).strftime("%Y-%m-%d")
        counts[day] += 1
    return dict(counts)


def _daily_command_counts(sessions: List[Session]) -> Dict[str, int]:
    """Map ``YYYY-MM-DD`` → number of commands executed on that day."""
    counts: Dict[str, int] = defaultdict(int)
    for s in sessions:
        day = datetime.fromtimestamp(s.start_time).strftime("%Y-%m-%d")
        counts[day] += len(s.commands)
    return dict(counts)


def _project_time_distribution(
    sessions: List[Session], project_names: Dict[int, str]
) -> List[Tuple[str, int]]:
    """Return ``[(project_name, seconds), ...]`` sorted by duration DESC."""
    times: Dict[str, int] = defaultdict(int)
    for s in sessions:
        name = project_names.get(s.project_id, "Other")
        if not name or name == "General / No Project":
            name = "Other"
        times[name] += s.duration_seconds
    return sorted(times.items(), key=lambda x: x[1], reverse=True)


def _category_frequency(sessions: List[Session]) -> List[Tuple[str, int]]:
    """Return ``[(display_name, count), ...]`` sorted by frequency DESC."""
    counts: Counter[str] = Counter()
    for s in sessions:
        for cmd in s.commands:
            counts[_classify_command_category(cmd.command)] += 1
    return counts.most_common()


def _daily_commit_counts(sessions: List[Session]) -> Dict[str, int]:
    """Map ``YYYY-MM-DD`` → unique-commits count on that day."""
    seen_hashes: set = set()
    counts: Dict[str, int] = defaultdict(int)
    for s in sessions:
        for c in s.commits:
            h = c.get("hash")
            if h and h not in seen_hashes:
                seen_hashes.add(h)
                day = datetime.fromtimestamp(c["timestamp"]).strftime("%Y-%m-%d")
                counts[day] += 1
    return dict(counts)


def _error_rate(sessions: List[Session]) -> float:
    """Return the command failure rate as a percentage (0-100)."""
    total = 0
    errors = 0
    for s in sessions:
        for cmd in s.commands:
            total += 1
            if cmd.exit_code != 0:
                errors += 1
    return round((errors / total) * 100, 1) if total > 0 else 0.0


def _longest_streak_within_range(sessions: List[Session]) -> int:
    """Consecutive active work-days within the given session list."""
    if not sessions:
        return 0
    active_dates = sorted(
        {datetime.fromtimestamp(s.start_time).date() for s in sessions}
    )
    if not active_dates:
        return 0
    streak = 1
    best = 1
    for i in range(1, len(active_dates)):
        if (active_dates[i] - active_dates[i - 1]).days == 1:
            streak += 1
            best = max(best, streak)
        else:
            streak = 1
    return best


def _time_of_day_split(sessions: List[Session]) -> Dict[str, int]:
    """Distribute total seconds into morning/afternoon/evening/night."""
    buckets = {"morning": 0, "afternoon": 0, "evening": 0, "night": 0}
    for s in sessions:
        dt = datetime.fromtimestamp(s.start_time)
        h = dt.hour
        dur = s.duration_seconds
        if 6 <= h < 12:
            buckets["morning"] += dur
        elif 12 <= h < 18:
            buckets["afternoon"] += dur
        elif 18 <= h < 22:
            buckets["evening"] += dur
        else:
            buckets["night"] += dur
    return buckets


def _week_over_week_delta(
    current: Dict[str, Any], previous: Dict[str, Any]
) -> Dict[str, Any]:
    """Compute deltas between two weekly summaries for comparison display."""
    ct = current.get("total_time", 0)
    pt = previous.get("total_time", 0)
    time_delta = ct - pt
    time_pct = round((time_delta / pt) * 100, 1) if pt > 0 else None

    cc = current.get("total_commits", 0)
    pc = previous.get("total_commits", 0)
    commit_delta = cc - pc
    commit_pct = round((commit_delta / pc) * 100, 1) if pc > 0 else None

    cs = current.get("total_sessions", 0)
    ps = previous.get("total_sessions", 0)
    session_delta = cs - ps

    ef = current.get("focus_score", 0)
    pf = previous.get("focus_score", 0)
    focus_delta = round(ef - pf, 1)

    return {
        "time_delta": time_delta,
        "time_pct": time_pct,
        "commit_delta": commit_delta,
        "commit_pct": commit_pct,
        "session_delta": session_delta,
        "focus_delta": focus_delta,
    }


def _daily_focus_scores(sessions: List[Session]) -> Dict[str, float]:
    """Per-day focus score (mirrors ``insights.calculate_focus_score`` logic)."""
    by_day: Dict[str, List[Session]] = defaultdict(list)
    for s in sessions:
        day = datetime.fromtimestamp(s.start_time).strftime("%Y-%m-%d")
        by_day[day].append(s)

    scores: Dict[str, float] = {}
    for day, day_sessions in by_day.items():
        projects_by_session = defaultdict(set)
        total_dur = 0
        for s in day_sessions:
            projects_by_session[s.start_time].add(s.project_id)
            total_dur += s.duration_seconds
        if not day_sessions:
            scores[day] = 0.0
            continue
        avg_projects = sum(len(p) for p in projects_by_session.values()) / len(day_sessions)
        avg_mins = (total_dur / len(day_sessions)) / 60.0
        score = 6.0
        score -= max(0.0, (avg_projects - 1.0) * 1.5)
        score += min(4.0, avg_mins / 20.0)
        scores[day] = round(max(0.0, min(10.0, score)), 1)
    return scores


def _top_achievements(sessions: List[Session], limit: int = 5) -> List[Dict[str, Any]]:
    """Return the most meaningful commits and commands as weekly highlights."""
    achievements: List[Dict[str, Any]] = []

    seen_hashes: set = set()
    for s in sessions:
        for c in s.commits:
            h = c.get("hash")
            if h and h not in seen_hashes:
                seen_hashes.add(h)
                msg = c.get("cleaned_message") or c.get("message", "")
                achievements.append({
                    "type": "commit",
                    "description": msg,
                    "hash": h[:7],
                    "timestamp": c["timestamp"],
                    "project_id": s.project_id,
                })

    for s in sessions:
        for cmd in s.commands:
            if not _is_noise_command(cmd.command) and cmd.exit_code == 0:
                text = cmd.command.strip()
                # Keep only commands that look meaningful (longer than 10 chars)
                if len(text) > 10:
                    achievements.append({
                        "type": "command",
                        "description": text[:120],
                        "hash": None,
                        "timestamp": cmd.timestamp,
                        "project_id": cmd.project_id,
                    })

    achievements.sort(key=lambda a: a["timestamp"], reverse=True)
    return achievements[:limit]


# ── main entry point ────────────────────────────────────────────────


def build_weekly_digest(
    sessions: List[Session],
    projects: List[Project],
    project_names: Dict[int, str],
    previous_sessions: Optional[List[Session]] = None,
) -> Dict[str, Any]:
    """Build a complete weekly digest dict from session data.

    Parameters
    ----------
    sessions:
        Sessions belonging to the **current** week.
    projects:
        All projects (for reference).
    project_names:
        Mapping of ``project_id → display_name``.
    previous_sessions:
        Optional sessions from the **previous** week for comparison.

    Returns
    -------
    dict
        A rich structure consumed by ``format_weekly_digest_output``.
    """
    total_time = sum(s.duration_seconds for s in sessions)
    total_commands = sum(len(s.commands) for s in sessions)

    # Unique commits
    seen_hashes: set = set()
    for s in sessions:
        for c in s.commits:
            h = c.get("hash")
            if h:
                seen_hashes.add(h)
    total_commits = len(seen_hashes)

    active_days = len({datetime.fromtimestamp(s.start_time).date() for s in sessions})

    digest: Dict[str, Any] = {
        "total_time": total_time,
        "total_sessions": len(sessions),
        "total_commands": total_commands,
        "total_commits": total_commits,
        "active_days": active_days,
        "error_rate": _error_rate(sessions),
        "streak": _longest_streak_within_range(sessions),
        "focus_score": round(
            sum(
                _daily_focus_scores(sessions).values()
            ) / max(1, len(_daily_focus_scores(sessions))),
            1,
        ),
        "daily_totals": _daily_session_totals(sessions),
        "daily_session_counts": _daily_session_counts(sessions),
        "daily_command_counts": _daily_command_counts(sessions),
        "daily_commit_counts": _daily_commit_counts(sessions),
        "daily_focus_scores": _daily_focus_scores(sessions),
        "project_distribution": _project_time_distribution(sessions, project_names),
        "category_frequency": _category_frequency(sessions),
        "time_of_day": _time_of_day_split(sessions),
        "top_achievements": _top_achievements(sessions, limit=7),
    }

    # Week-over-week comparison
    if previous_sessions is not None:
        prev_total_time = sum(s.duration_seconds for s in previous_sessions)
        prev_seen: set = set()
        for s in previous_sessions:
            for c in s.commits:
                h = c.get("hash")
                if h:
                    prev_seen.add(h)
        prev_summary = {
            "total_time": prev_total_time,
            "total_commits": len(prev_seen),
            "total_sessions": len(previous_sessions),
            "focus_score": round(
                sum(_daily_focus_scores(previous_sessions).values())
                / max(1, len(_daily_focus_scores(previous_sessions))),
                1,
            ) if previous_sessions else 0.0,
        }
        digest["comparison"] = _week_over_week_delta(digest, prev_summary)
    else:
        digest["comparison"] = None

    return digest
