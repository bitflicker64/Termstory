import json
import os
import time
import re
from typing import List, Dict, Optional, Tuple
from termstory.config import get_app_dir

def get_reminders_file_path() -> str:
    """Return path to reminders JSON file"""
    return os.path.join(get_app_dir("data"), "reminders.json")

def load_reminders() -> List[Dict]:
    """Load all reminders from the JSON file"""
    path = get_reminders_file_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def save_reminders(reminders: List[Dict]) -> None:
    """Save all reminders to the JSON file"""
    path = get_reminders_file_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(reminders, f, indent=4)

def parse_reminder_text(text: str) -> Tuple[str, int]:
    """Parse a phrase like 'remind me about X in N days' or 'X in N days'
    to extract description X and days N.
    """
    text = re.sub(r'\s+', ' ', text.strip())
    
    # Pattern 1: (remind me )?(about|to) <X> in <N> day(s)
    pattern1 = re.compile(
        r"^(?:remind\s+me\s+)?(?:about|to)\s+(.+?)\s+in\s+(\d+)\s+days?$",
        re.IGNORECASE
    )
    m1 = pattern1.match(text)
    if m1:
        return m1.group(1).strip(), int(m1.group(2))
        
    # Pattern 2: <X> in <N> day(s)
    pattern2 = re.compile(r"^(.+?)\s+in\s+(\d+)\s+days?$", re.IGNORECASE)
    m2 = pattern2.match(text)
    if m2:
        return m2.group(1).strip(), int(m2.group(2))
        
    raise ValueError(
        "Could not parse reminder phrase. Please use format like "
        "'remind me about X in N days' or 'X in N days'."
    )

def add_reminder(
    text: str,
    days: Optional[int] = None,
    db = None
) -> Dict:
    """Parse, create, and save a new reminder.
    Associates the reminder with the latest session in the database if available.
    """
    if days is not None:
        # Strip trailing 'in N days' if present in the text to avoid redundancy
        m = re.match(r"^(.+?)\s+in\s+(\d+)\s+days?$", text, re.IGNORECASE)
        if m:
            about = m.group(1).strip()
        else:
            about = text
    else:
        about, days = parse_reminder_text(text)
        
    if days < 0:
        raise ValueError("Days must be a non-negative integer.")

    created_at = int(time.time())
    due_at = created_at + (days * 86400)
    
    # Get latest session if database is provided
    session_id = None
    project_name = "Other"
    
    if db is not None:
        conn = db.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT s.id, p.name
                FROM sessions s
                LEFT JOIN projects p ON s.project_id = p.id
                ORDER BY s.start_time DESC
                LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                session_id = row[0]
                project_name = row[1] or "Other"
        except Exception:
            pass
        finally:
            conn.close()

    reminders = load_reminders()
    
    # Generate next ID
    existing_ids = [r.get("id") for r in reminders if isinstance(r.get("id"), int)]
    next_id = max(existing_ids) + 1 if existing_ids else 1
    
    new_reminder = {
        "id": next_id,
        "about": about,
        "days": days,
        "created_at": created_at,
        "due_at": due_at,
        "session_id": session_id,
        "project_name": project_name,
        "status": "pending"
    }
    
    reminders.append(new_reminder)
    save_reminders(reminders)
    return new_reminder

def complete_reminder(reminder_id: int) -> bool:
    """Mark a reminder as completed"""
    reminders = load_reminders()
    updated = False
    for r in reminders:
        if r.get("id") == reminder_id:
            r["status"] = "completed"
            updated = True
            break
            
    if updated:
        save_reminders(reminders)
    return updated


