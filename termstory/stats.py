import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

from termstory.database import Database
from termstory.date_utils import get_current_time

logger = logging.getLogger(__name__)

def daily_activity_heatmap(db: Database, days_limit: int = 30, colored: bool = True) -> str:
    """Generate a GitHub-like activity heatmap from the database for the last N days."""
    now = get_current_time().date()
    since_ts = int(datetime.combine(now - timedelta(days=days_limit), datetime.min.time()).timestamp())
    
    conn = db.get_connection()
    try:
        cursor = conn.cursor()
        # Only count commands that are NOT legacy if we want to follow insights/TUI rules,
        # but let's query all commands or respect legacy status.
        # Let's count all commands since it represents raw command volume.
        cursor.execute("SELECT timestamp FROM commands WHERE timestamp >= ?", (since_ts,))
        rows = cursor.fetchall()
    finally:
        conn.close()
        
    day_counts = defaultdict(int)
    for (ts,) in rows:
        dt = datetime.fromtimestamp(ts)
        day_counts[dt.date()] += 1
        
    heatmap_blocks = []
    for i in range(days_limit - 1, -1, -1):
        target_date = now - timedelta(days=i)
        cmd_count = day_counts[target_date]
        if cmd_count == 0:
            heatmap_blocks.append("[grey37]░[/]" if colored else "░")
        elif cmd_count < 5:
            heatmap_blocks.append("[green]▄[/]" if colored else "▄")
        elif cmd_count < 20:
            heatmap_blocks.append("[bold green]■[/]" if colored else "■")
        else:
            heatmap_blocks.append("[bold reverse green]█[/]" if colored else "█")
            
    return " ".join(heatmap_blocks)

