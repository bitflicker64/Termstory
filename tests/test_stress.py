import time
import random
import threading
import pytest
from termstory.database import Database
from termstory.models import Command, Session, Project
from termstory.search import advanced_search

def test_stress_and_concurrency(tmp_path):
    db_file = tmp_path / "stress_test.db"
    db = Database(str(db_file))
    db.init_db()

    # Generate 5 projects
    projects = [
        Project(id=i, name=f"Proj {i}", path=f"~/proj-{i}", first_seen=0, last_seen=0, session_count=0, total_time=0)
        for i in range(1, 6)
    ]

    # Step 1: Massive history simulation (10,000+ commands, 500+ sessions, 2 years)
    base_ts = int(time.time()) - 2 * 365 * 24 * 3600
    sessions = []
    commands = []
    
    for i in range(500):
        p = random.choice(projects)
        # Ensure start time increases to avoid dedup merge
        s_start = base_ts + i * 3600 * 4 + random.randint(0, 100)
        s_end = s_start + random.randint(60, 600)
        dur = s_end - s_start
        
        s_cmds = []
        for j in range(20):
            cmd_ts = s_start + int(j * (dur / 19.0))
            cmd = Command(
                timestamp=cmd_ts,
                command=f"echo 'stress test {i}-{j}' && git checkout main",
                exit_code=0,
                session_id=i + 1,
                project_id=p.id
            )
            commands.append(cmd)
            s_cmds.append(cmd)
            
        session = Session(
            id=i + 1,
            start_time=s_start,
            end_time=s_end,
            duration_seconds=dur,
            project_id=p.id,
            commands=s_cmds,
            ai_summary=f"AI Summary for stress session {i}"
        )
        sessions.append(session)

    # Bulk insert
    db.save_data(projects, sessions, commands)

    # Verify ingestion was successful
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM commands;")
    cmd_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM sessions;")
    sess_count = cursor.fetchone()[0]
    conn.close()

    assert cmd_count >= 10000, "Should have 10,000+ commands"
    assert sess_count >= 500, "Should have 500+ sessions"

    # Step 2: Concurrent reader/writer tests
    errors = []
    stop_event = threading.Event()

    def writer_worker():
        # Periodically inserts new commands and sessions
        session_idx = 1000
        while not stop_event.is_set():
            try:
                p = random.choice(projects)
                s_start = int(time.time()) - random.randint(0, 10000)
                cmd = Command(
                    timestamp=s_start,
                    command=f"git commit -m 'new concurrent command {session_idx}'",
                    exit_code=0,
                    session_id=session_idx,
                    project_id=p.id
                )
                session = Session(
                    id=session_idx,
                    start_time=s_start,
                    end_time=s_start + 10,
                    duration_seconds=10,
                    project_id=p.id,
                    commands=[cmd]
                )
                db.save_data([p], [session], [cmd])
                session_idx += 1
                time.sleep(0.02)
            except Exception as e:
                errors.append(("writer", e))
                break

    def ai_updater_worker():
        # Periodically updates AI summaries of random sessions
        while not stop_event.is_set():
            try:
                s_id = random.randint(1, 500)
                db.save_session_ai_summary(s_id, f"Concurrently updated summary {random.random()}")
                time.sleep(0.02)
            except Exception as e:
                errors.append(("ai_updater", e))
                break

    def search_worker():
        # Periodically runs advanced_search queries
        queries = ["echo", "stress", "git", "main", "concurrent"]
        while not stop_event.is_set():
            try:
                q = random.choice(queries)
                db.search_sessions(q)
                time.sleep(0.02)
            except Exception as e:
                errors.append(("search", e))
                break

    def stats_worker():
        # Periodically reads stats/projects
        while not stop_event.is_set():
            try:
                db.get_all_projects_with_stats()
                db.get_today_sessions()
                time.sleep(0.02)
            except Exception as e:
                errors.append(("stats", e))
                break

    threads = [
        threading.Thread(target=writer_worker),
        threading.Thread(target=ai_updater_worker),
        threading.Thread(target=search_worker),
        threading.Thread(target=stats_worker),
    ]

    for t in threads:
        t.daemon = True
        t.start()

    # Let them run for 5 seconds
    time.sleep(5)
    stop_event.set()

    for t in threads:
        t.join(timeout=2.0)

    # Check for errors/deadlocks
    if errors:
        for source, err in errors:
            print(f"Error in thread {source}: {err}")
    assert len(errors) == 0, f"Concurrent threads raised errors: {errors}"
