import pytest
import sqlite3
import time
from datetime import datetime
from termstory.database import Database
from termstory.models import Project, Session, Command
from termstory.search import advanced_search

def test_new_fts5_migration_triggers_and_search(tmp_path):
    db_file = tmp_path / "test_new_fts5.db"
    db = Database(str(db_file))
    db.init_db()

    # 1. Verify new FTS5 tables exist
    conn = db.get_connection()
    cursor = conn.cursor()
    for table in ['commands_fts', 'sessions_fts', 'ai_summaries_fts']:
        cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}';")
        assert cursor.fetchone() is not None, f"{table} should exist"
    
    # 2. Verify triggers exist
    for trigger in ['commands_ai', 'commands_au', 'commands_ad',
                    'sessions_ai', 'sessions_au', 'sessions_ad',
                    'macro_summaries_ai', 'macro_summaries_au', 'macro_summaries_ad']:
        cursor.execute(f"SELECT name FROM sqlite_master WHERE type='trigger' AND name='{trigger}';")
        assert cursor.fetchone() is not None, f"{trigger} should exist"
    conn.close()

    # 3. Test insert triggers
    p = Project(id=1, name="Project Alpha", path="~/projects/alpha", first_seen=1000, last_seen=2000, session_count=1, total_time=500)
    cmd1 = Command(id=1, timestamp=1050, command="git commit -m 'initial release'", exit_code=0, session_id=1, project_id=1)
    s1 = Session(id=1, start_time=1000, end_time=1500, duration_seconds=500, project_id=1, commands=[cmd1])

    db.save_data([p], [s1], [cmd1])
    
    # Call save_session_ai_summary to update ai_summary (and trigger insert/update)
    db.save_session_ai_summary(s1.id, "Started repository and made first commit")

    # Insert a macro summary (AI summary)
    # The timeframe_id must be unique
    db.save_macro_summary("today_review_1", "daily", "Completed the main pipeline setup")

    # Verify virtual table contents
    conn = db.get_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT command, exit_code FROM commands_fts WHERE rowid = ?;", (cmd1.id,))
    row = cursor.fetchone()
    assert row is not None
    assert row[0] == "git commit -m 'initial release'"
    assert row[1] == 0

    cursor.execute("SELECT ai_summary FROM sessions_fts WHERE rowid = ?;", (s1.id,))
    row = cursor.fetchone()
    assert row is not None
    assert row[0] == "Started repository and made first commit"

    cursor.execute("SELECT summary FROM ai_summaries_fts WHERE summary = 'Completed the main pipeline setup';")
    rows = cursor.fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "Completed the main pipeline setup"
    conn.close()

    # 4. Test update triggers
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE commands SET command = 'git push origin main' WHERE id = ?;", (cmd1.id,))
    cursor.execute("UPDATE sessions SET ai_summary = 'Pushed code to origin' WHERE id = ?;", (s1.id,))
    cursor.execute("UPDATE macro_summaries SET summary = 'Pushed changes successfully' WHERE timeframe_id = 'today_review_1';")
    conn.commit()
    conn.close()

    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT command FROM commands_fts WHERE rowid = ?;", (cmd1.id,))
    assert cursor.fetchone()[0] == "git push origin main"

    cursor.execute("SELECT ai_summary FROM sessions_fts WHERE rowid = ?;", (s1.id,))
    assert cursor.fetchone()[0] == "Pushed code to origin"

    cursor.execute("SELECT summary FROM ai_summaries_fts WHERE summary = 'Pushed changes successfully';")
    rows = cursor.fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "Pushed changes successfully"
    conn.close()

    # 5. Test delete triggers
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM commands WHERE id = ?;", (cmd1.id,))
    cursor.execute("DELETE FROM sessions WHERE id = ?;", (s1.id,))
    cursor.execute("DELETE FROM macro_summaries WHERE timeframe_id = 'today_review_1';")
    conn.commit()
    conn.close()

    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM commands_fts WHERE command = 'git push origin main';")
    assert cursor.fetchone()[0] == 0

    cursor.execute("SELECT COUNT(*) FROM sessions_fts WHERE ai_summary = 'Pushed code to origin';")
    assert cursor.fetchone()[0] == 0

    cursor.execute("SELECT COUNT(*) FROM ai_summaries_fts WHERE summary = 'Pushed changes successfully';")
    assert cursor.fetchone()[0] == 0
    conn.close()

def test_new_fts5_search_functionality(tmp_path):
    db_file = tmp_path / "test_new_fts5_search.db"
    db = Database(str(db_file))
    db.init_db()

    # Save mock data
    p = Project(id=1, name="Project Beta", path="~/projects/beta", first_seen=1000, last_seen=2000, session_count=1, total_time=500)
    
    # We will set a fixed created_at time for macro summary
    # Let's use current time
    now_ts = int(time.time())
    
    cmd1 = Command(id=10, timestamp=now_ts, command="python setup.py install", exit_code=0, session_id=5, project_id=1)
    s1 = Session(id=5, start_time=now_ts, end_time=now_ts + 100, duration_seconds=100, project_id=1, commands=[cmd1])
    
    db.save_data([p], [s1], [cmd1])
    
    # Call save_session_ai_summary to update ai_summary
    db.save_session_ai_summary(s1.id, "Ran installation script for package configuration")
    
    # Insert macro summary for the same day/time
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO macro_summaries (timeframe_id, type, summary, created_at) VALUES (?, ?, ?, ?)",
        ("day_beta", "daily", "Verified installation and setup on server", now_ts)
    )
    conn.commit()
    conn.close()

    # Test searching using advanced_search with fts=True
    # Search by command term "setup"
    results = advanced_search(db, query="setup", fts=True)
    assert len(results) == 1
    assert results[0]["session_id"] == s1.id

    # Search by session summary term "installation"
    results = advanced_search(db, query="installation", fts=True)
    assert len(results) == 1
    assert results[0]["session_id"] == s1.id

    # Search by macro summary term "server" (since it matches session on the same day)
    results = advanced_search(db, query="server", fts=True)
    assert len(results) == 1
    assert results[0]["session_id"] == s1.id
