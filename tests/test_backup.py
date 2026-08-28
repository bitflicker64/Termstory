import logging
import os
import glob
import sqlite3
import pytest
from termstory.backup import backup_db, restore_db, BackupError
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

    created = [backup_db() for _ in range(12)]

    from termstory.backup import _get_backup_dir
    backup_dir = _get_backup_dir()
    import glob
    remaining = glob.glob(os.path.join(backup_dir, "termstory_backup_*.db"))
    assert len(remaining) == 10

    # The two oldest backups must have been deleted first.
    for oldest in sorted(created)[:2]:
        assert not os.path.exists(oldest)


def test_backup_rotation_clock_skew(tmp_path, monkeypatch):
    # Regression for #429: when the wall clock jumps backward, a *newer* backup
    # can receive an earlier wall-clock timestamp in its filename. Rotation must
    # order by a stable creation-order signal, not by the timestamp embedded in
    # the filename, or it would delete the newest backup instead of the oldest.
    db_file = tmp_path / "test_clock_skew.db"
    db_path = str(db_file)
    monkeypatch.setenv("DB_PATH", db_path)
    monkeypatch.setattr("termstory.config.get_db_path", lambda: db_path)
    monkeypatch.setattr("termstory.backup.get_db_path", lambda: db_path)

    db = Database(db_path)
    db.init_db()

    # Simulate a clock that runs *backward*: each successive backup is created on
    # disk later (so it is genuinely newer) but receives an earlier wall-clock
    # timestamp in its filename. The last backup created therefore sorts first
    # lexicographically even though it is the newest.
    class BackwardClock:
        second = 60  # decremented before first use

        @classmethod
        def now(cls):
            cls.second -= 1
            from datetime import datetime as dt
            return dt(2026, 6, 18, 19, 0, cls.second)

    monkeypatch.setattr("termstory.backup.datetime", BackwardClock)

    # Replace the production ordering mechanism with a deterministic, path-based
    # creation-order mapping: each backup is assigned a monotonically increasing
    # key as it is created (0, 1, 2, ...). Any path not yet registered (the
    # just-created newest backup during its own rotation) sorts last, i.e. as the
    # newest, which is correct. This removes any dependence on filesystem
    # timestamp resolution while still exercising the real rotation behavior.
    from termstory import backup as backup_mod
    creation_order = {}

    def creation_key(path):
        return creation_order.get(os.path.normpath(path), float("inf"))

    monkeypatch.setattr(backup_mod, "_backup_creation_key", creation_key)

    created = []
    for _ in range(12):
        path = backup_db()
        created.append(path)
        creation_order[os.path.normpath(path)] = len(creation_order)

    from termstory.backup import _get_backup_dir
    import glob
    backup_dir = _get_backup_dir()
    remaining = glob.glob(os.path.join(backup_dir, "termstory_backup_*.db"))
    assert len(remaining) == 10

    # Confirm the clock-skew setup actually holds: the newest backup (the last
    # created, key 11) has a lexicographically *earlier* filename than the oldest
    # surviving backup.
    newest = created[-1]
    oldest_kept = created[2]
    assert os.path.basename(newest) < os.path.basename(oldest_kept)

    # Rotation must delete the genuinely oldest backups (the first two created),
    # whose filenames sort *last* due to the backward clock.
    for oldest in created[:2]:
        assert not os.path.exists(oldest)

    # The newest backup, despite carrying the misleading (earliest) filename,
    # must be preserved.
    assert os.path.isfile(newest)


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


