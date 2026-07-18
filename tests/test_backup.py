import logging
import os
import pytest
from termstory.backup import backup_db, restore_db
from termstory.database import Database
from termstory.models import Project, Session, Command

def test_backup_and_restore(tmp_path, monkeypatch):
    # Setup temporary database path under tmp_path
    db_file = tmp_path / "test_backup.db"
    db_path = str(db_file)

    # Patch the environment variable and functions to ensure they return our temporary database path
    monkeypatch.setenv("DB_PATH", db_path)
    monkeypatch.setattr("termstory.config.get_db_path", lambda: db_path)
    monkeypatch.setattr("termstory.backup.get_db_path", lambda: db_path)

    # Initialize database and insert sample data
    db = Database(db_path)
    db.init_db()
    now = 1730000000  # arbitrary timestamp
    project = Project(id=1, name="Demo Project", path="~/demo", first_seen=now, last_seen=now, session_count=1, total_time=100)
    command = Command(timestamp=now, command="echo hello", session_id=1, project_id=1)
    session = Session(id=1, start_time=now, end_time=now + 100, duration_seconds=100, project_id=1, commands=[command])
    db.save_data([project], [session], [command])

    # Perform backup
    backup_path = backup_db()
    assert os.path.isfile(backup_path), "Backup file was not created"

    # Corrupt original db by removing it
    os.remove(db_path)
    assert not os.path.exists(db_path)

    # Restore from backup
    restore_db(backup_path)
    assert os.path.isfile(db_path), "Database file was not recreated after restore"

    # Verify data was restored correctly
    restored_db = Database(db_path)
    restored_db.init_db()
    projects = restored_db.search_projects("")
    assert len(projects) == 1
    assert projects[0].name == "Demo Project"
    
    sessions = restored_db.search_sessions("")
    assert len(sessions) == 1
    assert sessions[0]["duration_seconds"] == 100

    conn = restored_db.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT command FROM commands")
    commands = cursor.fetchall()
    conn.close()
    assert len(commands) == 1
    assert commands[0][0] == "echo hello"


def test_backup_rotation(tmp_path, monkeypatch):
    db_file = tmp_path / "test_rotation.db"
    db_path = str(db_file)
    monkeypatch.setenv("DB_PATH", db_path)
    monkeypatch.setattr("termstory.config.get_db_path", lambda: db_path)
    monkeypatch.setattr("termstory.backup.get_db_path", lambda: db_path)

    db = Database(db_path)
    db.init_db()

    # Create 12 backups with incrementing simulated time
    class MockDatetime:
        counter = 0
        @classmethod
        def now(cls):
            cls.counter += 1
            from datetime import datetime as dt
            return dt(2026, 6, 18, 19, 0, cls.counter)

    monkeypatch.setattr("termstory.backup.datetime", MockDatetime)

    for _ in range(12):
        backup_db()

    from termstory.backup import _get_backup_dir
    backup_dir = _get_backup_dir()
    import glob
    remaining = glob.glob(os.path.join(backup_dir, "termstory_backup_*.db"))
    assert len(remaining) == 10


def test_backup_survives_rotation_failure(tmp_path, monkeypatch, caplog):
    db_file = tmp_path / "test_rotation_failure.db"
    db_path = str(db_file)
    monkeypatch.setenv("DB_PATH", db_path)
    monkeypatch.setattr("termstory.config.get_db_path", lambda: db_path)
    monkeypatch.setattr("termstory.backup.get_db_path", lambda: db_path)

    db = Database(db_path)
    db.init_db()

    class MockDatetime:
        counter = 0
        @classmethod
        def now(cls):
            cls.counter += 1
            from datetime import datetime as dt
            return dt(2026, 6, 18, 19, 0, cls.counter)

    monkeypatch.setattr("termstory.backup.datetime", MockDatetime)

    # Fill past the rotation threshold so cleanup runs and hits the failing os.remove.
    for _ in range(11):
        backup_db()

    def boom(_path):
        raise PermissionError("cannot delete backup")

    monkeypatch.setattr("termstory.backup.os.remove", boom)

    # Rotation now fails, but the backup itself must still succeed and return a path.
    with caplog.at_level(logging.ERROR, logger="termstory.backup"):
        backup_path = backup_db()

    assert os.path.isfile(backup_path)

    # The rotation failure must be logged, not silently swallowed. A regression
    # back to `except OSError: pass` would drop this record and fail here.
    assert any(
        "Failed to rotate old backups" in record.message
        for record in caplog.records
    )