def cluster_commands(commands: List[str]) -> List[List[str]]:
    """Cluster similar commands using sentence-transformers embeddings."""
    import math
    if not commands:
        return []
        
    # Clean and deduplicate commands
    unique_cmds = []
    seen = set()
    for cmd in commands:
        cleaned = cmd.strip()
        if cleaned and cleaned not in seen:
            unique_cmds.append(cleaned)
            seen.add(cleaned)
            
    if not unique_cmds:
        return []
        
    # Attempt to use sentence-transformers
    try:
        from termstory.rag import get_embeddings, SENTENCE_TRANSFORMERS_AVAILABLE
    except ImportError:
        SENTENCE_TRANSFORMERS_AVAILABLE = False

    if not SENTENCE_TRANSFORMERS_AVAILABLE:
        # Fallback: group by first word
        verb_clusters = {}
        for cmd in unique_cmds:
            verb = cmd.split()[0] if cmd.split() else "other"
            if verb not in verb_clusters:
                verb_clusters[verb] = []
            verb_clusters[verb].append(cmd)
        return list(verb_clusters.values())

    try:
        embeddings = get_embeddings(unique_cmds)
        # Convert to list of lists if numpy array
        if hasattr(embeddings, "tolist"):
            emb_list = embeddings.tolist()
        else:
            emb_list = embeddings
    except Exception:
        # Fallback if encoding failed
        verb_clusters = {}
        for cmd in unique_cmds:
            verb = cmd.split()[0] if cmd.split() else "other"
            if verb not in verb_clusters:
                verb_clusters[verb] = []
            verb_clusters[verb].append(cmd)
        return list(verb_clusters.values())
        
    # Simple leader clustering
    clusters = []
    cluster_embs = []
    threshold = 0.6
    
    for cmd, emb in zip(unique_cmds, emb_list):
        placed = False
        for i, (cluster, c_emb) in enumerate(zip(clusters, cluster_embs)):
            dot = sum(x * y for x, y in zip(emb, c_emb))
            norm1 = math.sqrt(sum(x * x for x in emb))
            norm2 = math.sqrt(sum(x * x for x in c_emb))
            sim = dot / (norm1 * norm2) if (norm1 > 0 and norm2 > 0) else 0.0
            
            if sim >= threshold:
                new_center = []
                for x, y in zip(c_emb, emb):
                    new_center.append((x * len(cluster) + y) / (len(cluster) + 1))
                cluster_embs[i] = new_center
                cluster.append(cmd)
                placed = True
                break
        if not placed:
            clusters.append([cmd])
            cluster_embs.append(emb)
            
    return clusters


def generate_cluster_summary(commands: List[str]) -> str:
    """Generate a single-line, high-density summary of a command cluster."""
    from termstory.config import load_config, get_config_value
    config = load_config()
    provider = config.get("active_provider", "disabled")
    
    if provider == "disabled":
        unique = []
        for c in commands:
            base = c.split()[0] if c.strip() else ""
            if base and base not in unique:
                unique.append(base)
        if not unique:
            return "Idle session"
        return f"Worked on commands: {', '.join(unique[:3])}"

    # Query LLM
    from termstory.ai import _send_llm_request
    prompt = (
        "You are a developer memory engine. Summarize the following cluster of raw terminal commands "
        "into a single-line, high-density, tech-dense summary of what the developer was doing (e.g. 'Set up Docker container and verified logs').\n\n"
        "Commands:\n" + "\n".join(f"- {c}" for c in commands) + "\n\n"
        "Return ONLY the single line summary. No markdown formatting, no conversational filler, and no surrounding quotes."
    )
    
    api_key = get_config_value(config, f"providers.{provider}.api_key") or ""
    api_base_url = get_config_value(config, f"providers.{provider}.api_base_url") or ""
    model_name = get_config_value(config, f"providers.{provider}.model_name") or ""
    
    summary = _send_llm_request(
        prompt, api_key, api_base_url, model_name, provider,
        max_tokens=100, timeout=15.0
    )
    if summary:
        from rich.markup import escape
        return escape(summary.strip())
    
    # Fallback if request failed
    unique = []
    for c in commands:
        base = c.split()[0] if c.strip() else ""
        if base and base not in unique:
            unique.append(base)
    return f"Worked on commands: {', '.join(unique[:3])}"