def test_backup_consecutive_calls_unique(tmp_path, monkeypatch):
    # Regression for #416: two immediate backup_db() calls must produce distinct
    # files. The test intentionally avoids sleep()/timing so a second-level
    # timestamp would collide and fail here.
    db_file = tmp_path / "test_consecutive.db"
    db_path = str(db_file)
    monkeypatch.setenv("DB_PATH", db_path)
    monkeypatch.setattr("termstory.config.get_db_path", lambda: db_path)
    monkeypatch.setattr("termstory.backup.get_db_path", lambda: db_path)

    db = Database(db_path)
    db.init_db()

    backup_one = backup_db()
    backup_two = backup_db()

    assert backup_one != backup_two, "consecutive backups must not collide"
    assert os.path.isfile(backup_one), "first backup file was not created"
    assert os.path.isfile(backup_two), "second backup file was not created"

        # Both backups must contain valid SQLite data.
    for backup_path in (backup_one, backup_two):
        conn = sqlite3.connect(backup_path)
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()
            assert integrity[0] == "ok"
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Helpers for Issue #479 restore-validation regression tests
# ---------------------------------------------------------------------------

def _patch_db_path(monkeypatch, db_path):
    """Patch get_db_path in both config and backup modules to *db_path*."""
    monkeypatch.setenv("DB_PATH", db_path)
    monkeypatch.setattr("termstory.config.get_db_path", lambda: db_path)
    monkeypatch.setattr("termstory.backup.get_db_path", lambda: db_path)


def _seed_db(db_path, project_name="Test Project"):
    """Initialize a TermStory DB and seed one project/session/command."""
    db = Database(db_path)
    db.init_db()
    now = 1730000000
    project = Project(
        id=1, name=project_name, path="~/demo",
        first_seen=now, last_seen=now,
        session_count=1, total_time=100,
    )
    command = Command(timestamp=now, command="echo hello",
                      session_id=1, project_id=1)
    session = Session(
        id=1, start_time=now, end_time=now + 100,
        duration_seconds=100, project_id=1, commands=[command],
    )
    db.save_data([project], [session], [command])


def _read_first_project_name(db_path):
    """Return the name of the first project row, or None if no projects."""
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM projects ORDER BY id LIMIT 1")
        row = cursor.fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _count_rows(db_path, table):
    """Return the row count of *table* in the database at *db_path*."""
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM {}".format(table))
        return cursor.fetchone()[0]
    finally:
        conn.close()


def _make_corrupt_termstory_db(path):
    """Create a valid TermStory DB (schema + bulk data) then corrupt a data
    page so that PRAGMA integrity_check fails while the schema on page 1
    remains readable."""
    db = Database(path)
    db.init_db()
    now = 1730000000
    conn = sqlite3.connect(path)
    conn.executemany(
        "INSERT INTO commands (timestamp, command, session_id, project_id) "
        "VALUES (?, ?, ?, ?)",
        [(now + i, "cmd{}".format(i), 1, 1) for i in range(5000)],
    )
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.commit()
    conn.close()
    page_size = 4096
    with open(path, "r+b") as f:
        f.seek(0, 2)
        size = f.tell()
        assert size > page_size * 3, "DB too small to corrupt: {} bytes".format(size)
        f.seek(page_size * 2)
        f.write(b"\xFF" * page_size)


# ---------------------------------------------------------------------------
# Issue #479 — restore validation and atomic replacement tests
# ---------------------------------------------------------------------------

def test_restore_valid_backup_succeeds(tmp_path, monkeypatch):
    """A valid backup restores all data correctly."""
    db_path = str(tmp_path / "live.db")
    _patch_db_path(monkeypatch, db_path)

    _seed_db(db_path, "Live Project")
    backup_path = backup_db()
    assert os.path.isfile(backup_path)

    restore_db(backup_path)

    assert os.path.isfile(db_path)
    assert _read_first_project_name(db_path) == "Live Project"
    assert _count_rows(db_path, "projects") == 1
    assert _count_rows(db_path, "sessions") == 1
    assert _count_rows(db_path, "commands") == 1


def test_restore_rejects_non_sqlite_file(tmp_path, monkeypatch):
    """A file that is not a SQLite database must be rejected and the
    active DB must remain untouched."""
    db_path = str(tmp_path / "live.db")
    _patch_db_path(monkeypatch, db_path)
    _seed_db(db_path, "Sentinel Project")

    fake_backup = str(tmp_path / "not_sqlite.db")
    with open(fake_backup, "w") as f:
        f.write("This is definitely not a SQLite database file.")

    with pytest.raises(BackupError):
        restore_db(fake_backup)

    assert _read_first_project_name(db_path) == "Sentinel Project"