def project_breakdown(db: Database) -> Dict[str, Dict[str, Any]]:
    """Calculate statistics (commands, duration, sessions, first/last seen) for each project.
    Maps empty or 'General / No Project' names to 'Other'."""
    conn = db.get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, path, first_seen, last_seen FROM projects")
        projects = cursor.fetchall()
        
        cursor.execute("SELECT project_id, COUNT(*) FROM commands GROUP BY project_id")
        cmd_counts = dict(cursor.fetchall())
        
        cursor.execute("SELECT project_id, COUNT(*), SUM(duration_seconds) FROM sessions GROUP BY project_id")
        session_stats = {row[0]: (row[1], row[2] or 0) for row in cursor.fetchall()}
    finally:
        conn.close()
        
    breakdown = {}
    
    # Account for commands/sessions with project_id IS NULL -> "Other"
    null_cmds = cmd_counts.get(None, 0)
    null_sess, null_dur = session_stats.get(None, (0, 0))
    
    breakdown["Other"] = {
        "id": None,
        "path": None,
        "commands_count": null_cmds,
        "total_duration": null_dur,
        "sessions_count": null_sess,
        "first_seen": None,
        "last_seen": None,
    }
    
    for p_id, name, path, first_seen, last_seen in projects:
        mapped_name = name
        if not name or name == "General / No Project":
            mapped_name = "Other"
            
        cmds = cmd_counts.get(p_id, 0)
        sess, dur = session_stats.get(p_id, (0, 0))
        
        if mapped_name == "Other":
            breakdown["Other"]["commands_count"] += cmds
            breakdown["Other"]["total_duration"] += dur
            breakdown["Other"]["sessions_count"] += sess
            if first_seen is not None:
                if breakdown["Other"]["first_seen"] is None:
                    breakdown["Other"]["first_seen"] = first_seen
                else:
                    breakdown["Other"]["first_seen"] = min(breakdown["Other"]["first_seen"], first_seen)
            if last_seen is not None:
                if breakdown["Other"]["last_seen"] is None:
                    breakdown["Other"]["last_seen"] = last_seen
                else:
                    breakdown["Other"]["last_seen"] = max(breakdown["Other"]["last_seen"], last_seen)
        else:
            if mapped_name in breakdown:
                breakdown[mapped_name]["commands_count"] += cmds
                breakdown[mapped_name]["total_duration"] += dur
                breakdown[mapped_name]["sessions_count"] += sess
                if first_seen is not None:
                    if breakdown[mapped_name]["first_seen"] is None:
                        breakdown[mapped_name]["first_seen"] = first_seen
                    else:
                        breakdown[mapped_name]["first_seen"] = min(breakdown[mapped_name]["first_seen"], first_seen)
                if last_seen is not None:
                    if breakdown[mapped_name]["last_seen"] is None:
                        breakdown[mapped_name]["last_seen"] = last_seen
                    else:
                        breakdown[mapped_name]["last_seen"] = max(breakdown[mapped_name]["last_seen"], last_seen)
            else:
                breakdown[mapped_name] = {
                    "id": p_id,
                    "path": path,
                    "commands_count": cmds,
                    "total_duration": dur,
                    "sessions_count": sess,
                    "first_seen": first_seen,
                    "last_seen": last_seen,
                }
                
    # If "Other" has first/last seen as None, check if we can pull from NULL project_id records
    if breakdown["Other"]["first_seen"] is None or breakdown["Other"]["last_seen"] is None:
        conn = db.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT MIN(timestamp), MAX(timestamp) FROM commands WHERE project_id IS NULL")
            c_min, c_max = cursor.fetchone()
            cursor.execute("SELECT MIN(start_time), MAX(end_time) FROM sessions WHERE project_id IS NULL")
            s_min, s_max = cursor.fetchone()
        finally:
            conn.close()
            
        times = [t for t in [c_min, c_max, s_min, s_max] if t is not None]
        if times:
            if breakdown["Other"]["first_seen"] is None:
                breakdown["Other"]["first_seen"] = min(times)
            else:
                breakdown["Other"]["first_seen"] = min(breakdown["Other"]["first_seen"], min(times))
            if breakdown["Other"]["last_seen"] is None:
                breakdown["Other"]["last_seen"] = max(times)
            else:
                breakdown["Other"]["last_seen"] = max(breakdown["Other"]["last_seen"], max(times))

    # Remove "Other" if completely empty to keep results clean
    if (breakdown["Other"]["commands_count"] == 0 and 
        breakdown["Other"]["total_duration"] == 0 and 
        breakdown["Other"]["sessions_count"] == 0):
        del breakdown["Other"]
        
    return breakdown

def _project_display_name(name):
    """Map empty/'General / No Project' project names to 'Other'.

    Mirrors the display-name rule used by ``project_breakdown()`` so machine-readable
    output stays label-consistent with the human-readable dashboard.
    """
    if not name or name == "General / No Project":
        return "Other"
    return name

def _safe_datetime_from_ts(ts):
    """Convert a Unix timestamp to a ``datetime``, skipping invalid values.

    Returns ``None`` for ``None``/out-of-range input instead of raising, following
    the existing repository convention (see ``insights.analyze_all``, which wraps
    ``datetime.fromtimestamp`` in ``except (OSError, ValueError, OverflowError)``)
    so a single malformed row cannot crash ``stats --json``.
    """
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(ts)
    except (OSError, ValueError, OverflowError):
        return None

def _iso_from_ts(ts):
    """Convert a Unix timestamp to a stable ISO-8601 string (or ``None``)."""
    dt = _safe_datetime_from_ts(ts)
    return dt.isoformat() if dt is not None else None

