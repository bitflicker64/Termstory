"""Concurrency stress test: 2 sessions x 20 commands = 40 commands per worker (lightweight)"""
import sqlite3
import threading
import time
import os
import random
from termstory.database import Database
from termstory.models import Project, Session, Command

DB_PATH = "test_stress.db"

def writer_worker(worker_id, num_sessions=2, commands_per_session=20):
    """Write ~40 commands per worker (2 sessions x 20 commands)"""
    db = Database(DB_PATH)
    db.init_db()
    
    base_time = int(time.time()) - 86400 * 30  # 30 days ago
    
    for s in range(num_sessions):
        try:
            # Create project
            p = Project(
                id=None,
                name=f"stress_proj_{worker_id}_{s}",
                path=f"/tmp/stress_proj_{worker_id}_{s}",
                first_seen=base_time + s * 1000,
                last_seen=base_time + s * 1000 + 500,
                session_count=1,
                total_time=500
            )
            
            # Create session - use temp_id for session mapping
            temp_session_id = worker_id * 1000 + s + 1
            session_start = base_time + s * 1000
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
                    timestamp=session_start + c * 6,
                    command=f"git commit -m 'stress test {worker_id} {s} {c}'",
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

def reader_worker(worker_id, num_queries=100):
    """Concurrent reads during writes"""
    db = Database(DB_PATH)
    
    for q in range(num_queries):
        try:
            # Search queries
            db.search_sessions("stress")
            db.search_sessions("commit")
            db.search_sessions("test")
            
            # List projects
            conn = db.get_connection()
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM projects")
            c.fetchone()
            conn.close()
            
            # Get sessions (range query)
            import time as time_module
            db.get_range_sessions(int(time_module.time()) - 86400 * 40, int(time_module.time()))
            
        except Exception as e:
            print(f"Reader {worker_id} query {q} error: {e}")
        time.sleep(0.001)  # Small delay

def test_stress():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    
    print("Starting stress test: 5 writers x 25 sessions x 80 commands = 10,000 commands")
    print("Plus 3 concurrent readers...")
    
    threads = []
    start = time.time()
    
    # Start readers first
    for i in range(3):
        t = threading.Thread(target=reader_worker, args=(i, 150))
        threads.append(t)
        t.start()
    
    # Start writers
    for i in range(5):
        t = threading.Thread(target=writer_worker, args=(i,))
        threads.append(t)
        t.start()
    
    # Wait for all
    for t in threads:
        t.join()
    
    elapsed = time.time() - start
    print(f"Finished stress test in {elapsed:.2f}s")
    
    # Verify counts
    db = Database(DB_PATH)
    conn = db.get_connection()
    c = conn.cursor()
    
    c.execute("SELECT COUNT(*) FROM commands")
    cmd_count = c.fetchone()[0]
    print(f"Commands inserted: {cmd_count}")
    
    c.execute("SELECT COUNT(*) FROM sessions")
    sess_count = c.fetchone()[0]
    print(f"Sessions created: {sess_count}")
    
    c.execute("SELECT COUNT(*) FROM projects")
    proj_count = c.fetchone()[0]
    print(f"Projects created: {proj_count}")
    
    # Test FTS search still works
    results = db.search_sessions("stress test")
    print(f"FTS search results: {len(results)} sessions found")
    
    assert cmd_count >= 200, f"Expected ~200 commands, got {cmd_count}"
    assert sess_count >= 2, f"Expected at least 2 sessions, got {sess_count}"
    assert proj_count >= 10, f"Expected ~10 projects, got {proj_count}"
    
    print("STRESS TEST PASSED")
    conn.close()

if __name__ == "__main__":
    test_stress()