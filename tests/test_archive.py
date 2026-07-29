import os
import sqlite3
import pytest
from datetime import datetime, timedelta
from typer.testing import CliRunner
from termstory.cli import app
from termstory.database import Database
from termstory.models import Project, Session, Command
from termstory.archive import archive_old_data, is_timeframe_older_than
from datetime import date

def test_is_timeframe_older_than():
    cutoff_date = date(2026, 6, 14)
    # Test date type
    assert is_timeframe_older_than("2026-06-13", "date", cutoff_date) is True
    assert is_timeframe_older_than("2026-06-15", "date", cutoff_date) is False
    assert is_timeframe_older_than("invalid", "date", cutoff_date) is False
    
    # Test month type
    # For month, cutoff is 2026-06-14. 2026-05 next month starts 2026-06-01 <= cutoff_date -> True
    assert is_timeframe_older_than("2026-05", "month", cutoff_date) is True
    # 2026-06 next month starts 2026-07-01 > cutoff_date -> False
    assert is_timeframe_older_than("2026-06", "month", cutoff_date) is False
    assert is_timeframe_older_than("invalid", "month", cutoff_date) is False

def test_archive_logic(tmp_path, monkeypatch):
    # Mock current date/time to June 14, 2026
    monkeypatch.setenv("TERMSTORY_DATE_OVERRIDE", "2026-06-14 12:00:00")
    
    main_db_path = str(tmp_path / "main.db")
    archive_db_path = str(tmp_path / "archive.db")
    
    # Initialize main database
    main_db = Database(main_db_path)
    main_db.init_db()
    
    # Setup baseline timestamps
    # Cutoff is 30 days before June 14, 2026 (approx May 15, 2026)
    base_time = int(datetime(2026, 6, 14, 12, 0, 0).timestamp())
    old_time = base_time - (45 * 24 * 3600)  # 45 days ago
    new_time = base_time - (5 * 24 * 3600)   # 5 days ago
    
    # Create projects with required fields session_count and total_time
    old_project = Project(id=1, name="Old Project", path="~/old", first_seen=old_time, last_seen=old_time, session_count=1, total_time=60, project_context="Old context")
    new_project = Project(id=2, name="New Project", path="~/new", first_seen=new_time, last_seen=new_time, session_count=1, total_time=60)
    
    # Create sessions & commands
    old_cmd = Command(id=1, timestamp=old_time, command="git commit -m 'old'", session_id=1, project_id=1)
    old_session = Session(id=1, start_time=old_time, end_time=old_time + 60, duration_seconds=60, project_id=1, commands=[old_cmd], ai_summary="Old summary")
    
    new_cmd = Command(id=2, timestamp=new_time, command="git commit -m 'new'", session_id=2, project_id=2)
    new_session = Session(id=2, start_time=new_time, end_time=new_time + 60, duration_seconds=60, project_id=2, commands=[new_cmd], ai_summary="New summary")
    
    # Save data to main DB
    main_db.save_data([old_project, new_project], [old_session, new_session], [old_cmd, new_cmd])
    
    # Save commits
    old_commit = {"hash": "1111111111111111111111111111111111111111", "timestamp": old_time, "message": "feat: old commit", "cleaned_message": "old commit"}
    new_commit = {"hash": "2222222222222222222222222222222222222222", "timestamp": new_time, "message": "feat: new commit", "cleaned_message": "new commit"}
    main_db.save_commits(1, [old_commit])
    main_db.save_commits(2, [new_commit])
    
    # Save macro summaries and manually update session summaries since save_data ignores them on insertion
    conn = main_db.get_connection()
    conn.execute("UPDATE sessions SET ai_summary = 'Old summary' WHERE id = 1")
    conn.execute("UPDATE sessions SET ai_summary = 'New summary' WHERE id = 2")
    conn.execute("INSERT INTO macro_summaries (timeframe_id, type, summary) VALUES ('2026-04', 'month', 'April Summary')")
    conn.execute("INSERT INTO macro_summaries (timeframe_id, type, summary) VALUES ('2026-06-10', 'date', 'June 10 Summary')")
    conn.commit()
    conn.close()
    
    # Perform archiving (older than 30 days)
    stats = archive_old_data(main_db_path, archive_db_path, days=30)
    
    assert stats["sessions"] == 1
    assert stats["commands"] == 1
    assert stats["commits"] == 1
    assert stats["macro_summaries"] == 1  # 2026-04 month is archived, 2026-06-10 is not.
    
    # Verify Main Database state
    conn_main = sqlite3.connect(main_db_path)
    c_main = conn_main.cursor()
    
    c_main.execute("SELECT id FROM sessions")
    sessions_main = [r[0] for r in c_main.fetchall()]
    assert sessions_main == [2]  # Only new session remains
    
    c_main.execute("SELECT id FROM commands")
    commands_main = [r[0] for r in c_main.fetchall()]
    assert commands_main == [2]  # Only new command remains
    
    c_main.execute("SELECT hash FROM commits")
    commits_main = [r[0] for r in c_main.fetchall()]
    assert commits_main == ["2222222222222222222222222222222222222222"]
    
    c_main.execute("SELECT timeframe_id FROM macro_summaries")
    macro_main = [r[0] for r in c_main.fetchall()]
    assert "2026-06-10" in macro_main
    assert "2026-04" not in macro_main
    
    # Check main search index
    c_main.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='search_index'")
    if c_main.fetchone():
        c_main.execute("SELECT ref_id, type FROM search_index")
        fts_main = c_main.fetchall()
        # Should not contain old ref_ids
        for ref_id, fts_type in fts_main:
            assert ref_id != "1"
            assert ref_id != "1111111111111111111111111111111111111111"
            
    conn_main.close()
    
    # Verify Archive Database state
    conn_arch = sqlite3.connect(archive_db_path)
    c_arch = conn_arch.cursor()
    
    c_arch.execute("SELECT start_time, project_id, ai_summary FROM sessions")
    sessions_arch = c_arch.fetchall()
    assert len(sessions_arch) == 1
    assert sessions_arch[0][0] == old_time
    assert sessions_arch[0][2] == "Old summary"
    
    c_arch.execute("SELECT timestamp, command, session_id FROM commands")
    commands_arch = c_arch.fetchall()
    assert len(commands_arch) == 1
    assert commands_arch[0][0] == old_time
    assert commands_arch[0][1] == "git commit -m 'old'"
    
    # Ensure correct mapping (command is linked to the session in archive)
    assert commands_arch[0][2] == 1
    
    c_arch.execute("SELECT hash FROM commits")
    commits_arch = [r[0] for r in c_arch.fetchall()]
    assert commits_arch == ["1111111111111111111111111111111111111111"]
    
    c_arch.execute("SELECT timeframe_id FROM macro_summaries")
    macro_arch = [r[0] for r in c_arch.fetchall()]
    assert "2026-04" in macro_arch
    assert "2026-06-10" not in macro_arch
    
    c_arch.execute("SELECT name, project_context FROM projects WHERE id = ?", (sessions_arch[0][1],))
    proj_row = c_arch.fetchone()
    assert proj_row is not None
    assert proj_row[0] == "Old Project"
    assert proj_row[1] == "Old context"
    
    # Check archive search index
    c_arch.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='search_index'")
    if c_arch.fetchone():
        c_arch.execute("SELECT content, type FROM search_index")
        fts_arch = c_arch.fetchall()
        assert len(fts_arch) >= 2 # command and session_summary (and possibly commit)
        
    conn_arch.close()