def project_breakdown_json(db: Database):
    """Identity-keyed variant of :func:`project_breakdown` for JSON output.

    ``project_breakdown()`` aggregates into a name-keyed dict, which silently merges
    two distinct projects that share a display name (keeping only one id/path). This
    variant keys each aggregate by the project's stable ``id`` so same-named projects
    stay separate rows, while preserving:

    * the ``General / No Project`` -> ``Other`` display-name mapping, and
    * folding project-less activity (``project_id IS NULL``) into a single
      ``Other`` bucket (dropped when it has no activity), like project_breakdown().

    Returns a list of plain dicts sorted deterministically by
    ``(-total_duration, name, id)`` so JSON output is stable across runs.
    """
    conn = db.get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, path, first_seen, last_seen FROM projects")
        projects = cursor.fetchall()

        cursor.execute("SELECT project_id, COUNT(*) FROM commands GROUP BY project_id")
        cmd_counts = dict(cursor.fetchall())

        cursor.execute(
            "SELECT project_id, COUNT(*), SUM(duration_seconds) "
            "FROM sessions GROUP BY project_id"
        )
        session_stats = {row[0]: (row[1], row[2] or 0) for row in cursor.fetchall()}

        # First/last activity for project-less rows (mirrors project_breakdown).
        cursor.execute(
            "SELECT MIN(timestamp), MAX(timestamp) FROM commands WHERE project_id IS NULL"
        )
        c_min, c_max = cursor.fetchone()
        cursor.execute(
            "SELECT MIN(start_time), MAX(end_time) FROM sessions WHERE project_id IS NULL"
        )
        s_min, s_max = cursor.fetchone()
    finally:
        conn.close()

    entries = {}
    for p_id, name, path, first_seen, last_seen in projects:
        sess_count, total_dur = session_stats.get(p_id, (0, 0))
        entries[p_id] = {
            "name": _project_display_name(name),
            "id": p_id,
            "path": path,
            "commands_count": cmd_counts.get(p_id, 0),
            "total_duration": total_dur,
            "sessions_count": sess_count,
            "first_seen": first_seen,
            "last_seen": last_seen,
        }

    null_sess, null_dur = session_stats.get(None, (0, 0))
    null_cmds = cmd_counts.get(None, 0)
    if null_cmds or null_sess or null_dur:
        times = [t for t in (c_min, c_max, s_min, s_max) if t is not None]
        entries[None] = {
            "name": "Other",
            "id": None,
            "path": None,
            "commands_count": null_cmds,
            "total_duration": null_dur,
            "sessions_count": null_sess,
            "first_seen": min(times) if times else None,
            "last_seen": max(times) if times else None,
        }

    def _sort_key(p_id):
        e = entries[p_id]
        return (-e["total_duration"], e["name"], p_id is None, p_id or 0)

    return [entries[key] for key in sorted(entries.keys(), key=_sort_key)]

