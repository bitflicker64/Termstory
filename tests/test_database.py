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


def test_database_profiler_logs_queries(tmp_path):
    db_file = tmp_path / "test_profiler.db"
    db = Database(str(db_file))
    db.init_db()
    
    # We should have captured some queries during init_db()
    assert len(db.query_logs) > 0
    
    # Let's run a custom query
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM projects")
    conn.close()
    
    # The select query should be logged
    queries = [log["sql"] for log in db.query_logs]
    assert any("SELECT * FROM projects" in q for q in queries)
    assert all(isinstance(log["duration"], float) for log in db.query_logs)


def test_database_query_logs_are_bounded():
    db = Database(":memory:")
    db.max_query_log = 10

    for i in range(16):
        db.log_query(f"SELECT {i}", 0.001)

    assert len(db.query_logs) <= db.max_query_log
    assert db.query_logs[0]["sql"] == "SELECT 10"
    assert db.query_logs[-1]["sql"] == "SELECT 15"



def test_database_uses_default_timeout_when_config_unset(tmp_path, monkeypatch):
    """Database() must fall back to DEFAULT_DB_TIMEOUT (30.0) when config.json
    has no db_timeout key."""
    monkeypatch.setattr(
        "termstory.database.load_config",
        lambda: {"max_query_log": 10000},  # no db_timeout key present
    )
    db_file = tmp_path / "test_default_timeout.db"
    db = Database(str(db_file))
    assert db.db_timeout == Database.DEFAULT_DB_TIMEOUT
    assert db.db_timeout == 30.0


def test_database_reads_db_timeout_from_config(tmp_path, monkeypatch):
    """Database() must read db_timeout from config.json instead of the
    hardcoded 30.0 literal."""
    monkeypatch.setattr(
        "termstory.database.load_config",
        lambda: {"max_query_log": 10000, "db_timeout": 90.0},
    )
    db_file = tmp_path / "test_custom_timeout.db"
    db = Database(str(db_file))
    assert db.db_timeout == 90.0


def test_database_get_connection_passes_configured_timeout(tmp_path, monkeypatch):
    """get_connection() must pass self.db_timeout to sqlite3.connect(),
    not the old hardcoded 30.0 literal."""
    import sqlite3

    monkeypatch.setattr(
        "termstory.database.load_config",
        lambda: {"max_query_log": 10000, "db_timeout": 5.0},
    )
    db_file = tmp_path / "test_connect_timeout.db"
    db = Database(str(db_file))

    captured_kwargs = {}
    real_connect = sqlite3.connect

    def spy_connect(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return real_connect(*args, **kwargs)

    monkeypatch.setattr("termstory.database.sqlite3.connect", spy_connect)

    conn = db.get_connection()
    conn.close()

    assert captured_kwargs.get("timeout") == 5.0


def test_database_clamps_non_positive_db_timeout(tmp_path, monkeypatch):
    """A zero or negative db_timeout in config must not be passed through
    to sqlite3.connect(), it should fall back to DEFAULT_DB_TIMEOUT."""
    for bad_value in (0, -1.0):
        monkeypatch.setattr(
            "termstory.database.load_config",
            lambda v=bad_value: {"max_query_log": 10000, "db_timeout": v},
        )
        db_file = tmp_path / f"test_bad_timeout_{bad_value}.db"
        db = Database(str(db_file))
        assert db.db_timeout == Database.DEFAULT_DB_TIMEOUT, f"expected default for db_timeout={bad_value!r}"


def test_database_ignores_malformed_db_timeout(tmp_path, monkeypatch):
    """A non-numeric db_timeout in config must not crash Database.__init__
    and must fall back to DEFAULT_DB_TIMEOUT."""
    monkeypatch.setattr(
        "termstory.database.load_config",
        lambda: {"max_query_log": 10000, "db_timeout": "not-a-number"},
    )
    db_file = tmp_path / "test_bad_timeout.db"
    db = Database(str(db_file))
    assert db.db_timeout == Database.DEFAULT_DB_TIMEOUT


def test_database_still_works_end_to_end_with_custom_timeout(tmp_path, monkeypatch):
    """Regression guard: a Database configured with a custom db_timeout
    must still initialize and accept queries normally (existing tests
    green, per the issue's acceptance criteria)."""
    monkeypatch.setattr(
        "termstory.database.load_config",
        lambda: {"max_query_log": 10000, "db_timeout": 45.0},
    )
    db_file = tmp_path / "test_e2e_timeout.db"
    db = Database(str(db_file))
    db.init_db()

    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cursor.fetchall()}
    conn.close()

    assert "projects" in tables
    assert db.db_timeout == 45.0


