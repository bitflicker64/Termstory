"""Flow Analyzer — deep work detection, distraction tracking, and productivity metrics for TermStory.

Detects flow states (sustained uninterrupted work blocks), identifies
distraction patterns (context switches, noise commands, frequent pauses),
and computes productivity scores per session and per day.

Design: pure-computation module — no I/O, no DB access.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from termstory.formatter import classify_command, _is_noise_command
from termstory.models import Session, Command, format_duration


# ── Constants ───────────────────────────────────────────────────────

# Minimum seconds of uninterrupted command activity to qualify as a "flow block"
FLOW_BLOCK_MIN_SECONDS = 600  # 10 minutes

# Maximum gap (seconds) between commands before a flow block is considered broken
FLOW_GAP_TOLERANCE = 300  # 5 minutes

# Productivity tiers
PRODUCTIVITY_TIERS = [
    (80, "🚀 Hyperproductivity"),
    (60, "⚡ Flow State"),
    (40, "🔥 Solid Focus"),
    (20, "😴 Warming Up"),
    (0, "🌊 Drifting"),
]


# ── Helpers (public, testable) ──────────────────────────────────────


def _command_gaps(commands: List[Command]) -> List[int]:
    """Return list of gaps (seconds) between consecutive commands."""
    if len(commands) < 2:
        return []
    gaps = []
    for i in range(1, len(commands)):
        gap = commands[i].timestamp - commands[i - 1].timestamp
        gaps.append(gap)
    return gaps


def _detect_flow_blocks(
    commands: List[Command], min_seconds: int = FLOW_BLOCK_MIN_SECONDS,
    gap_tolerance: int = FLOW_GAP_TOLERANCE,
) -> List[Dict[str, Any]]:
    """Detect flow blocks — periods of sustained command activity.

    A flow block is a contiguous sequence of commands where:
    - No gap exceeds gap_tolerance
    - Total duration >= min_seconds
    """
    if not commands:
        return []

    sorted_cmds = sorted(commands, key=lambda c: c.timestamp)
    blocks: List[Dict[str, Any]] = []
    block_start = sorted_cmds[0].timestamp
    block_cmds = [sorted_cmds[0]]

    for i in range(1, len(sorted_cmds)):
        gap = sorted_cmds[i].timestamp - sorted_cmds[i - 1].timestamp
        if gap <= gap_tolerance:
            block_cmds.append(sorted_cmds[i])
        else:
            # End current block
            block_end = block_cmds[-1].timestamp
            block_dur = block_end - block_cmds[0].timestamp
            if block_dur >= min_seconds:
                blocks.append({
                    "start": block_cmds[0].timestamp,
                    "end": block_cmds[-1].timestamp,
                    "duration": block_dur,
                    "commands": len(block_cmds),
                    "avg_gap": int(sum(
                        block_cmds[j].timestamp - block_cmds[j - 1].timestamp
                        for j in range(1, len(block_cmds))
                    ) / max(1, len(block_cmds) - 1)),
                })
            block_start = sorted_cmds[i].timestamp
            block_cmds = [sorted_cmds[i]]

    # Final block
    if block_cmds:
        block_end = block_cmds[-1].timestamp
        block_dur = block_end - block_cmds[0].timestamp
        if block_dur >= min_seconds:
            blocks.append({
                "start": block_cmds[0].timestamp,
                "end": block_cmds[-1].timestamp,
                "duration": block_dur,
                "commands": len(block_cmds),
                "avg_gap": int(sum(
                    block_cmds[j].timestamp - block_cmds[j - 1].timestamp
                    for j in range(1, len(block_cmds))
                ) / max(1, len(block_cmds) - 1)),
            })

    return blocks


def _distraction_score(commands: List[Command]) -> float:
    """Score how distracting a session was (0 = no distractions, 100 = very distracting).

    Factors:
    - Noise command ratio
    - Exit error frequency
    - Context switches (distinct command categories)
    - Long gaps between commands
    """
    if not commands:
        return 0.0

    sorted_cmds = sorted(commands, key=lambda c: c.timestamp)
    total = len(sorted_cmds)

    # Noise ratio
    noise_count = sum(1 for c in sorted_cmds if _is_noise_command(c.command))
    noise_ratio = noise_count / total

    # Error ratio
    error_count = sum(1 for c in sorted_cmds if c.exit_code != 0)
    error_ratio = error_count / total

    # Context switch score: number of distinct categories / total
    categories = set()
    for c in sorted_cmds:
        categories.add(classify_command(c.command))
    switch_ratio = len(categories) / total if total > 0 else 0

    # Gap analysis: fraction of gaps > 2 minutes
    gaps = _command_gaps(sorted_cmds)
    long_gaps = sum(1 for g in gaps if g > 120)
    gap_ratio = long_gaps / len(gaps) if gaps else 0

    # Weighted combination
    score = (
        noise_ratio * 30 +
        error_ratio * 25 +
        switch_ratio * 25 +
        gap_ratio * 20
    )
    return round(min(100.0, max(0.0, score)), 1)


def _productivity_score(
    commands: List[Command],
    duration_seconds: int,
    flow_blocks: List[Dict[str, Any]],
) -> float:
    """Compute a 0-100 productivity score for a session.

    Factors:
    - Flow block coverage (fraction of session in flow state)
    - Command density (commands per minute)
    - Error rate (lower is better)
    - Noise ratio (lower is better)
    """
    if not commands or duration_seconds <= 0:
        return 0.0

    total = len(commands)

    # Flow coverage
    flow_time = sum(b["duration"] for b in flow_blocks)
    flow_ratio = flow_time / duration_seconds

    # Command density (commands per minute, capped at 5)
    cmds_per_min = min(5.0, (total / duration_seconds) * 60) if duration_seconds > 0 else 0
    density_score = (cmds_per_min / 5.0) * 100

    # Error-free ratio
    errors = sum(1 for c in commands if c.exit_code != 0)
    error_free = 1.0 - (errors / total) if total > 0 else 1.0

    # Noise-free ratio
    noise = sum(1 for c in commands if _is_noise_command(c.command))
    noise_free = 1.0 - (noise / total) if total > 0 else 1.0

    # Weighted score
    score = (
        flow_ratio * 40 +
        density_score * 25 +
        error_free * 20 +
        noise_free * 15
    )
    return round(min(100.0, max(0.0, score)), 1)


def _classify_productivity(score: float) -> str:
    """Map a productivity score to a tier label."""
    for threshold, label in PRODUCTIVITY_TIERS:
        if score >= threshold:
            return label
    return PRODUCTIVITY_TIERS[-1][1]


def _daily_flow_summary(sessions: List[Session]) -> List[Dict[str, Any]]:
    """Per-day summary of flow blocks, distractions, and productivity."""
    by_day: Dict[str, List[Session]] = defaultdict(list)
    for s in sessions:
        day = datetime.fromtimestamp(s.start_time).strftime("%Y-%m-%d")
        by_day[day].append(s)

    summaries = []
    for day, day_sessions in sorted(by_day.items()):
        all_cmds = []
        total_dur = 0
        total_flow_blocks = 0
        total_flow_time = 0
        scores = []

        for s in day_sessions:
            all_cmds.extend(s.commands)
            total_dur += s.duration_seconds
            blocks = _detect_flow_blocks(s.commands)
            total_flow_blocks += len(blocks)
            total_flow_time += sum(b["duration"] for b in blocks)
            prod = _productivity_score(s.commands, s.duration_seconds, blocks)
            scores.append(prod)

        avg_score = sum(scores) / len(scores) if scores else 0.0
        distraction = _distraction_score(all_cmds)

        summaries.append({
            "date": day,
            "weekday": datetime.strptime(day, "%Y-%m-%d").strftime("%A"),
            "total_time": total_dur,
            "flow_blocks": total_flow_blocks,
            "flow_time": total_flow_time,
            "productivity_score": avg_score,
            "distraction_score": distraction,
            "total_commands": len(all_cmds),
            "tier": _classify_productivity(avg_score),
        })

    return summaries


def _session_flow_analysis(sessions: List[Session]) -> List[Dict[str, Any]]:
    """Analyze each session for flow blocks and productivity."""
    results = []
    for s in sessions:
        blocks = _detect_flow_blocks(s.commands)
        prod = _productivity_score(s.commands, s.duration_seconds, blocks)
        dist = _distraction_score(s.commands)

        dt = datetime.fromtimestamp(s.start_time)
        results.append({
            "session_id": s.id,
            "date": dt.strftime("%Y-%m-%d"),
            "time": dt.strftime("%H:%M"),
            "duration": s.duration_seconds,
            "commands": len(s.commands),
            "flow_blocks": len(blocks),
            "flow_time": sum(b["duration"] for b in blocks),
            "productivity_score": prod,
            "distraction_score": dist,
            "tier": _classify_productivity(prod),
            "flow_details": blocks,
        })

    results.sort(key=lambda r: r["productivity_score"], reverse=True)
    return results


def _top_flow_blocks(sessions: List[Session], limit: int = 5) -> List[Dict[str, Any]]:
    """Find the longest flow blocks across all sessions."""
    all_blocks: List[Dict[str, Any]] = []
    for s in sessions:
        blocks = _detect_flow_blocks(s.commands)
        for b in blocks:
            # Find the dominant command category in this block
            block_cmds = [
                c for c in s.commands
                if b["start"] <= c.timestamp <= b["end"]
            ]
            cats = Counter(classify_command(c.command) for c in block_cmds)
            dominant = cats.most_common(1)[0][0] if cats else "unknown"

            dt = datetime.fromtimestamp(b["start"])
            all_blocks.append({
                **b,
                "date": dt.strftime("%Y-%m-%d"),
                "time": dt.strftime("%H:%M"),
                "dominant_category": dominant,
                "project_id": s.project_id,
            })

    all_blocks.sort(key=lambda x: x["duration"], reverse=True)
    return all_blocks[:limit]


def _distraction_patterns(sessions: List[Session]) -> Dict[str, Any]:
    """Identify common distraction patterns across sessions."""
    all_cmds: List[Command] = []
    for s in sessions:
        all_cmds.extend(s.commands)

    if not all_cmds:
        return {"noise_commands": [], "error_hotspots": [], "context_switches": 0}

    sorted_cmds = sorted(all_cmds, key=lambda c: c.timestamp)

    # Most frequent noise commands
    noise_cmds = Counter()
    for c in sorted_cmds:
        if _is_noise_command(c.command):
            base = c.command.strip().split()[0] if c.command.strip() else ""
            noise_cmds[base] += 1

    # Error hotspots: commands that frequently fail
    error_cmds: Counter = Counter()
    for c in sorted_cmds:
        if c.exit_code != 0:
            base = c.command.strip().split()[0] if c.command.strip() else ""
            error_cmds[base] += 1

    # Context switches: how many times the category changes between consecutive commands
    switches = 0
    prev_cat = None
    for c in sorted_cmds:
        cat = classify_command(c.command)
        if prev_cat is not None and cat != prev_cat:
            switches += 1
        prev_cat = cat

    return {
        "noise_commands": noise_cmds.most_common(5),
        "error_hotspots": error_cmds.most_common(5),
        "context_switches": switches,
    }


# ── Main entry point ────────────────────────────────────────────────


def build_flow_report(sessions: List[Session]) -> Dict[str, Any]:
    """Build a complete flow analysis report from session data.

    Parameters
    ----------
    sessions:
        All sessions to analyse.

    Returns
    -------
    dict
        Full flow report consumed by ``format_flow_output``.
    """
    # Overall metrics
    total_time = sum(s.duration_seconds for s in sessions)
    total_commands = sum(len(s.commands) for s in sessions)

    # All flow blocks
    all_flow_blocks: List[Dict[str, Any]] = []
    total_flow_time = 0
    session_scores: List[float] = []

    for s in sessions:
        blocks = _detect_flow_blocks(s.commands)
        all_flow_blocks.extend(blocks)
        total_flow_time += sum(b["duration"] for b in blocks)
        prod = _productivity_score(s.commands, s.duration_seconds, blocks)
        session_scores.append(prod)

    avg_productivity = round(sum(session_scores) / len(session_scores), 1) if session_scores else 0.0

    # Best / worst sessions
    session_analysis = _session_flow_analysis(sessions)
    best_session = session_analysis[0] if session_analysis else None
    worst_session = session_analysis[-1] if session_analysis else None

    # Daily summaries
    daily = _daily_flow_summary(sessions)
    best_day = max(daily, key=lambda d: d["productivity_score"]) if daily else None
    worst_day = min(daily, key=lambda d: d["productivity_score"]) if daily else None

    # Top flow blocks
    top_blocks = _top_flow_blocks(sessions, limit=5)

    # Distraction patterns
    distractions = _distraction_patterns(sessions)

    # Flow coverage: fraction of total work time in flow state
    flow_coverage = round((total_flow_time / total_time) * 100, 1) if total_time > 0 else 0.0

    return {
        "total_time": total_time,
        "total_commands": total_commands,
        "total_sessions": len(sessions),
        "total_flow_blocks": len(all_flow_blocks),
        "total_flow_time": total_flow_time,
        "flow_coverage": flow_coverage,
        "avg_productivity": avg_productivity,
        "avg_tier": _classify_productivity(avg_productivity),
        "best_session": best_session,
        "worst_session": worst_session,
        "best_day": best_day,
        "worst_day": worst_day,
        "daily": daily,
        "top_flow_blocks": top_blocks,
        "distractions": distractions,
        "session_analysis": session_analysis[:10],  # Top 10
    }
