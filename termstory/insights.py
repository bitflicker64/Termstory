from datetime import datetime
from collections import defaultdict
from typing import List, Tuple, Dict, Any
from termstory.models import Session, Project, Command, format_duration
from termstory.formatter import classify_command, DISPLAY_NAMES

def calculate_time_distribution(sessions: List[Session], projects: List[Project]) -> List[Tuple[str, float, int]]:
    """Calculate the percentage of total hours and absolute time spent on each project.
    Returns: [(project_name, percentage, duration_seconds), ...] sorted by duration DESC
    """
    total_time = sum(s.duration_seconds for s in sessions)
    if total_time == 0:
        return []
        
    project_map = {p.id: p.name for p in projects if p.id is not None}
    time_by_project = defaultdict(int)
    
    for s in sessions:
        p_name = project_map.get(s.project_id, "General / No Project")
        time_by_project[p_name] += s.duration_seconds
        
    sorted_time = sorted(time_by_project.items(), key=lambda x: x[1], reverse=True)
    
    distribution = []
    for p_name, duration in sorted_time:
        pct = (duration / total_time) * 100
        distribution.append((p_name, pct, duration))
        
    return distribution

def calculate_time_of_day_distribution(sessions: List[Session]) -> Dict[str, int]:
    """Calculate total seconds spent in Morning (6-12), Afternoon (12-18), and Evening (18-6)"""
    distribution = {"morning": 0, "afternoon": 0, "evening": 0}
    
    for session in sessions:
        # Determine time-of-day category by the midpoint of the session
        mid_ts = (session.start_time + session.end_time) // 2
        dt = datetime.fromtimestamp(mid_ts)
        hour = dt.hour
        
        if 6 <= hour < 12:
            distribution["morning"] += session.duration_seconds
        elif 12 <= hour < 18:
            distribution["afternoon"] += session.duration_seconds
        else:
            distribution["evening"] += session.duration_seconds
            
    return distribution

def calculate_day_distribution(sessions: List[Session]) -> Dict[str, int]:
    """Group session durations by day of week"""
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    distribution = {d: 0 for d in days}
    
    for session in sessions:
        dt = datetime.fromtimestamp(session.start_time)
        day_name = dt.strftime("%A")
        if day_name in distribution:
            distribution[day_name] += session.duration_seconds
            
    return distribution

def calculate_focus_score(sessions: List[Session]) -> float:
    """Calculate a focus score out of 10.0 based on context switching and session lengths.
    - Base score: 5.0
    - Context switches per day: penalty of up to 3.0 (switches = unique projects per active day minus 1)
    - Session duration: bonus of up to 5.0 (longer average sessions reflect deeper focus)
    """
    if not sessions:
        return 0.0
        
    # Group sessions by calendar day to find unique projects worked on per day
    projects_by_day = defaultdict(set)
    total_duration = 0
    
    for s in sessions:
        day_str = datetime.fromtimestamp(s.start_time).strftime("%Y-%m-%d")
        projects_by_day[day_str].add(s.project_id)
        total_duration += s.duration_seconds
        
    # Calculate average projects per active day
    active_days = len(projects_by_day)
    if active_days == 0:
        return 0.0
        
    avg_projects = sum(len(p_set) for p_set in projects_by_day.values()) / active_days
    
    # Calculate average session length in minutes
    avg_session_mins = (total_duration / len(sessions)) / 60
    
    # Base score
    score = 6.0
    
    # Penalty: subtract 1.5 points for every project above 1.0 worked on average per day
    switches_penalty = max(0.0, (avg_projects - 1.0) * 1.5)
    score -= switches_penalty
    
    # Bonus: add points for average session length (up to 45 mins = +2.0, up to 90 mins = +4.0)
    duration_bonus = min(4.0, (avg_session_mins / 20.0))
    score += duration_bonus
    
    # Bounded between 0.0 and 10.0, rounded to 1 decimal place
    return round(max(0.0, min(10.0, score)), 1)

