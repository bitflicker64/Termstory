"""
predict.py — Pre-Cognitive Workspace Engine

Analyzes historical session patterns to predict what a developer will likely
work on next. Surfaces context momentum, likely projects, and suggested commands
before the developer even opens a terminal.

Design principles:
  - Density over decoration (no panels, no boxing)
  - Recognize, don't inspect: outputs are scan-optimized, not verbose
  - Noise-filtered: only creative/memorable work surfaces in predictions
  - Respects is_legacy flag — synthetic timestamps are excluded
"""

import sqlite3
from datetime import datetime, timedelta
from collections import Counter, defaultdict
from typing import List, Dict, Optional, Tuple

from termstory.database import Database

# ---------------------------------------------------------------------------
# Noise commands that are excluded from prediction signals
# (mirrors formatter.py's _is_noise_command heuristic)
# ---------------------------------------------------------------------------
_NOISE_PREFIXES = (
    "cd ", "ls", "pwd", "echo ", "cat ", "man ",
    "history", "clear", "exit", "which ", "type ",
    "git status", "git log", "git diff", "git branch",
    "docker ps", "docker logs", "docker inspect",
    "grep ", "awk ", "sed ", "head ", "tail ",
    "ping ", "curl -I", "wget --spider",
)

_NOISE_EXACT = {"ls", "pwd", "clear", "exit", "history", "cd"}


def _is_noise(cmd: str) -> bool:
    """Return True if the command is routine navigation / inspection noise."""
    stripped = cmd.strip()
    if stripped in _NOISE_EXACT:
        return True
    for prefix in _NOISE_PREFIXES:
        if stripped.startswith(prefix):
            return True
    return False


# ---------------------------------------------------------------------------
# Time-of-day bucketing
# ---------------------------------------------------------------------------
_HOUR_BUCKETS = [
    (0, 6,   "night"),
    (6, 9,   "early-morning"),
    (9, 12,  "morning"),
    (12, 14, "midday"),
    (14, 18, "afternoon"),
    (18, 22, "evening"),
    (22, 24, "late-night"),
]


def _hour_bucket(hour: int) -> str:
    for lo, hi, label in _HOUR_BUCKETS:
        if lo <= hour < hi:
            return label
    return "unknown"


def _day_label(weekday: int) -> str:
    return ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][weekday]


# ---------------------------------------------------------------------------
# Core prediction engine
# ---------------------------------------------------------------------------

