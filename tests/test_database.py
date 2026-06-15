import time
from termstory.database import Database
from termstory.models import Command, Session, Project

def test_init_db(tmp_path):
    db_file = tmp_path / "test_init.db"
    db = Database(str(db_file))
    db.init_db()
    
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cursor.fetchall()}
    assert "projects" in tables
    assert "sessions" in tables
    assert "commands" in tables
    conn.close()

def test_insert_and_retrieve(tmp_path):
    db_file = tmp_path / "test_data.db"
    db = Database(str(db_file))
    db.init_db()
    
    # Use current epoch time to ensure retrieved records fall under "today" query window
    now_ts = int(time.time())
    
    # 1. Create memory entities with temporary sequential IDs
    project = Project(
        id=99, # Temp python ID
        name="Apache HugeGraph",
        path="~/projects/incubator-hugegraph",
        first_seen=now_ts,
        last_seen=now_ts + 100,
        session_count=1,
        total_time=100
    )
    cmd = Command(
        timestamp=now_ts,
        command="git status",
        exit_code=0,
        session_id=1,
        project_id=99
    )
    session = Session(
        id=999, # Temp python ID
        start_time=now_ts,
        end_time=now_ts + 100,
        duration_seconds=100,
        project_id=99,
        commands=[cmd]
    )
    
    # 2. Save using the bulk mapping transaction method
    db.save_data([project], [session], [cmd])
    
    # Check that database IDs were mapped back to the python entities
    assert project.id is not None
    assert project.id != 99
    assert session.id is not None
    assert session.id != 999
    assert cmd.project_id == project.id
    assert cmd.session_id == session.id
    
    # 3. Retrieve today's sessions
    today_sessions = db.get_today_sessions()
    assert len(today_sessions) == 1
    
    db_session = today_sessions[0]
    assert db_session.id == session.id
    assert db_session.start_time == now_ts
    assert db_session.project_id == project.id
    
    # 4. Retrieve today's projects
    today_projects = db.get_projects_by_ids([db_session.project_id])
    assert len(today_projects) == 1
    assert today_projects[0].name == "Apache HugeGraph"
    assert today_projects[0].path == "~/projects/incubator-hugegraph"
    
    # 5. Check commands inside session
    assert len(db_session.commands) == 1
    db_cmd = db_session.commands[0]
    assert db_cmd.command == "git status"
    assert db_cmd.session_id == db_session.id
    assert db_cmd.project_id == project.id

def test_session_growth_updates_existing_session(tmp_path):
    db_file = tmp_path / "test_growth.db"
    db = Database(str(db_file))
    db.init_db()
    
    now_ts = int(time.time())
    
    # Session starts with one command
    project = Project(id=1, name="Proj A", path="~/proj-a", first_seen=now_ts, last_seen=now_ts, session_count=1, total_time=0)
    cmd1 = Command(timestamp=now_ts, command="git status", exit_code=0, session_id=1, project_id=1)
    session1 = Session(id=1, start_time=now_ts, end_time=now_ts, duration_seconds=0, project_id=1, commands=[cmd1])
    
    db.save_data([project], [session1], [cmd1])
    db_session_id = session1.id
    
    # Session grows: new command added, end_time changes, duration changes
    cmd2 = Command(timestamp=now_ts + 300, command="git diff", exit_code=0, session_id=1, project_id=1)
    session2 = Session(id=1, start_time=now_ts, end_time=now_ts + 300, duration_seconds=300, project_id=1, commands=[cmd1, cmd2])
    
    db.save_data([project], [session2], [cmd1, cmd2])
    
    # Retrieve sessions and verify only ONE row exists and has the updated duration/end_time
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, start_time, end_time, duration_seconds, project_id FROM sessions")
    rows = cursor.fetchall()
    conn.close()
    
    assert len(rows) == 1
    assert rows[0][0] == db_session_id
    assert rows[0][2] == now_ts + 300 # Updated end_time
    assert rows[0][3] == 300          # Updated duration


def test_macro_summaries_caching(tmp_path):
    db_file = tmp_path / "test_macro.db"
    db = Database(str(db_file))
    db.init_db()
    
    # Verify table exists
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cursor.fetchall()}
    assert "macro_summaries" in tables
    conn.close()
    
    # Test saving and retrieving
    timeframe_id = "2026-06"
    assert db.get_macro_summary(timeframe_id) is None
    
    db.save_macro_summary(timeframe_id, "month", "Review summary text.")
    assert db.get_macro_summary(timeframe_id) == "Review summary text."
    
    # Test overwriting (UPSERT-like behavior)
    db.save_macro_summary(timeframe_id, "month", "Updated review summary text.")
    assert db.get_macro_summary(timeframe_id) == "Updated review summary text."