def stats_json(db: Database) -> Dict[str, Any]:
    """Assemble a plain, machine-readable statistics payload.

    Uses the identity-keyed :func:`project_breakdown_json` variant (preserving the
    ``General / No Project`` -> ``Other`` display mapping) instead of duplicating the
    calculation, and computes the top-level session/command/project totals plus the
    overall activity window directly from the database.

    The activity window is built from every valid timestamp source via ``UNION ALL``
    (session starts, non-NULL session ends, and command timestamps) so an unfinished
    session (``end_time IS NULL``) still contributes its ``start_time`` to ``latest``.

    Timestamps are serialized with :func:`_iso_from_ts`, which follows the existing
    repository convention of skipping invalid/out-of-range values rather than
    crashing.

    All return values are plain JSON-compatible primitives (ints, strings or None) so
    the result can be passed straight to ``json.dumps()`` with no custom serializer.

    Returns a dict shaped as::

        {
            "total_sessions": int,
            "total_commands": int,
            "total_projects": int,
            "time_range": {"earliest": str | None, "latest": str | None},
            "projects": [
                {"name", "id", "path", "commands_count", "total_duration",
                 "sessions_count", "first_seen", "last_seen"}, ...
            ],
        }

    For an empty database, all totals are 0, ``projects`` is ``[]`` and the time
    range is ``{"earliest": None, "latest": None}``.
    """
    conn = db.get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM sessions")
        total_sessions = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM commands")
        total_commands = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM projects")
        total_projects = cursor.fetchone()[0]

        # Collect every timestamp source via UNION ALL. Doing MIN/MAX in SQL here
        # would be wrong twice over: an unfinished session (end_time IS NULL) must
        # still contribute its start_time to `latest`, and SQLite would happily
        # return an out-of-range outlier that Python cannot convert -- masking all
        # valid activity. So candidates are filtered/converted per the repository
        # convention (skip OSError/ValueError/OverflowError) before min/max.
        sql = (
            "SELECT ts FROM ("
            " SELECT start_time AS ts FROM sessions WHERE start_time IS NOT NULL"
            " UNION ALL"
            " SELECT end_time AS ts FROM sessions WHERE end_time IS NOT NULL"
            " UNION ALL"
            " SELECT timestamp AS ts FROM commands WHERE timestamp IS NOT NULL"
            ") AS activity"
        )
        cursor.execute(sql)
        candidate_rows = cursor.fetchall()
    finally:
        conn.close()

    valid_datetimes = []
    for (ts,) in candidate_rows:
        dt = _safe_datetime_from_ts(ts)
        if dt is not None:
            valid_datetimes.append(dt)

    if valid_datetimes:
        earliest_dt = min(valid_datetimes)
        latest_dt = max(valid_datetimes)
    else:
        earliest_dt = None
        latest_dt = None

    projects = []
    for stats in project_breakdown_json(db):
        projects.append({
            "name": stats["name"],
            "id": stats["id"],
            "path": stats["path"],
            "commands_count": stats["commands_count"],
            "total_duration": stats["total_duration"],
            "sessions_count": stats["sessions_count"],
            "first_seen": _iso_from_ts(stats["first_seen"]),
            "last_seen": _iso_from_ts(stats["last_seen"]),
        })

    return {
        "total_sessions": total_sessions,
        "total_commands": total_commands,
        "total_projects": total_projects,
        "time_range": {
            "earliest": earliest_dt.isoformat() if earliest_dt else None,
            "latest": latest_dt.isoformat() if latest_dt else None,
        },
        "projects": projects,
    }


_LANG_CACHE = {}

def detect_project_language_from_files(path: str) -> Optional[str]:
    """Helper to check common config files on disk to infer project language."""
    if not path:
        return None
    expanded = os.path.expanduser(path)
    if expanded in _LANG_CACHE:
        return _LANG_CACHE[expanded]
        
    if not os.path.isdir(expanded):
        _LANG_CACHE[expanded] = None
        return None
        
    path_lower = expanded.lower()
    for prefix in ["/mnt", "/volumes/smb", "\\\\"]:
        if path_lower.startswith(prefix):
            _LANG_CACHE[expanded] = None
            return None
            
    checks = [
        ("Cargo.toml", "Rust"),
        ("package.json", "JavaScript/TypeScript"),
        ("pyproject.toml", "Python"),
        ("setup.py", "Python"),
        ("requirements.txt", "Python"),
        ("go.mod", "Go"),
        ("pom.xml", "Java/Kotlin"),
        ("build.gradle", "Java/Kotlin"),
        ("CMakeLists.txt", "C/C++"),
        ("Gemfile", "Ruby"),
        ("composer.json", "PHP"),
    ]
    
    for filename, lang in checks:
        try:
            if os.path.exists(os.path.join(expanded, filename)):
                _LANG_CACHE[expanded] = lang
                return lang
        except Exception:
            logger.debug("Failed to check for config file %s in %s", filename, path, exc_info=True)
            
    try:
        for f in os.listdir(expanded):
            if f.endswith(".csproj") or f.endswith(".sln"):
                _LANG_CACHE[expanded] = "C#"
                return "C#"
            if f == "Makefile":
                _LANG_CACHE[expanded] = "C/C++"
                return "C/C++"
    except Exception:
        logger.debug("Failed to list directory contents for language detection: %s", path, exc_info=True)
        
    _LANG_CACHE[expanded] = None
    return None