def detect_patterns_and_anomalies(sessions: List[Session], projects: List[Project]) -> List[str]:
    """Analyze sessions and commands to generate rule-based developer insights"""
    insights = []
    if not sessions:
        return ["No work data available yet. Start running commands to generate insights!"]
        
    # 1. Busiest and least active days
    day_dist = calculate_day_distribution(sessions)
    active_days = {day: duration for day, duration in day_dist.items() if duration > 0}
    
    if active_days:
        busiest_day = max(active_days.items(), key=lambda x: x[1])
        least_day = min(active_days.items(), key=lambda x: x[1])
        
        busiest_duration = format_duration(busiest_day[1])
        least_duration = format_duration(least_day[1])
        
        insights.append(f"Most productive day: {busiest_day[0]} ({busiest_duration})")
        if busiest_day[0] != least_day[0]:
            insights.append(f"Least active day: {least_day[0]} ({least_duration})")
            
    # 2. Average session duration
    total_seconds = sum(s.duration_seconds for s in sessions)
    avg_session_seconds = int(total_seconds / len(sessions))
    insights.append(f"Your average session duration is {format_duration(avg_session_seconds)} (very consistent)")
    
    # 3. Project focus insights
    time_dist = calculate_time_distribution(sessions, projects)
    if time_dist:
        top_project = time_dist[0]
        insights.append(f"Your longest project focus is on '{top_project[0]}' ({format_duration(top_project[2])})")
        
    # 4. Command patterns
    all_commands = [c for s in sessions for c in s.commands]
    cmd_counts = defaultdict(int)
    for c in all_commands:
        cat = classify_command(c.command)
        cmd_counts[cat] += 1
        
    if cmd_counts:
        sorted_cmds = sorted(cmd_counts.items(), key=lambda x: x[1], reverse=True)
        top_cmd_name = DISPLAY_NAMES.get(sorted_cmds[0][0], sorted_cmds[0][0].capitalize())
        insights.append(f"{top_cmd_name} is your #1 tool ({sorted_cmds[0][1]} executions)")
        
        # Git vs Docker ratio if both exist
        git_count = cmd_counts.get("git", 0)
        docker_count = cmd_counts.get("docker", 0)
        if git_count > 0 and docker_count > 0:
            ratio = round(git_count / docker_count, 1)
            if ratio >= 1.5:
                insights.append(f"You run git {ratio}x more than Docker")
            elif ratio <= 0.7:
                docker_ratio = round(docker_count / git_count, 1)
                insights.append(f"You run docker {docker_ratio}x more than Git")
                
    # 5. Day-of-week context switching anomaly
    # Group sessions by day of week
    switches_by_day = defaultdict(set)
    for s in sessions:
        dt = datetime.fromtimestamp(s.start_time)
        day_name = dt.strftime("%A")
        switches_by_day[day_name].add(s.project_id)
        
    if switches_by_day:
        avg_switches = sum(len(p_set) for p_set in switches_by_day.values()) / len(switches_by_day)
        
        # Check if Friday is particularly focused
        friday_projects = len(switches_by_day.get("Friday", set()))
        if "Friday" in switches_by_day and friday_projects > 0 and friday_projects < avg_switches:
            insights.append("You switch projects less on Fridays compared to other days")
            
    return insights

def calculate_streak(sessions: List[Session]) -> int:
    """Calculate consecutive active work days ending today or on the last active day."""
    if not sessions:
        return 0
    active_dates = {
        datetime.fromtimestamp(s.start_time).date()
        for s in sessions
    }
    if not active_dates:
        return 0
    
    sorted_dates = sorted(list(active_dates), reverse=True)
    streak = 1
    current_date = sorted_dates[0]
    
    # Allow a gap of at most 1 day (e.g. if today is inactive but yesterday was active, streak is still active)
    from termstory.date_utils import get_current_time
    today = get_current_time().date()
    if (today - current_date).days > 1:
        return 0
        
    for d in sorted_dates[1:]:
        if (current_date - d).days == 1:
            streak += 1
            current_date = d
        elif (current_date - d).days > 1:
            break
    return streak