def test_cli_archive_command(tmp_path, monkeypatch):
    monkeypatch.setenv("TERMSTORY_DATE_OVERRIDE", "2026-06-14 12:00:00")
    
    db_file = tmp_path / "main_cli.db"
    archive_file = tmp_path / "archive_cli.db"
    
    # Patch get_db_path
    monkeypatch.setattr("termstory.cli.get_db_path", lambda: str(db_file))
    monkeypatch.setattr("termstory.config.get_db_path", lambda: str(db_file))
    monkeypatch.setattr("termstory.cli.get_history_files", lambda: [])
    
    # Initialize main db with old session
    db = Database(str(db_file))
    db.init_db()
    
    base_time = int(datetime(2026, 6, 14, 12, 0, 0).timestamp())
    old_time = base_time - (45 * 24 * 3600)  # 45 days ago
    
    old_project = Project(id=1, name="Old Project", path="~/old", first_seen=old_time, last_seen=old_time, session_count=1, total_time=60)
    old_cmd = Command(id=1, timestamp=old_time, command="git commit -m 'old'", session_id=1, project_id=1)
    old_session = Session(id=1, start_time=old_time, end_time=old_time + 60, duration_seconds=60, project_id=1, commands=[old_cmd], ai_summary="Old summary")
    
    db.save_data([old_project], [old_session], [old_cmd])
    
    # Run CLI command
    runner = CliRunner()
    result = runner.invoke(app, ["archive", "--days", "30", "--archive-db", str(archive_file)])
    
    assert result.exit_code == 0, result.stdout
    assert "Archiving data older than 30 days" in result.stdout
    assert "Archiving completed successfully" in result.stdout
    assert "Sessions archived: 1" in result.stdout
    
    # Verify main database is clean
    conn = db.get_connection()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM sessions")
    assert c.fetchone()[0] == 0
    conn.close()
    
    # Verify archive database got the data
    conn_arch = sqlite3.connect(str(archive_file))
    c_arch = conn_arch.cursor()
    c_arch.execute("SELECT COUNT(*) FROM sessions")
    assert c_arch.fetchone()[0] == 1
    conn_arch.close()


