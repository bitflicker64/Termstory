"""Regression tests for issue #138: COALESCE(-1) sentinel collision (part 1)
and over-broad orphan-session pruning in save_data (part 3).

Part 2 (unguarded row[0] in the save_data conflict-recovery path) already has
dedicated coverage in tests/test_save_data_none_safety.py and needed no code
change. The None-guard and RuntimeError it describes were already present.
"""
import sqlite3

from termstory.database import Database
from termstory.models import Command, Session


# ── Part 1: sentinel collision ───────────────────────────────────────────────
def test_negative_one_project_id_does_not_collide_with_null(tmp_path):
    """A session with project_id = -1 and a session with project_id IS NULL,
    at the same start_time, must be able to coexist. Under the old
    UNIQUE INDEX ... (start_time, COALESCE(project_id, -1)) they would be
    treated as the same key and the second INSERT would raise IntegrityError.
    """
    db_file = tmp_path / "sentinel.db"
    db = Database(str(db_file))
    db.init_db()

    conn = db.get_connection()
    cursor = conn.cursor()
    # Simulate a future/legacy code path that assigns project_id = -1
    # directly, satisfying the FK by giving that id a real projects row.
    cursor.execute(
        "INSERT INTO projects (id, name, path, first_seen, last_seen) "
        "VALUES (-1, 'sentinel-collision', '/sentinel', 1000, 1000)"
    )
    conn.commit()

    start_time = 1750000000
    cursor.execute(
        "INSERT INTO sessions (start_time, end_time, duration_seconds, project_id) "
        "VALUES (?, ?, ?, -1)",
        (start_time, start_time + 10, 10),
    )
    # This second INSERT is the one that would previously raise
    # sqlite3.IntegrityError under the COALESCE-based index.
    cursor.execute(
        "INSERT INTO sessions (start_time, end_time, duration_seconds, project_id) "
        "VALUES (?, ?, ?, NULL)",
        (start_time, start_time + 20, 20),
    )
    conn.commit()

    cursor.execute("SELECT project_id FROM sessions WHERE start_time = ?", (start_time,))
    project_ids = {row[0] for row in cursor.fetchall()}
    conn.close()

    assert project_ids == {-1, None}, (
        "a project_id = -1 session and a project_id IS NULL session sharing a "
        "start_time are different sessions and must both persist"
    )


def test_sentinel_index_replaced_with_partial_indexes(tmp_path):
    """The old COALESCE-based unique index must be gone, replaced by the two
    partial indexes, on both a fresh DB and a DB migrating from the old
    schema."""
    # Fresh DB
    fresh_db_file = tmp_path / "fresh.db"
    db = Database(str(fresh_db_file))
    db.init_db()
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='sessions'")
    index_names = {row[0] for row in cursor.fetchall()}
    conn.close()

    assert "idx_sessions_start_time_unique" not in index_names
    assert "idx_sessions_start_time_with_project" in index_names
    assert "idx_sessions_start_time_no_project" in index_names

    # DB that already has the old sentinel-based index from a prior version
    legacy_db_file = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(legacy_db_file))
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            start_time INTEGER NOT NULL,
            end_time INTEGER,
            duration_seconds INTEGER,
            project_id INTEGER
        )
    """)
    cursor.execute(
        "CREATE UNIQUE INDEX idx_sessions_start_time_unique "
        "ON sessions(start_time, COALESCE(project_id, -1))"
    )
    conn.commit()
    conn.close()

    db2 = Database(str(legacy_db_file))
    db2.init_db()
    conn = db2.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='sessions'")
    index_names_after_migration = {row[0] for row in cursor.fetchall()}
    conn.close()

    assert "idx_sessions_start_time_unique" not in index_names_after_migration
    assert "idx_sessions_start_time_with_project" in index_names_after_migration
    assert "idx_sessions_start_time_no_project" in index_names_after_migration


# ── Part 3: over-broad orphan pruning ───────────────────────────────────────
def test_unique_empty_session_is_not_pruned(tmp_path):
    """A session with zero commands that is NOT a duplicate of any other
    session (its start_time is unique in the table) must survive the
    orphan-prune DELETE in save_data."""
    db_file = tmp_path / "empty_session.db"
    db = Database(str(db_file))
    db.init_db()

    cmd = Command(id=None, session_id=1, timestamp=1750000000, command="ls")
    s1 = Session(id=1, start_time=1750000000, end_time=1750000010,
                 duration_seconds=10, project_id=None, commands=[cmd])
    db.save_data([], [s1], [cmd])

    conn = db.get_connection()
    cursor = conn.cursor()
    # A genuinely empty session, e.g. a terminal opened and closed with no
    # commands run. Inserted directly, not a duplicate of anything.
    cursor.execute(
        "INSERT INTO sessions (start_time, end_time, duration_seconds, project_id) "
        "VALUES (?, ?, ?, NULL)",
        (1750001000, 1750001001, 1),
    )
    conn.commit()
    conn.close()

    # A second save_data call is what actually runs the orphan-prune DELETE.
    cmd2 = Command(id=None, session_id=2, timestamp=1750002000, command="pwd")
    s2 = Session(id=2, start_time=1750002000, end_time=1750002010,
                 duration_seconds=10, project_id=None, commands=[cmd2])
    db.save_data([], [s2], [cmd2])

    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT start_time FROM sessions ORDER BY start_time")
    remaining = [row[0] for row in cursor.fetchall()]
    conn.close()

    assert 1750001000 in remaining, (
        "a unique, non-duplicate empty session must not be pruned just for "
        "having zero commands"
    )
