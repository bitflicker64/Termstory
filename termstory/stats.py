import os
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

from termstory.database import Database
from termstory.date_utils import get_current_time

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

def detect_project_language_from_files(path: str) -> Optional[str]:
    """Helper to check common config files on disk to infer project language."""
    if not path or not os.path.isdir(path):
        return None
        
    path_lower = path.lower()
    for prefix in ["/mnt", "/volumes/smb", "\\\\"]:
        if path_lower.startswith(prefix):
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
            if os.path.exists(os.path.join(path, filename)):
                return lang
        except Exception:
            pass
            
    try:
        for f in os.listdir(path):
            if f.endswith(".csproj") or f.endswith(".sln"):
                return "C#"
            if f == "Makefile":
                return "C/C++"
    except Exception:
        pass
        
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
                lang = cmd_classifications.get(first_token)
                
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