def test_restore_rejects_incomplete_schema(tmp_path, monkeypatch):
    """A valid SQLite file missing required TermStory tables must be rejected."""
    db_path = str(tmp_path / "live.db")
    _patch_db_path(monkeypatch, db_path)
    _seed_db(db_path, "Sentinel Project")

    fake_backup = str(tmp_path / "incomplete.db")
    conn = sqlite3.connect(fake_backup)
    conn.execute(
        "CREATE TABLE arbitrary_data (id INTEGER PRIMARY KEY, value TEXT)"
    )
    conn.commit()
    conn.close()

    with pytest.raises(BackupError):
        restore_db(fake_backup)

    assert _read_first_project_name(db_path) == "Sentinel Project"


def test_restore_rejects_corrupt_database(tmp_path, monkeypatch):
    """A corrupted SQLite DB (failing integrity_check) must be rejected."""
    db_path = str(tmp_path / "live.db")
    _patch_db_path(monkeypatch, db_path)
    _seed_db(db_path, "Sentinel Project")

    corrupt_backup = str(tmp_path / "corrupt.db")
    _make_corrupt_termstory_db(corrupt_backup)

    with pytest.raises(BackupError):
        restore_db(corrupt_backup)

    assert _read_first_project_name(db_path) == "Sentinel Project"


def test_restore_atomic_success_replaces_data(tmp_path, monkeypatch):
    """A valid restore atomically replaces the active database data."""
    db_path = str(tmp_path / "live.db")
    _patch_db_path(monkeypatch, db_path)

    _seed_db(db_path, "Old Project")

    backup_db_path = str(tmp_path / "backup.db")
    _seed_db(backup_db_path, "New Project")

    restore_db(backup_db_path)

    assert _read_first_project_name(db_path) == "New Project"


def test_restore_idempotent(tmp_path, monkeypatch):
    """Restoring the same valid backup twice must succeed both times."""
    db_path = str(tmp_path / "live.db")
    _patch_db_path(monkeypatch, db_path)

    _seed_db(db_path, "Idempotent Project")
    backup_path = backup_db()

    restore_db(backup_path)
    assert _read_first_project_name(db_path) == "Idempotent Project"

    restore_db(backup_path)
    assert _read_first_project_name(db_path) == "Idempotent Project"


def test_restore_failed_no_temp_artifacts(tmp_path, monkeypatch):
    """A failed restore must not leave temporary database files behind."""
    db_path = str(tmp_path / "live.db")
    _patch_db_path(monkeypatch, db_path)
    _seed_db(db_path, "Sentinel Project")

    fake_backup = str(tmp_path / "not_sqlite.db")
    with open(fake_backup, "w") as f:
        f.write("not a database")

    with pytest.raises(BackupError):
        restore_db(fake_backup)

    leftover = glob.glob(os.path.join(str(tmp_path), ".termstory_restore_*"))
    assert leftover == [], "Leftover temp files: {}".format(leftover)


def test_restore_failed_preserves_all_active_data(tmp_path, monkeypatch):
    """After a failed restore, every table in the active DB must be intact."""
    db_path = str(tmp_path / "live.db")
    _patch_db_path(monkeypatch, db_path)
    _seed_db(db_path, "Preserve Me")

    fake_backup = str(tmp_path / "not_sqlite.db")
    with open(fake_backup, "w") as f:
        f.write("corrupt")

    with pytest.raises(BackupError):
        restore_db(fake_backup)

    assert _read_first_project_name(db_path) == "Preserve Me"
    assert _count_rows(db_path, "projects") == 1
    assert _count_rows(db_path, "sessions") == 1
    assert _count_rows(db_path, "commands") == 1
