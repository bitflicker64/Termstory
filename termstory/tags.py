import re
from typing import List, Dict, Optional
from termstory.models import Session

TAG_RULES = {
    "deploy": {
        "cmd_patterns": [
            r"\bdeploy(ment)?\b", r"\bpublish\b", r"\brelease\b", r"\bkubectl\b", r"\bhelm\b",
            r"\bvercel\b", r"\bflyctl\b", r"\bgcloud\b", r"\baws s3 sync\b", r"\bcapistrano\b",
            r"\bshipit\b", r"\bterraform apply\b", r"\bgh release\b", r"\bdocker push\b"
        ],
        "commit_patterns": [
            r"\bdeploy(ment)?\b", r"\bpublish\b", r"\brelease\b", r"\bship\b", r"\bprod(uction)?\b"
        ]
    },
    "debug": {
        "cmd_patterns": [
            r"\bdebug(ger)?\b", r"\bpdb\b", r"\bipdb\b", r"\blldb\b", r"\bgdb\b",
            r"\btrace\b", r"\bvalgrind\b", r"\bstrace\b", r"\bprofile(r)?\b",
            r"\bpyinstrument\b",
            r"\btcpdump\b", r"\bdoctor\b", r"\bdiagnose\b", r"\bdiagnostic\b",
            r"\bjournalctl\b", r"\bdmesg\b", r"\bdocker logs\b"
        ],
        "commit_patterns": [
            r"\bdebug(ger)?\b", r"\bfix\b", r"\bbug\b", r"\bissue\b", r"\bresolve(d)?\b",
            r"\bcrash(ed)?\b", r"\berror\b", r"\bworkaround\b"
        ]
    },
    "setup": {
        "cmd_patterns": [
            r"\bsetup\b", r"\binstall\b", r"\binit\b", r"\bcreate\b", r"\bbuild\b",
            r"\bconfigure\b", r"\bmake\b", r"\bcmake\b", r"\bclone\b", r"\bvenv\b",
            r"\bvirtualenv\b", r"\bpip\b", r"\bnpm\b", r"\byarn\b", r"\bpnpm\b",
            r"\bpoetry\b", r"\bcargo\b"
        ],
        "commit_patterns": [
            r"\bsetup\b", r"\binit\b", r"\binstall\b", r"\bconfigure\b", r"\badd\b",
            r"\bcreate\b", r"\bconfig\b", r"\bdeps\b", r"\bdependenc(y|ies)\b"
        ]
    },
    "test": {
        "cmd_patterns": [
            r"\btest(s|ing)?\b", r"\bpytest\b", r"\bunittest\b", r"\bjest\b", r"\bmocha\b",
            r"\btox\b", r"\bcoverage\b", r"\brspec\b", r"\bcypress\b", r"\bplaywright\b"
        ],
        "commit_patterns": [
            r"\btest(s|ing)?\b", r"\bspec\b", r"\bcoverage\b", r"\bassert\b"
        ]
    },
    "docs": {
        "cmd_patterns": [
            r"\bdocs?\b", r"\bdocumentation\b", r"\breadme\b", r"\bwiki\b", r"\bchangelog\b",
            r"\bsphinx\b", r"\bmkdocs\b", r"\bmarkdown\b", r"\bmd\b", r"\bman\b", r"\btldr\b",
            r"\bhelp\b"
        ],
        "commit_patterns": [
            r"\bdocs?\b", r"\bdocumentation\b", r"\breadme\b", r"\bwiki\b", r"\bchangelog\b",
            r"\btypo\b", r"\bcomment\b"
        ]
    }
}

# Compile patterns
for tag, rules in TAG_RULES.items():
    rules["cmd_compiled"] = [re.compile(p, re.IGNORECASE) for p in rules["cmd_patterns"]]
    rules["commit_compiled"] = [re.compile(p, re.IGNORECASE) for p in rules["commit_patterns"]]