def test_cli_archive_rejects_days_zero(tmp_path, monkeypatch):
    monkeypatch.setenv("TERMSTORY_DATE_OVERRIDE", "2026-06-14 12:00:00")
    
    db_file = tmp_path / "main_cli.db"
    archive_file = tmp_path / "archive_cli.db"
    
    monkeypatch.setattr("termstory.cli.get_db_path", lambda: str(db_file))
    monkeypatch.setattr("termstory.config.get_db_path", lambda: str(db_file))
    monkeypatch.setattr("termstory.cli.get_history_files", lambda: [])
    
    db = Database(str(db_file))
    db.init_db()
    
    runner = CliRunner()
    result = runner.invoke(app, ["archive", "--days", "0", "--archive-db", str(archive_file)])
    
    assert result.exit_code == 1, result.stdout
    assert "days must be greater than 0" in result.output.lower()
    
    # Verify archive logic was not executed (no archive db created)
    assert not archive_file.exists()


def test_cli_archive_rejects_days_negative(tmp_path, monkeypatch):
    monkeypatch.setenv("TERMSTORY_DATE_OVERRIDE", "2026-06-14 12:00:00")
    
    db_file = tmp_path / "main_cli.db"
    archive_file = tmp_path / "archive_cli.db"
    
    monkeypatch.setattr("termstory.cli.get_db_path", lambda: str(db_file))
    monkeypatch.setattr("termstory.config.get_db_path", lambda: str(db_file))
    monkeypatch.setattr("termstory.cli.get_history_files", lambda: [])
    
    db = Database(str(db_file))
    db.init_db()
    
    runner = CliRunner()
    result = runner.invoke(app, ["archive", "--days", "-5", "--archive-db", str(archive_file)])
    
    assert result.exit_code == 1, result.stdout
    assert "days must be greater than 0" in result.output.lower()
    
    # Verify archive logic was not executed (no archive db created)
    assert not archive_file.exists()
