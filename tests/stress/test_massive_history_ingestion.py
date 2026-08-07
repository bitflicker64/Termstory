"""Concurrency stress test: 5 writers x 20 sessions x 100 commands = 10,000 commands (massive)"""
import sqlite3
import threading
import time
import os
import random
from datetime import datetime
from termstory.database import Database
from termstory.models import Project, Session, Command
def writer_worker(db_path, base_path, worker_id, num_sessions, commands_per_session, errors):
    """Write commands per worker simulating long multi-year history logs"""
    db = Database(db_path)
    
    # Spread out over 3 years
    base_time = int(time.time()) - 86400 * 365 * 3
    
    for s in range(num_sessions):
        retries = 5
        success = False
        while retries > 0 and not success:
            try:
                # Create project
                p = Project(
                    id=None,
                    name=f"stress_proj_{worker_id}_{s}",
                    path=os.path.join(base_path, f"stress_proj_{worker_id}_{s}"),
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
                success = True
                
            except sqlite3.OperationalError as e:
                if "database is locked" in str(e):
                    retries -= 1
                    time.sleep(0.01)  # Brief backoff
                else:
                    errors.append(f"Writer {worker_id} db error: {e}")
                    break
            except Exception as e:
                errors.append(f"Writer {worker_id} error: {e}")
                break
                
        if not success:
            errors.append(f"Writer {worker_id} failed to save session {s} after retries")

def reader_worker(db_path, worker_id, num_queries, errors):
    """Concurrent reads during writes"""
    db = Database(db_path)
    
    for q in range(num_queries):
        try:
            # Search queries
            db.search_sessions("massive")
            db.search_sessions("commit")
            
            # List projects
            conn = db.get_connection()
            try:
                c = conn.cursor()
                c.execute("SELECT COUNT(*) FROM projects")
                c.fetchone()
            finally:
                conn.close()
            
            # Get sessions (range query) covers all 3 years
            db.get_range_sessions(int(time.time()) - 86400 * 365 * 3, int(time.time()))
            
        except Exception as e:
            errors.append(f"Reader {worker_id} error: {e}")
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
    
    errors = []
    
    # Start readers first
    for i in range(3):
        t = threading.Thread(target=reader_worker, args=(db_path, i, 50, errors))
        threads.append(t)
        t.start()
    
    # Start writers
    for i in range(num_writers):
        t = threading.Thread(target=writer_worker, args=(db_path, str(tmp_path), i, sessions_per_writer, commands_per_session, errors))
        threads.append(t)
        t.start()
    
    # Wait for all
    for t in threads:
        t.join()
    
    # Verify counts
    db = Database(db_path)
    conn = db.get_connection()
    try:
        c = conn.cursor()
        
        c.execute("SELECT COUNT(*) FROM commands")
        cmd_count = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM sessions")
        sess_count = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM projects")
        proj_count = c.fetchone()[0]
        
        
        # Test FTS search still works
        results = db.search_sessions("massive stress test")
        assert len(results) > 0, "FTS should return results after concurrent ingestion"
        
        assert len(errors) == 0, f"Concurrency errors detected: {errors}"
        
        assert cmd_count == (num_writers * sessions_per_writer * commands_per_session), "All commands should be ingested"
        assert sess_count == (num_writers * sessions_per_writer), "All sessions should be ingested"
        assert proj_count == (num_writers * sessions_per_writer), "All projects should be ingested"
    finally:
        conn.close()

def test_archive_batched_inserts_timing(tmp_path, monkeypatch):
    """Archive a large history fast enough that a per-command INSERT regression is obvious."""
    from termstory.archive import archive_old_data

    monkeypatch.setenv("TERMSTORY_DATE_OVERRIDE", "2026-06-14 12:00:00")
    db_path = str(tmp_path / "archive_stress.db")
    archive_db_path = str(tmp_path / "archive_stress_out.db")

    db = Database(db_path)
    db.init_db()

    # 50 sessions x 200 commands = 10,000 commands — enough to expose the old loop.
    num_sessions = 50
    commands_per_session = 200
    base_time = int(datetime(2026, 1, 1, 12, 0, 0).timestamp())

    projects = []
    sessions = []
    commands = []
    for s in range(num_sessions):
        session_id = s + 1
        start = base_time + s * 3600
        projects.append(Project(
            id=session_id,
            name=f"archive_proj_{s}",
            path=os.path.join(str(tmp_path), f"archive_proj_{s}"),
            first_seen=start,
            last_seen=start + 400,
            session_count=1,
            total_time=400,
        ))
        sessions.append(Session(
            id=session_id,
            start_time=start,
            end_time=start + 400,
            duration_seconds=400,
            project_id=session_id,
            commands=[],
        ))
        for c in range(commands_per_session):
            commands.append(Command(
                timestamp=start + c,
                command=f"echo archive-batch {s} {c}",
                exit_code=0,
                session_id=session_id,
                project_id=session_id,
            ))

    db.save_data(projects, sessions, commands)

    started = time.perf_counter()
    stats = archive_old_data(db_path, archive_db_path, days=30)
    elapsed = time.perf_counter() - started

    assert stats["sessions"] == num_sessions
    assert stats["commands"] == num_sessions * commands_per_session
    assert elapsed < 30.0, f"archive of {stats['commands']} commands took {elapsed:.1f}s"