class Predictor:
    """
    Analyses all non-legacy sessions to compute prediction signals.

    Signals used:
      1. Recency momentum  — sessions from the last 7 days carry strong weight
      2. Time-of-day affinity — if it's morning and you always code Project X in
         mornings, surface it
      3. Day-of-week cadence — if it's Monday and you always restart Project Y
         on Mondays (e.g., picking up Friday work), flag it
      4. Command repertoire — what tools you typically reach for in each project
      5. Gap detection — sessions with a trailing gap > 12 h are flagged as
         "interrupted"; those projects are surfaced as likely resumption targets
    """

    def __init__(self, db_path: str):
        self.db_path = db_path

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _load_sessions(self, cutoff_time: Optional[int] = None) -> List[Dict]:
        """
        Load all non-legacy sessions with their project names, commands, and
        session boundaries directly from SQLite — no ORM overhead.
        """
        conn = Database(self.db_path).get_connection()
        try:
            cursor = conn.cursor()

            query = """
                SELECT
                    s.id,
                    s.start_time,
                    s.end_time,
                    s.duration_seconds,
                    s.project_id,
                    COALESCE(p.name, 'Other') AS project_name,
                    p.path AS project_path
                FROM sessions s
                LEFT JOIN projects p ON s.project_id = p.id
            """
            params = []
            if cutoff_time is not None:
                query += " WHERE s.start_time >= ?"
                params.append(cutoff_time)
            query += " ORDER BY s.start_time ASC"

            cursor.execute(query, params)
            session_rows = cursor.fetchall()

            sessions = []
            for row in session_rows:
                s_id, start, end, dur, p_id, p_name, p_path = row

                # Fetch commands for this session, filter out legacy
                cursor.execute("""
                    SELECT command, is_legacy
                    FROM commands
                    WHERE session_id = ?
                    ORDER BY timestamp ASC
                """, (s_id,))
                cmd_rows = cursor.fetchall()

                # Skip session if ALL commands are legacy
                if cmd_rows and all(bool(r[1]) for r in cmd_rows):
                    continue

                cmds = [r[0] for r in cmd_rows if not _is_noise(r[0])]
                project_name = p_name if p_name and p_name not in ("General / No Project", "") else "Other"

                sessions.append({
                    "id": s_id,
                    "start": start,
                    "end": end,
                    "duration": dur or 0,
                    "project_id": p_id,
                    "project_name": project_name,
                    "project_path": p_path or "",
                    "commands": cmds,
                })
        finally:
            conn.close()

        return sessions

    # ------------------------------------------------------------------
    # Signal computation
    # ------------------------------------------------------------------

    def _compute_signals(
        self, sessions: List[Dict], now: datetime
    ) -> Dict:
        """
        Return a dict of prediction signals built from the session history.
        """
        if not sessions:
            return {}

        now_bucket = _hour_bucket(now.hour)
        now_day = _day_label(now.weekday())
        now_ts = now.timestamp()

        # --- Recency scores (exponential decay, half-life = 3 days) ---
        half_life_seconds = 3 * 24 * 3600
        project_recency: Dict[str, float] = defaultdict(float)
        for s in sessions:
            age = now_ts - s["start"]
            if age < 0:
                continue
            decay = 0.5 ** (age / half_life_seconds)
            project_recency[s["project_name"]] += decay * (s["duration"] / 3600.0 + 0.1)

        # --- Time-of-day affinity ---
        project_bucket_count: Dict[Tuple[str, str], int] = defaultdict(int)
        for s in sessions:
            dt = datetime.fromtimestamp(s["start"])
            bucket = _hour_bucket(dt.hour)
            project_bucket_count[(s["project_name"], bucket)] += 1

        # For each project, compute affinity = count in current bucket / total count
        total_by_project: Counter = Counter(s["project_name"] for s in sessions)
        project_time_affinity: Dict[str, float] = {}
        for proj, total in total_by_project.items():
            in_bucket = project_bucket_count.get((proj, now_bucket), 0)
            project_time_affinity[proj] = in_bucket / max(total, 1)

        # --- Day-of-week cadence ---
        project_day_count: Dict[Tuple[str, str], int] = defaultdict(int)
        for s in sessions:
            dt = datetime.fromtimestamp(s["start"])
            day = _day_label(dt.weekday())
            project_day_count[(s["project_name"], day)] += 1

        project_day_affinity: Dict[str, float] = {}
        for proj, total in total_by_project.items():
            on_day = project_day_count.get((proj, now_day), 0)
            project_day_affinity[proj] = on_day / max(total, 1)

        # --- Interrupted sessions (gap > 12h before now) ---
        interrupted: List[Dict] = []
        cutoff_12h = now_ts - 12 * 3600
        cutoff_7d = now_ts - 7 * 24 * 3600
        for s in sessions:
            # Session ended more than 12h ago but within 7 days
            if cutoff_7d <= s["end"] <= cutoff_12h:
                # No subsequent session in the same project within 12h after this one
                gap_end = s["end"] + 12 * 3600
                has_followup = any(
                    t["project_name"] == s["project_name"]
                    and s["end"] < t["start"] <= gap_end
                    for t in sessions
                )
                if not has_followup:
                    interrupted.append(s)

        # Most recently interrupted session (if any)
        interrupted.sort(key=lambda x: x["end"], reverse=True)
        interrupted_session = interrupted[0] if interrupted else None

        # --- Command repertoire per project (top-5 non-noise commands) ---
        project_commands: Dict[str, Counter] = defaultdict(Counter)
        for s in sessions:
            for cmd in s["commands"]:
                # Extract base tool (first word or first two words)
                parts = cmd.strip().split()
                if not parts:
                    continue
                key = parts[0] if len(parts) == 1 else f"{parts[0]} {parts[1]}"
                project_commands[s["project_name"]][key] += 1

        project_top_cmds: Dict[str, List[str]] = {}
        for proj, counter in project_commands.items():
            project_top_cmds[proj] = [cmd for cmd, _ in counter.most_common(5)]

        # --- Combined score ---
        all_projects = set(total_by_project.keys())
        combined: Dict[str, float] = {}
        for proj in all_projects:
            score = (
                project_recency.get(proj, 0.0) * 2.0
                + project_time_affinity.get(proj, 0.0) * 1.5
                + project_day_affinity.get(proj, 0.0) * 1.0
            )
            combined[proj] = score

        # Boost interrupted project
        if interrupted_session:
            iproj = interrupted_session["project_name"]
            combined[iproj] = combined.get(iproj, 0.0) + 3.0

        return {
            "now_bucket": now_bucket,
            "now_day": now_day,
            "combined_scores": combined,
            "interrupted_session": interrupted_session,
            "project_top_cmds": project_top_cmds,
            "project_recency": project_recency,
            "project_time_affinity": project_time_affinity,
            "project_day_affinity": project_day_affinity,
            "total_sessions": len(sessions),
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def predict(
        self,
        top_n: int = 3,
        now: Optional[datetime] = None,
        days: Optional[int] = None,
    ) -> Dict:
        """
        Run the prediction pipeline and return structured results.

        Returns:
            {
                "now": datetime,
                "top_projects": [
                    {
                        "rank": 1,
                        "project_name": "termstory",
                        "project_path": "~/Projects/termstory",
                        "score": 4.71,
                        "signals": ["recency", "interrupted", "morning affinity"],
                        "suggested_commands": ["python -m pytest", "git add", ...],
                        "interrupted_at": "Friday 18:42"  # or None
                    }, ...
                ],
                "total_sessions_analysed": 342,
                "time_context": "Monday morning",
                "message": "No history available"  # only if empty
            }
        """
        if now is None:
            now = datetime.now()

        cutoff_time = None
        if days is not None:
            cutoff_time = int((now - timedelta(days=days)).timestamp())

        sessions = self._load_sessions(cutoff_time=cutoff_time)

        if not sessions:
            return {
                "now": now,
                "top_projects": [],
                "total_sessions_analysed": 0,
                "time_context": f"{_day_label(now.weekday())} {_hour_bucket(now.hour)}",
                "message": "No session history found. Run some commands and re-ingest.",
            }

        signals = self._compute_signals(sessions, now)
        combined = signals["combined_scores"]
        interrupted = signals["interrupted_session"]

        # Rank projects by combined score
        ranked = sorted(combined.items(), key=lambda x: x[1], reverse=True)[:top_n]

        top_projects = []
        for rank, (proj_name, score) in enumerate(ranked, start=1):
            # Build signal tags
            signal_tags = []
            if signals["project_recency"].get(proj_name, 0) > 0.3:
                signal_tags.append("recency")
            if interrupted and interrupted["project_name"] == proj_name:
                signal_tags.append("interrupted")
            if signals["project_time_affinity"].get(proj_name, 0) > 0.2:
                signal_tags.append(f"{signals['now_bucket']} affinity")
            if signals["project_day_affinity"].get(proj_name, 0) > 0.2:
                signal_tags.append(f"{signals['now_day']} cadence")
            if not signal_tags:
                signal_tags.append("historical pattern")

            # Find project path from sessions
            proj_path = next(
                (s["project_path"] for s in sessions if s["project_name"] == proj_name),
                ""
            )

            interrupted_at = None
            if interrupted and interrupted["project_name"] == proj_name:
                dt = datetime.fromtimestamp(interrupted["end"])
                interrupted_at = dt.strftime("%A %H:%M")

            top_projects.append({
                "rank": rank,
                "project_name": proj_name,
                "project_path": proj_path,
                "score": round(score, 2),
                "signals": signal_tags,
                "suggested_commands": signals["project_top_cmds"].get(proj_name, []),
                "interrupted_at": interrupted_at,
            })

        time_context = f"{signals['now_day']} {signals['now_bucket']}"

        return {
            "now": now,
            "top_projects": top_projects,
            "total_sessions_analysed": signals["total_sessions"],
            "time_context": time_context,
        }


# ---------------------------------------------------------------------------
# Output formatter — density-over-decoration, no Panel, no borders
# ---------------------------------------------------------------------------

def format_predict_output(result: Dict) -> str:
    """
    Render prediction results as dense, screenshot-friendly terminal output.
    Returns a plain string with ANSI-compatible colour escapes via Rich markup
    embedded — caller should pass through rich.text.Text.from_ansi or console.print.
    """
    lines = []

    now: datetime = result["now"]
    context = result.get("time_context", "")
    total = result.get("total_sessions_analysed", 0)

    # Header
    lines.append(f"\033[1;36m◆ Pre-Cognitive Workspace\033[0m  "
                 f"\033[2m{now.strftime('%a %b %d, %Y  %H:%M')} · {context} · {total} sessions\033[0m")
    lines.append("\033[2m" + "─" * 72 + "\033[0m")

    if "message" in result:
        lines.append(f"\033[33m{result['message']}\033[0m")
        return "\n".join(lines)

    top = result.get("top_projects", [])
    if not top:
        lines.append("\033[33mInsufficient session history to predict next work.\033[0m")
        return "\n".join(lines)

    for p in top:
        rank = p["rank"]
        name = p["project_name"]
        path = p["project_path"].replace(
            __import__("os").path.expanduser("~"), "~"
        ) if p["project_path"] else ""
        score = p["score"]
        signals = "  ".join(f"\033[35m{s}\033[0m" for s in p["signals"])
        interrupted_at = p.get("interrupted_at")
        cmds = p.get("suggested_commands", [])

        # Rank badge
        badge = "\033[1;33m★\033[0m" if rank == 1 else "\033[2m·\033[0m"

        # Project line
        path_suffix = f"  \033[2m{path}\033[0m" if path else ""
        lines.append(f"  {badge}  \033[1;37m{name}\033[0m{path_suffix}")

        # Signals line
        lines.append(f"     \033[2mscore {score:5.2f}  ·  \033[0m{signals}")

        # Interrupted context
        if interrupted_at:
            lines.append(
                f"     \033[33m⚡ Interrupted session detected — last active {interrupted_at}\033[0m"
            )

        # Suggested commands
        if cmds:
            cmd_str = "  ".join(f"\033[36m{c}\033[0m" for c in cmds[:4])
            lines.append(f"     \033[2mlikely:\033[0m  {cmd_str}")

        lines.append("")  # blank separator between projects

    # Footer tip
    lines.append(
        "\033[2mRun \033[0m\033[36mtermstory predict --top 5\033[0m\033[2m for more · "
        "\033[0m\033[36mtermstory predict --json\033[0m\033[2m for machine-readable output\033[0m"
    )

    return "\n".join(lines)