def analyze_all(db=None) -> Dict:
    """Analyze all recorded history to produce total counts, most active periods,
    most used projects, and current coding streak.
    """
    if db is None:
        from termstory.config import get_db_path
        from termstory.database import Database
        db = Database(get_db_path())
        
    conn = db.get_connection()
    try:
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM sessions")
        total_sessions = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM commands")
        total_commands = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM projects")
        total_projects = cursor.fetchone()[0]
        
        cursor.execute("SELECT id, name FROM projects")
        project_names = {row[0]: row[1] for row in cursor.fetchall()}
        
        cursor.execute("""
            SELECT s.id, s.start_time, s.end_time, s.duration_seconds, s.project_id,
                   (SELECT IFNULL(SUM(c.is_legacy) = COUNT(c.id), 0) FROM commands c WHERE c.session_id = s.id) AS is_legacy
            FROM sessions s
        """)
        session_rows = cursor.fetchall()

        cursor.execute("SELECT id, timestamp, command, exit_code, session_id, project_id FROM commands")
        cmd_rows = cursor.fetchall()
        cmds_by_session = defaultdict(list)
        for c_id, ts, cmd_text, exit_code, s_id, p_id in cmd_rows:
            cmds_by_session[s_id].append(Command(id=c_id, timestamp=ts, command=cmd_text, exit_code=exit_code, session_id=s_id, project_id=p_id))
            
        cursor.execute("SELECT hash, timestamp, message, cleaned_message, project_id FROM commits")
        commit_rows = cursor.fetchall()
        commits_by_project = defaultdict(list)
        for hash_val, ts, msg, clean_msg, p_id in commit_rows:
            commits_by_project[p_id].append({
                "hash": hash_val,
                "timestamp": ts,
                "message": msg,
                "cleaned_message": clean_msg
            })
    finally:
        conn.close()
        
    sessions = []
    for row in session_rows:
        s_id, start, end, duration, p_id, is_legacy = row
        s_cmds = cmds_by_session.get(s_id, [])
        s_commits = []
        if p_id is not None:
            p_commits = commits_by_project.get(p_id, [])
            for c in p_commits:
                if start - 300 <= c["timestamp"] <= (end + 600 if end is not None else start + 3600):
                    s_commits.append(c)
                    
        sessions.append(Session(
            id=s_id,
            start_time=start,
            end_time=end,
            duration_seconds=duration,
            project_id=p_id,
            commands=s_cmds,
            commits=s_commits,
            is_legacy=bool(is_legacy)
        ))
        
    real_sessions = [s for s in sessions if not getattr(s, "is_legacy", False)]
    
    # Calculate streak using non-legacy sessions
    streak = calculate_streak(real_sessions)
    
    # Calculate day distribution using non-legacy sessions
    day_dist = calculate_day_distribution(real_sessions)
    if any(day_dist.values()):
        most_active_day = max(day_dist.items(), key=lambda x: x[1])[0]
    else:
        most_active_day = "N/A"
        
    # Calculate time of day distribution using non-legacy sessions
    time_dist = calculate_time_of_day_distribution(real_sessions)
    if any(time_dist.values()):
        most_active_time = max(time_dist.items(), key=lambda x: x[1])[0]
    else:
        most_active_time = "N/A"
        
    # Most used projects (can include all sessions, or real sessions. Let's use all sessions to reflect total time)
    project_durations = defaultdict(int)
    for s in sessions:
        name = "Other"
        if s.project_id is not None:
            raw_name = project_names.get(s.project_id, "Other")
            if raw_name == "General / No Project" or not raw_name:
                name = "Other"
            else:
                name = raw_name
        project_durations[name] += s.duration_seconds
        
    sorted_projects = sorted(project_durations.items(), key=lambda x: x[1], reverse=True)
    
    vampire_metrics = get_vampire_metrics(sessions)
    rpg_info = assign_rpg_class(sessions)
    
    return {
        "total_sessions": total_sessions,
        "total_commands": total_commands,
        "total_projects": total_projects,
        "most_active_day": most_active_day,
        "most_active_time": most_active_time,
        "most_used_projects": sorted_projects,
        "streak": streak,
        "vampire_index": vampire_metrics["vampire_index"],
        "vampire_metrics": vampire_metrics,
        "rpg_class": rpg_info["class_name"],
        "rpg_info": rpg_info
    }