def language_detection(db: Database) -> Dict[str, float]:
    """Detect language distribution based on project files and executed commands.
    Returns: Dict[language_name, percentage_float] sorted by percentage DESC."""
    conn = db.get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id, path FROM projects")
        projects = cursor.fetchall()
        
        cutoff = int((get_current_time() - timedelta(days=90)).timestamp())
        cursor.execute("SELECT project_id, command FROM commands WHERE timestamp >= ?", (cutoff,))
        commands = cursor.fetchall()
    finally:
        conn.close()
        
    project_langs = {}
    for p_id, path in projects:
        if path:
            lang = detect_project_language_from_files(path)
            if lang:
                project_langs[p_id] = lang
                
    lang_counts = defaultdict(int)
    total_classified = 0
    
    cmd_classifications = {
        "python": "Python", "python3": "Python", "pip": "Python", "pip3": "Python", "pytest": "Python", "poetry": "Python",
        "uv": "Python", "uvx": "Python",
        "npm": "JavaScript/TypeScript", "yarn": "JavaScript/TypeScript", "pnpm": "JavaScript/TypeScript",
        "node": "JavaScript/TypeScript", "npx": "JavaScript/TypeScript", "tsc": "JavaScript/TypeScript",
        "cargo": "Rust", "rustc": "Rust",
        "go": "Go",
        "mvn": "Java/Kotlin", "gradle": "Java/Kotlin", "java": "Java/Kotlin", "javac": "Java/Kotlin",
        "gcc": "C/C++", "g++": "C/C++", "clang": "C/C++", "clang++": "C/C++", "make": "C/C++", "cmake": "C/C++",
        "ruby": "Ruby", "gem": "Ruby", "bundle": "Ruby",
        "php": "PHP", "composer": "PHP",
        "dotnet": "C#",
        "swift": "Swift", "swiftc": "Swift",
        "sh": "Shell", "bash": "Shell", "zsh": "Shell"
    }
    
    for project_id, cmd_text in commands:
        lang = None
        if project_id in project_langs:
            lang = project_langs[project_id]
        else:
            tokens = cmd_text.strip().split()
            if tokens:
                first_token = os.path.basename(tokens[0].lower())
                for ext in ['.exe', '.bat', '.cmd', '.sh']:
                    if first_token.endswith(ext):
                        first_token = first_token[:-len(ext)]
                        break
                lang = cmd_classifications.get(first_token)
                if not lang:
                    if first_token.startswith("python") or first_token.startswith("pip") or first_token.startswith("pytest"):
                        lang = "Python"
                    elif first_token.startswith("npm") or first_token.startswith("yarn") or first_token.startswith("pnpm") or first_token.startswith("node"):
                        lang = "JavaScript/TypeScript"
                    elif first_token.startswith("cargo") or first_token.startswith("rust"):
                        lang = "Rust"
                    elif first_token == "go" or first_token.startswith("go-") or first_token.startswith("gofmt") or first_token.startswith("gotest"):
                        lang = "Go"
                
        if lang:
            lang_counts[lang] += 1
            total_classified += 1
            
    if total_classified == 0:
        return {}
        
    lang_percentages = {}
    for lang, count in lang_counts.items():
        lang_percentages[lang] = round((count / total_classified) * 100, 1)
        
    return dict(sorted(lang_percentages.items(), key=lambda x: x[1], reverse=True))

def peak_hours(db: Database) -> Dict[int, int]:
    """Calculate command counts by hour of day (0-23).
    Returns: Dict[hour, command_count] sorted by hour."""
    conn = db.get_connection()
    try:
        cursor = conn.cursor()
        cutoff = int((get_current_time() - timedelta(days=90)).timestamp())
        cursor.execute("SELECT timestamp FROM commands WHERE timestamp >= ?", (cutoff,))
        rows = cursor.fetchall()
    finally:
        conn.close()
        
    hourly_counts = {h: 0 for h in range(24)}
    for (ts,) in rows:
        dt = datetime.fromtimestamp(ts)
        hourly_counts[dt.hour] += 1
        
    return hourly_counts