def _seed_projects_and_sessions(db, now_ts, n_projects, sessions_per_project=1, path_prefix=""):
    """Helper: insert n_projects projects (each with sessions_per_project sessions)
    directly via SQL and return the list of project ids.

    Used to set up deterministic data for get_projects_by_ids tests.
    """
    conn = db.get_connection()
    cursor = conn.cursor()
    project_ids = []
    for i in range(n_projects):
        cursor.execute(
            "INSERT INTO projects (name, path, first_seen, last_seen, project_context) "
            "VALUES (?, ?, ?, ?, ?)",
            (f"Proj {i}", f"~/proj-{path_prefix}-{i}", now_ts, now_ts + i * 10, f"ctx-{i}"),
        )
        pid = cursor.lastrowid
        project_ids.append(pid)
        for j in range(sessions_per_project):
            cursor.execute(
                "INSERT INTO sessions (start_time, end_time, duration_seconds, project_id) "
                "VALUES (?, ?, ?, ?)",
                (now_ts + i * 1000 + j, now_ts + i * 1000 + j + 50, 50 + j, pid),
            )
    conn.commit()
    conn.close()
    return project_ids


def test_get_projects_by_ids_correctness(tmp_path):
    """get_projects_by_ids returns correct aggregates and metadata for:
    multiple projects with sessions, a project with zero sessions, multiple
    sessions for one project, and preserves the original project metadata."""
    db_file = tmp_path / "test_proj_by_ids.db"
    db = Database(str(db_file))
    db.init_db()

    now_ts = int(time.time())

    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO projects (name, path, first_seen, last_seen, project_context) "
        "VALUES (?, ?, ?, ?, ?)",
        ("Proj A", "~/proj-a", now_ts, now_ts + 10, "ctx-a"),
    )
    cursor.execute(
        "INSERT INTO projects (name, path, first_seen, last_seen, project_context) "
        "VALUES (?, ?, ?, ?, ?)",
        ("Proj B", "~/proj-b", now_ts, now_ts + 20, None),
    )
    cursor.execute(
        "INSERT INTO projects (name, path, first_seen, last_seen, project_context) "
        "VALUES (?, ?, ?, ?, ?)",
        ("Proj C", "~/proj-c", now_ts, now_ts + 30, "ctx-c"),
    )

    cursor.execute("SELECT id, path FROM projects ORDER BY id")
    path_to_id = {row[1]: row[0] for row in cursor.fetchall()}
    a_id = path_to_id["~/proj-a"]
    b_id = path_to_id["~/proj-b"]
    c_id = path_to_id["~/proj-c"]

    # Two sessions for Proj A (durations 100 and 200 -> count=2, total=300)
    cursor.execute(
        "INSERT INTO sessions (start_time, end_time, duration_seconds, project_id) "
        "VALUES (?, ?, ?, ?)",
        (now_ts, now_ts + 100, 100, a_id),
    )
    cursor.execute(
        "INSERT INTO sessions (start_time, end_time, duration_seconds, project_id) "
        "VALUES (?, ?, ?, ?)",
        (now_ts + 200, now_ts + 400, 200, a_id),
    )
    # One session for Proj B (duration 300 -> count=1, total=300)
    cursor.execute(
        "INSERT INTO sessions (start_time, end_time, duration_seconds, project_id) "
        "VALUES (?, ?, ?, ?)",
        (now_ts, now_ts + 300, 300, b_id),
    )
    # Proj C: intentionally no sessions (zero-session project)
    conn.commit()
    conn.close()

    # Pass ids in non-sorted order to verify ORDER BY p.id stabilizes output
    projects = db.get_projects_by_ids([c_id, a_id, b_id])

    assert len(projects) == 3
    by_id = {p.id: p for p in projects}

    # session_count correctness
    assert by_id[a_id].session_count == 2
    assert by_id[b_id].session_count == 1
    assert by_id[c_id].session_count == 0

    # total_time correctness
    assert by_id[a_id].total_time == 300
    assert by_id[b_id].total_time == 300
    assert by_id[c_id].total_time == 0

    # zero-session project must still be returned with zeroed aggregates
    assert c_id in by_id

    # existing project metadata remains unchanged
    assert by_id[a_id].name == "Proj A"
    assert by_id[a_id].path == "~/proj-a"
    assert by_id[a_id].first_seen == now_ts
    assert by_id[a_id].last_seen == now_ts + 10
    assert by_id[a_id].project_context == "ctx-a"
    assert by_id[b_id].name == "Proj B"
    assert by_id[b_id].project_context is None
    assert by_id[c_id].name == "Proj C"
    assert by_id[c_id].project_context == "ctx-c"

    # ORDER BY p.id is deterministic regardless of input order
    assert [p.id for p in projects] == sorted([a_id, b_id, c_id])