def test_session_deduplication_stable_key(tmp_path):
    db_file = tmp_path / "test_dedup.db"
    db = Database(str(db_file))
    db.init_db()
    
    now_ts = int(time.time())
    
    # Save a session with project 1
    project1 = Project(id=1, name="Proj A", path="~/proj-a", first_seen=now_ts, last_seen=now_ts, session_count=1, total_time=0)
    cmd1 = Command(timestamp=now_ts, command="git status", exit_code=0, session_id=1, project_id=1)
    session1 = Session(id=1, start_time=now_ts, end_time=now_ts, duration_seconds=0, project_id=1, commands=[cmd1])
    
    db.save_data([project1], [session1], [cmd1])
    
    # Simulate a second run where the SAME session (same start_time) gets resolved to project 2
    project2 = Project(id=2, name="Proj B", path="~/proj-b", first_seen=now_ts, last_seen=now_ts, session_count=1, total_time=0)
    cmd1_updated = Command(timestamp=now_ts, command="git status", exit_code=0, session_id=2, project_id=2)
    session2 = Session(id=2, start_time=now_ts, end_time=now_ts, duration_seconds=0, project_id=2, commands=[cmd1_updated])
    
    db.save_data([project2], [session2], [cmd1_updated])
    
    # Verify we still only have ONE session in the database, and it updated to project2
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, start_time, project_id FROM sessions")
    rows = cursor.fetchall()
    conn.close()
    
    assert len(rows) == 1
    assert rows[0][1] == now_ts
    assert rows[0][2] == project2.id  # Project was updated/overwritten for the existing session


def test_migration_deduplicates_legacy_data(tmp_path):
    db_file = tmp_path / "test_migration.db"
    
    # 1. Create a legacy database without UNIQUE index and manually insert duplicate sessions
    import sqlite3
    conn = sqlite3.connect(str(db_file))
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            start_time INTEGER NOT NULL,
            end_time INTEGER NOT NULL,
            duration_seconds INTEGER NOT NULL,
            project_id INTEGER,
            ai_summary TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE commands (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER NOT NULL,
            command TEXT NOT NULL,
            exit_code INTEGER NOT NULL,
            session_id INTEGER,
            project_id INTEGER
        )
    """)
    
    # Insert legacy duplicates (same start_time, different project_ids/ids)
    cursor.execute("INSERT INTO sessions (start_time, end_time, duration_seconds, project_id, ai_summary) VALUES (?, ?, ?, ?, ?)", (1000, 1050, 50, 1, None))
    s1_id = cursor.lastrowid
    cursor.execute("INSERT INTO sessions (start_time, end_time, duration_seconds, project_id, ai_summary) VALUES (?, ?, ?, ?, ?)", (1000, 1060, 60, 2, "AI Summary"))
    s2_id = cursor.lastrowid
    
    # Insert commands belonging to them
    cursor.execute("INSERT INTO commands (timestamp, command, exit_code, session_id, project_id) VALUES (?, ?, ?, ?, ?)", (1001, "cmd1", 0, s1_id, 1))
    cursor.execute("INSERT INTO commands (timestamp, command, exit_code, session_id, project_id) VALUES (?, ?, ?, ?, ?)", (1002, "cmd2", 0, s2_id, 2))
    
    conn.commit()
    conn.close()
    
    # 2. Instantiate and run Database.init_db() which runs the migration
    db = Database(str(db_file))
    db.init_db()
    
    # 3. Verify that duplicate sessions with SAME project_id are merged, different project_id preserved
    conn = db.get_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT id, start_time, project_id, ai_summary FROM sessions")
    sessions_rows = cursor.fetchall()
    
    cursor.execute("SELECT session_id, command FROM commands ORDER BY timestamp ASC")
    commands_rows = cursor.fetchall()
    
    conn.close()
    
    # Should have 2 sessions (same start_time, different project_id - both preserved)
    assert len(sessions_rows) == 2
    # Both commands should belong to their respective sessions
    assert len(commands_rows) == 2
    assert commands_rows[0][0] in (sessions_rows[0][0], sessions_rows[1][0])
    assert commands_rows[1][0] in (sessions_rows[0][0], sessions_rows[1][0])

def test_database_weekly_vacuum(tmp_path):
    db_file = tmp_path / "test_vacuum.db"
    db = Database(str(db_file))
    db.init_db()
    
    # 1. Verify last_vacuum exists in macro_summaries
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT created_at FROM macro_summaries WHERE timeframe_id = 'last_vacuum'")
    row = cursor.fetchone()
    assert row is not None
    initial_ts = row[0]
    
    # 2. Update last_vacuum created_at to 8 days ago
    eight_days_ago = initial_ts - 8 * 24 * 3600
    cursor.execute("UPDATE macro_summaries SET created_at = ? WHERE timeframe_id = 'last_vacuum'", (eight_days_ago,))
    conn.commit()
    conn.close()
    
    # 3. Call init_db() again, which should trigger weekly VACUUM and update timestamp
    db.init_db()
    
    # 4. Verify last_vacuum created_at is updated back to close to current time
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT created_at FROM macro_summaries WHERE timeframe_id = 'last_vacuum'")
    row2 = cursor.fetchone()
    assert row2 is not None
    assert row2[0] > eight_days_ago
    conn.close()