def detect_late_night_chaotic_sessions(db=None) -> List[Dict]:
    """Detect late-night sessions (between 11 PM and 5 AM) that exhibit chaotic characteristics
    (e.g., high command frequency, high failure count, or specific developer desperation patterns).
    """
    if db is None:
        from termstory.config import get_db_path
        from termstory.database import Database
        db = Database(get_db_path())

    conn = db.get_connection()
    try:
        cursor = conn.cursor()

        # Load all sessions
        cursor.execute("""
            SELECT id, start_time, end_time, duration_seconds, project_id
            FROM sessions
            ORDER BY start_time DESC
        """)
        session_rows = cursor.fetchall()

        # Get project names map
        cursor.execute("SELECT id, name FROM projects")
        project_names = {row[0]: row[1] for row in cursor.fetchall()}

        chaotic_sessions = []

        for row in session_rows:
            s_id, start, end, duration, p_id = row
            dt = datetime.fromtimestamp(start)
            hour = dt.hour

            # Late night check: 11 PM (23) to 5 AM (5)
            is_late_night = (hour >= 23 or hour < 5)
            if not is_late_night:
                continue

            # Fetch commands for this session
            cursor.execute("""
                SELECT command, exit_code
                FROM commands
                WHERE session_id = ?
                ORDER BY timestamp ASC
            """, (s_id,))
            cmd_rows = cursor.fetchall()
            if not cmd_rows:
                continue

            commands = [r[0] for r in cmd_rows]
            failed_cmds = [r[0] for r in cmd_rows if r[1] != 0]
            failed_count = len(failed_cmds)
            total_count = len(commands)

            # Chaos score heuristics:
            # 1. Total command count >= 10 (working intensely)
            # 2. Failed command count >= 3 (struggling)
            # 3. Running git commit --amend or similar desperate commands
            has_desperate_command = any("amend" in cmd or "revert" in cmd or "force" in cmd or "reset" in cmd for cmd in commands)

            is_chaotic = (total_count >= 10 or failed_count >= 3 or has_desperate_command)

            if is_chaotic:
                p_name = project_names.get(p_id, "Other")
                if p_name == "General / No Project" or not p_name:
                    p_name = "Other"

                # Fetch commits in session
                commits = []
                if p_id is not None:
                    cursor.execute("""
                        SELECT message
                        FROM commits
                        WHERE project_id = ? AND timestamp >= ? AND timestamp <= ?
                        ORDER BY timestamp ASC
                    """, (p_id, start - 300, end + 600 if end is not None else start + 3600))
                    commits = [r[0] for r in cursor.fetchall()]

                chaotic_sessions.append({
                    "session_id": s_id,
                    "start_time": start,
                    "end_time": end,
                    "duration_seconds": duration,
                    "project_name": p_name,
                    "commands": commands,
                    "failed_commands": failed_cmds,
                    "commits": commits,
                    "hour": hour
                })

        return chaotic_sessions
    finally:
        conn.close()


def calculate_vampire_coder_index(sessions: List[Session]) -> float:
    """Calculate the percentage of commands and commits executed between midnight and 5:00 AM."""
    total_count = 0
    vampire_count = 0
    seen_commits = set()  # Track commit timestamps to avoid double-counting
    for s in sessions:
        for cmd in s.commands:
            total_count += 1
            dt = datetime.fromtimestamp(cmd.timestamp)
            if 0 <= dt.hour < 5:
                vampire_count += 1
        for commit in s.commits:
            ts = commit.get("timestamp")
            if ts and ts not in seen_commits:
                total_count += 1
                seen_commits.add(ts)
                dt = datetime.fromtimestamp(ts)
                if 0 <= dt.hour < 5:
                    vampire_count += 1
    if total_count == 0:
        return 0.0
    return round((vampire_count / total_count) * 100, 1)