def test_get_projects_by_ids_empty(tmp_path):
    """Empty project_ids must return [] and execute no SQL."""
    db_file = tmp_path / "test_proj_empty.db"
    db = Database(str(db_file))
    db.init_db()

    db.query_logs.clear()
    assert db.get_projects_by_ids([]) == []
    # No connection/queries should run for an empty id list.
    assert len(db.query_logs) == 0


def _bulk_project_query_count(logs):
    """Count the relevant data-fetch SQL issued by get_projects_by_ids(): the
    single bulk aggregate (projects LEFT JOIN sessions).

    This excludes incidental connection-setup SQL such as the
    ``PRAGMA foreign_keys = ON`` that get_connection() issues. That PRAGMA is
    routed through the query-logging cursor only on some Python/sqlite versions
    (e.g. the 3.9/3.10 CI jobs) and not others, so counting *all* logged
    statements would be version-dependent. Counting only the relevant SELECT is
    robust across versions and still proves the N+1 pattern is gone.
    """
    return [
        entry["sql"]
        for entry in logs
        if "FROM projects" in entry["sql"] and "LEFT JOIN sessions" in entry["sql"]
    ]


def test_get_projects_by_ids_query_count_is_constant(tmp_path):
    """Regression/perf test for issue #423.

    get_projects_by_ids() must issue a bounded, constant number of relevant
    SQL queries regardless of how many project ids it is given (NOT 1 + N).
    The old implementation issued one SELECT ... FROM projects plus a
    per-project SELECT ... FROM sessions WHERE project_id = ? for every id, so
    the count grew linearly with N. Compare a small (N=3) and a large (N=16)
    workload: both must return the right projects and issue the same, bounded
    number of relevant queries.
    """
    db_file = tmp_path / "test_proj_perf.db"
    db = Database(str(db_file))
    db.init_db()
    now_ts = int(time.time())

    # Small workload: 3 projects, one session each (duration 50).
    small_ids = _seed_projects_and_sessions(db, now_ts, 3, sessions_per_project=1, path_prefix="a")
    db.query_logs.clear()
    projects_small = db.get_projects_by_ids(small_ids)
    small_logs = list(db.query_logs)
    small_relevant = _bulk_project_query_count(small_logs)

    assert len(projects_small) == 3
    assert all(p.session_count == 1 for p in projects_small)
    assert all(p.total_time == 50 for p in projects_small)

    # Larger workload: 12 projects with 3 sessions each (50/51/52 => 153s),
    # plus a zero-session project, to exercise the LEFT JOIN and zero-session
    # preservation without adding queries.
    large_ids = _seed_projects_and_sessions(db, now_ts, 12, sessions_per_project=3, path_prefix="b")
    zero_ids = _seed_projects_and_sessions(db, now_ts, 1, sessions_per_project=0, path_prefix="c")
    all_ids = small_ids + large_ids + zero_ids
    db.query_logs.clear()
    projects_large = db.get_projects_by_ids(all_ids)
    large_logs = list(db.query_logs)
    large_relevant = _bulk_project_query_count(large_logs)

    assert len(projects_large) == len(all_ids)
    # Multiple sessions for one project: 50 + 51 + 52 = 153s.
    large_proj_ids = set(large_ids)
    large_projs = [p for p in projects_large if p.id in large_proj_ids]
    assert all(p.session_count == 3 for p in large_projs)
    assert all(p.total_time == 153 for p in large_projs)
    # Zero-session project still returned with zeroed aggregates (LEFT JOIN).
    zero_proj = next(p for p in projects_large if p.id == zero_ids[0])
    assert zero_proj.session_count == 0
    assert zero_proj.total_time == 0

    # --- Core O(1) regression assertions -----------------------------------
    # Exactly ONE bulk aggregate query for N ids, independent of N. The old
    # N+1 implementation has no LEFT JOIN, so this filter matches 0 there --
    # this alone rejects a regression to the old behavior.
    assert len(small_relevant) == 1
    assert len(large_relevant) == 1
    assert len(small_relevant) == len(large_relevant)  # constant regardless of N

    # The per-project session aggregate that characterized the old N+1 pattern
    # (SELECT ... FROM sessions WHERE project_id = ?) must be absent entirely.
    n_plus_1 = [
        e["sql"] for e in large_logs
        if "FROM sessions" in e["sql"] and "WHERE project_id = ?" in e["sql"]
    ]
    assert n_plus_1 == []  # would hold N entries under the old impl

    # Total statements issued must be bounded and NOT grow with N. A constant
    # connection-setup PRAGMA may be logged on some Python/sqlite versions, so
    # compare the two workloads and demand a bound well below 1 + N.
    assert len(large_logs) == len(small_logs)  # O(1): constant across input sizes
    assert len(large_logs) < len(all_ids)  # bounded: not 1 + N
    assert len(large_logs) <= 2  # 1 SELECT + optional version-dependent PRAGMA