def consolidate_sleep_contexts(db, force: bool = False) -> int:
    """Detect idle periods (30+ min gaps in command history or since last command)
    and consolidate command contexts into summaries.
    """
    # 1. Get the last consolidated end_time
    conn = db.get_connection()
    last_end = 0
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(end_time) FROM rem_sleep_consolidation")
        row = cursor.fetchone()
        if row and row[0] is not None:
            last_end = row[0]
    except Exception:
        pass
    finally:
        conn.close()

    # 2. Fetch all commands since last_end ordered by timestamp ASC
    conn = db.get_connection()
    commands = []
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT command, timestamp
            FROM commands
            WHERE timestamp > ?
            ORDER BY timestamp ASC
        """, (last_end,))
        rows = cursor.fetchall()
        for r in rows:
            commands.append({"command": r[0], "timestamp": r[1]})
    except Exception:
        pass
    finally:
        conn.close()

    if not commands:
        return 0

    # 3. Group commands into chunks separated by gaps of >= 1800 seconds (30 minutes)
    chunks = []
    current_chunk = [commands[0]]
    
    for cmd in commands[1:]:
        if (cmd["timestamp"] - current_chunk[-1]["timestamp"]) >= 1800:
            chunks.append(current_chunk)
            current_chunk = [cmd]
        else:
            current_chunk.append(cmd)
    if current_chunk:
        chunks.append(current_chunk)

    # 4. Filter chunks followed by an idle period
    now = int(time.time())
    chunks_to_consolidate = []
    
    for i, chunk in enumerate(chunks):
        is_last = (i == len(chunks) - 1)
        if not is_last:
            chunks_to_consolidate.append(chunk)
        else:
            if force or (now - chunk[-1]["timestamp"] >= 1800):
                chunks_to_consolidate.append(chunk)

    if not chunks_to_consolidate:
        return 0

    # 5. Consolidate each chunk
    consolidated_count = 0
    for chunk in chunks_to_consolidate:
        start_time = chunk[0]["timestamp"]
        end_time = chunk[-1]["timestamp"]
        cmd_strs = [c["command"] for c in chunk]
        
        clusters = cluster_commands(cmd_strs)
        cluster_summaries = []
        for cluster in clusters:
            summ = generate_cluster_summary(cluster)
            if summ:
                cluster_summaries.append(summ)
                
        if not cluster_summaries:
            continue
            
        if len(cluster_summaries) == 1:
            final_summary = cluster_summaries[0]
        else:
            final_summary = " | ".join(cluster_summaries)
            
        db.save_consolidated_context(start_time, end_time, final_summary, cmd_strs)
        consolidated_count += 1

    return consolidated_count


def start_sleep_daemon(db_path: str):
    """Spawns the sleep daemon in the background if it's not already running."""
    import sys
    import subprocess
    
    pid_file = os.path.join(get_app_dir("data"), "sleep_daemon.pid")
    if os.path.exists(pid_file):
        try:
            with open(pid_file, "r") as f:
                pid = int(f.read().strip())
            os.kill(pid, 0)
            return # Already running
        except (ValueError, OSError):
            pass
            
    # Inherit and configure the python path
    env = os.environ.copy()
    package_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if "PYTHONPATH" in env:
        env["PYTHONPATH"] = package_root + os.pathsep + env["PYTHONPATH"]
    else:
        env["PYTHONPATH"] = package_root

    try:
        subprocess.Popen(
            [sys.executable, "-c", f"from termstory.reminder import run_sleep_daemon; run_sleep_daemon({repr(db_path)})"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=env
        )
    except Exception:
        pass


def run_sleep_daemon(db_path: str):
    """Run a daemon loop checking for idle periods and consolidating contexts."""
    import sys
    from termstory.database import Database
    
    pid_file = os.path.join(get_app_dir("data"), "sleep_daemon.pid")
    try:
        with open(pid_file, "w") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass
        
    db = Database(db_path)
    while True:
        try:
            consolidate_sleep_contexts(db, force=False)
        except Exception:
            pass
        time.sleep(300)