def get_vampire_metrics(sessions: List[Session]) -> Dict[str, Any]:
    """Calculate detailed Vampire Coder Index metrics."""
    total_commands = 0
    vampire_commands = 0
    total_commits = 0
    vampire_commits = 0
    for s in sessions:
        for cmd in s.commands:
            total_commands += 1
            dt = datetime.fromtimestamp(cmd.timestamp)
            if 0 <= dt.hour < 5:
                vampire_commands += 1
        for commit in s.commits:
            total_commits += 1
            ts = commit.get("timestamp")
            if ts:
                dt = datetime.fromtimestamp(ts)
                if 0 <= dt.hour < 5:
                    vampire_commits += 1
    total = total_commands + total_commits
    vampire = vampire_commands + vampire_commits
    index = round((vampire / total) * 100, 1) if total > 0 else 0.0
    return {
        "vampire_index": index,
        "vampire_commands": vampire_commands,
        "total_commands": total_commands,
        "vampire_commits": vampire_commits,
        "total_commits": total_commits,
    }


def assign_rpg_class(sessions: List[Session]) -> Dict[str, Any]:
    """Assign a daily RPG class based on command usage patterns."""
    counts = {
        "Regex Sorcerer": 0,
        "Docker Demolitionist": 0,
        "Git Paladin": 0,
        "Frontend Bard": 0,
        "Python Alchemist": 0,
        "Database Necromancer": 0,
        "Systems Ranger": 0,
    }
    
    total_commands = 0
    for s in sessions:
        for cmd in s.commands:
            total_commands += 1
            cmd_text = cmd.command.strip()
            lower_cmd = cmd_text.lower()
            
            # Check for Regex Sorcerer: pipe or grep, awk, sed
            if "|" in cmd_text or any(x in lower_cmd.split() for x in ["grep", "awk", "sed"]):
                counts["Regex Sorcerer"] += 1
            # Check for Docker Demolitionist: docker, docker-compose, podman
            elif any(x in lower_cmd.split() for x in ["docker", "docker-compose", "podman"]):
                counts["Docker Demolitionist"] += 1
            # Check for Git Paladin: git, gh
            elif any(x in lower_cmd.split() for x in ["git", "gh"]):
                counts["Git Paladin"] += 1
            # Check for Frontend Bard: npm, yarn, pnpm, npx
            elif any(x in lower_cmd.split() for x in ["npm", "yarn", "pnpm", "npx"]):
                counts["Frontend Bard"] += 1
            # Check for Python Alchemist: python, python3, pytest, poetry, pip
            elif any(x in lower_cmd.split() for x in ["python", "python3", "pytest", "poetry", "pip"]):
                counts["Python Alchemist"] += 1
            # Check for Database Necromancer: sqlite3, psql, mysql, mongo, prisma, sql
            elif any(x in lower_cmd.split() for x in ["sqlite3", "psql", "mysql", "mongo", "prisma", "sql"]):
                counts["Database Necromancer"] += 1
            # Check for Systems Ranger: make, cmake, gcc, clang, cargo, rustc, go
            elif any(x in lower_cmd.split() for x in ["make", "cmake", "gcc", "clang", "cargo", "rustc", "go"]):
                counts["Systems Ranger"] += 1

    # Find the dominant class
    dominant_class = "Terminal Nomad"
    max_count = 0
    
    for cls, count in counts.items():
        if count > max_count:
            max_count = count
            dominant_class = cls
            
    # Default descriptions/titles
    descriptions = {
        "Regex Sorcerer": "You spend your days piping streams and filtering logs. Magic is real, and it's written in regex.",
        "Docker Demolitionist": "Containers rise and fall at your command. You compose environments and smash dependencies.",
        "Git Paladin": "A defender of the commit history, keeping branches clean and merging with honor.",
        "Frontend Bard": "Weaving HTML, CSS, and JS into modern masterpieces. NPM packages are your spells.",
        "Python Alchemist": "Transmuting simple scripts into elegant solutions, one list comprehension at a time.",
        "Database Necromancer": "Summoning schemas and querying the ancient tables of databases.",
        "Systems Ranger": "Tracking low-level build processes and hunting compile-time warnings.",
        "Terminal Nomad": "Wandering through directories and running miscellaneous commands."
    }
    
    return {
        "class_name": dominant_class,
        "description": descriptions.get(dominant_class, ""),
        "max_count": max_count,
        "total_commands": total_commands,
        "counts": counts
    }