def compute_tags_from_text(commands: List[str], commits: List[Dict]) -> List[str]:
    matched_tags = set()
    
    # 1. Match commands
    for cmd in commands:
        cmd_lower = cmd.lower()
        for tag, rules in TAG_RULES.items():
            if tag in matched_tags:
                continue
            for pattern in rules["cmd_compiled"]:
                if pattern.search(cmd_lower):
                    matched_tags.add(tag)
                    break

    # 2. Match commits
    for commit in commits:
        msgs = []
        if "message" in commit and commit["message"]:
            msgs.append(commit["message"].lower())
        if "cleaned_message" in commit and commit["cleaned_message"]:
            msgs.append(commit["cleaned_message"].lower())
            
        for msg in msgs:
            for tag, rules in TAG_RULES.items():
                if tag in matched_tags:
                    continue
                for pattern in rules["commit_compiled"]:
                    if pattern.search(msg):
                        matched_tags.add(tag)
                        break
                        
    # Retain the exact order of tags: deploy, debug, setup, test, docs
    ordered_tags = ["deploy", "debug", "setup", "test", "docs"]
    return [t for t in ordered_tags if t in matched_tags]


def auto_tag_all_sessions(db, force: bool = False) -> None:
    """Read sessions, evaluate their commands and commits to compute tags, and save back to the DB in bulk."""
    conn = db.get_connection()
    try:
        cursor = conn.cursor()
        
        # 1. Fetch sessions
        if force:
            cursor.execute("SELECT id, start_time, end_time, project_id FROM sessions")
        else:
            cursor.execute("SELECT id, start_time, end_time, project_id FROM sessions WHERE tags IS NULL")
            
        sessions_data = cursor.fetchall()
        if not sessions_data:
            return

        session_ids = [row[0] for row in sessions_data]
        project_ids = list({row[3] for row in sessions_data if row[3] is not None})

        commands_by_session = {}
        commits_by_project = {}

        # 2. Fetch commands and commits, querying selectively for performance if not forcing all
        if len(session_ids) > 500 or force:
            # Fetch all commands
            cursor.execute("SELECT session_id, command FROM commands WHERE session_id IS NOT NULL")
            for s_id, cmd in cursor.fetchall():
                if s_id not in commands_by_session:
                    commands_by_session[s_id] = []
                commands_by_session[s_id].append(cmd)

            # Fetch all commits
            cursor.execute("SELECT project_id, timestamp, message, cleaned_message FROM commits WHERE project_id IS NOT NULL")
            for p_id, ts, msg, cl_msg in cursor.fetchall():
                if p_id not in commits_by_project:
                    commits_by_project[p_id] = []
                commits_by_project[p_id].append({"timestamp": ts, "message": msg, "cleaned_message": cl_msg})
        else:
            # Fetch commands for relevant sessions only
            for i in range(0, len(session_ids), 500):
                chunk = session_ids[i:i+500]
                placeholders = ",".join("?" for _ in chunk)
                cursor.execute(f"SELECT session_id, command FROM commands WHERE session_id IN ({placeholders})", chunk)
                for s_id, cmd in cursor.fetchall():
                    if s_id not in commands_by_session:
                        commands_by_session[s_id] = []
                    commands_by_session[s_id].append(cmd)

            # Fetch commits for relevant projects only
            if project_ids:
                for i in range(0, len(project_ids), 500):
                    chunk = project_ids[i:i+500]
                    placeholders = ",".join("?" for _ in chunk)
                    cursor.execute(f"SELECT project_id, timestamp, message, cleaned_message FROM commits WHERE project_id IN ({placeholders})", chunk)
                    for p_id, ts, msg, cl_msg in cursor.fetchall():
                        if p_id not in commits_by_project:
                            commits_by_project[p_id] = []
                        commits_by_project[p_id].append({"timestamp": ts, "message": msg, "cleaned_message": cl_msg})

        # 3. For each session, gather its commands and matching commits, compute tags, and update
        updates = []
        for s_id, start_time, end_time, p_id in sessions_data:
            cmds = commands_by_session.get(s_id, [])
            matching_commits = []
            if p_id is not None:
                project_commits = commits_by_project.get(p_id, [])
                # Buffer: 5m pre, 10m post (same as database.py)
                start_buf = start_time - 300
                end_buf = (end_time if end_time is not None else start_time) + 600
                for commit in project_commits:
                    if start_buf <= commit["timestamp"] <= end_buf:
                        matching_commits.append(commit)

            tags = compute_tags_from_text(cmds, matching_commits)
            tags_str = ",".join(tags) if tags else ""
            updates.append((tags_str, s_id))

        # 4. Bulk update tags
        if updates:
            cursor.execute("BEGIN IMMEDIATE;")
            cursor.executemany("""
                UPDATE sessions SET tags = ? WHERE id = ?
            """, updates)
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
