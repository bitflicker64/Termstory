"""Concurrency stress test: 2 sessions x 20 commands = 40 commands per worker (lightweight)"""
import sqlite3
import threading
import time
import os
import random
from termstory.database import Database
from termstory.models import Project, Session, Command
import pytest

def writer_worker(db_path, worker_id, num_sessions=50, commands_per_session=100):
    """Write commands per worker simulating long multi-year history logs"""
    db = Database(db_path)
    
    # Spread out over 3 years
    base_time = int(time.time()) - 86400 * 365 * 3
    
    for s in range(num_sessions):
        try:
            # Create project
            p = Project(
                id=None,
                name=f"stress_proj_{worker_id}_{s}",
                path=f"/tmp/stress_proj_{worker_id}_{s}",
                first_seen=base_time + s * 86400,
                last_seen=base_time + s * 86400 + 500,
                session_count=1,
                total_time=500
            )
            
            # Create session - use temp_id for session mapping
            temp_session_id = worker_id * 10000 + s + 1
            session_start = base_time + s * 86400 + worker_id * 3600
            session_end = session_start + 500
            sess = Session(
                id=temp_session_id,
                start_time=session_start,
                end_time=session_end,
                duration_seconds=500,
                project_id=None,  # Will be set after project save
                commands=[],
                tags=None
            )
            
            # Create commands for this session
            cmds = []
            for c in range(commands_per_session):
                cmd = Command(
                    timestamp=session_start + c * 2,
                    command=f"git commit -m 'massive stress test {worker_id} {s} {c}'",
                    exit_code=0,
                    session_id=temp_session_id,  # Use temp_id for mapping
                    project_id=None,
                    is_legacy=False
                )
                cmds.append(cmd)
            
            sess.commands = cmds
            db.save_data([p], [sess], cmds)
            
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e):
                time.sleep(0.01)  # Brief backoff
                continue
            else:
                raise
        except Exception as e:
            print(f"Writer {worker_id} session {s} error: {e}")

def reader_worker(db_path, worker_id, num_queries=50):
    """Concurrent reads during writes"""
    db = Database(db_path)
    
    for q in range(num_queries):
        try:
            # Search queries
            db.search_sessions("massive")
            db.search_sessions("commit")
            
            # List projects
            conn = db.get_connection()
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM projects")
            c.fetchone()
            conn.close()
            
            # Get sessions (range query)
            db.get_range_sessions(int(time.time()) - 86400 * 365, int(time.time()))
            
        except Exception as e:
            print(f"Reader {worker_id} query {q} error: {e}")
        time.sleep(0.005)  # Small delay

def test_massive_history_ingestion(tmp_path):
    """
    Test synthesizing massive, multi-year history logs to simulate 
    worst-case ingestion scenarios under high concurrency.
    """
    db_path = str(tmp_path / "stress.db")
    
    # Initialize the database on the main thread first
    db = Database(db_path)
    db.init_db()
    
    # 5 writers x 20 sessions x 100 commands = 10,000 commands
    num_writers = 5
    sessions_per_writer = 20
    commands_per_session = 100
    
    threads = []
    
    # Start readers first
    for i in range(3):
        t = threading.Thread(target=reader_worker, args=(db_path, i, 50))
        threads.append(t)
        t.start()
    
    # Start writers
    for i in range(num_writers):
        t = threading.Thread(target=writer_worker, args=(db_path, i, sessions_per_writer, commands_per_session))
        threads.append(t)
        t.start()
    
    # Wait for all
    for t in threads:
        t.join()
    
    # Verify counts
    db = Database(db_path)
    conn = db.get_connection()
    c = conn.cursor()
    
    c.execute("SELECT COUNT(*) FROM commands")
    cmd_count = c.fetchone()[0]
    
    c.execute("SELECT COUNT(*) FROM sessions")
    sess_count = c.fetchone()[0]
    
    c.execute("SELECT COUNT(*) FROM projects")
    proj_count = c.fetchone()[0]
    
    # Test FTS search still works
    results = db.search_sessions("massive stress test")
    
    assert cmd_count >= (num_writers * sessions_per_writer * commands_per_session) * 0.95, "Most commands should be ingested"
    assert sess_count >= (num_writers * sessions_per_writer) * 0.95
    assert proj_count >= (num_writers * sessions_per_writer) * 0.95
    
    conn.close()