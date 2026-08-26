import time
from termstory.database import Database
from termstory.models import Project, Session, Command

def test_database_commits_and_search(tmp_path, monkeypatch):
    monkeypatch.setenv("TERMSTORY_DATE_OVERRIDE", "2026-06-06 12:00:00")
    db_file = tmp_path / "test_search.db"
    db = Database(str(db_file))
    db.init_db()
    
    from datetime import datetime
    now = int(datetime(2026, 6, 6, 12, 0, 0).timestamp())
    
    # 1. Save projects
    p1 = Project(id=1, name="Apache HugeGraph", path="~/projects/incubator-hugegraph", first_seen=now, last_seen=now, session_count=1, total_time=100)
    p2 = Project(id=2, name="Termstory CLI", path="~/projects/termstory", first_seen=now, last_seen=now, session_count=1, total_time=150)
    
    # 2. Save sessions and commands
    cmd1 = Command(timestamp=now, command="docker ps -a", session_id=1, project_id=1)
    s1 = Session(id=1, start_time=now, end_time=now + 100, duration_seconds=100, project_id=1, commands=[cmd1])
    
    cmd2 = Command(timestamp=now + 5000, command="pytest tests/", session_id=2, project_id=2)
    s2 = Session(id=2, start_time=now + 5000, end_time=now + 5100, duration_seconds=100, project_id=2, commands=[cmd2])
    
    db.save_data([p1, p2], [s1, s2], [cmd1, cmd2])
    
    # 3. Save git commits
    commits_p1 = [
        {"hash": "1111111111111111111111111111111111111111", "timestamp": now + 20, "message": "feat: Add docker health checks", "cleaned_message": "Add docker health checks"},
        {"hash": "2222222222222222222222222222222222222222", "timestamp": now - 3600, "message": "docs: document raft config", "cleaned_message": "Document raft config"}
    ]
    db.save_commits(p1.id, commits_p1)
    
    commits_p2 = [
        {"hash": "3333333333333333333333333333333333333333", "timestamp": now + 5050, "message": "fix: fix tests for cli run", "cleaned_message": "Fix tests for cli run"}
    ]
    db.save_commits(p2.id, commits_p2)
    
    # 4. Verify commits are fetched inside get_today_sessions and get_session_commits
    sessions_today = db.get_today_sessions()
    assert len(sessions_today) >= 2
    
    # Session 1 should have 1 commit mapped (the docker health check commit, which falls in the time range)
    s1_retrieved = next(s for s in sessions_today if s.id == 1)
    assert len(s1_retrieved.commits) == 1
    assert s1_retrieved.commits[0]["hash"] == "1111111111111111111111111111111111111111"
    assert s1_retrieved.commits[0]["cleaned_message"] == "Add docker health checks"
    
    # 5. Test search_sessions matching commit message
    results = db.search_sessions("health")
    assert len(results) == 1
    assert results[0]["session_id"] == 1
    assert results[0]["project_name"] == "Apache HugeGraph"
    assert len(results[0]["matching_commits"]) == 1
    assert results[0]["matching_commits"][0]["hash"] == "1111111111111111111111111111111111111111"
    
    # 6. Test search_sessions matching command text
    results = db.search_sessions("pytest")
    assert len(results) == 1
    assert results[0]["session_id"] == 2
    assert results[0]["project_name"] == "Termstory CLI"
    assert "pytest tests/" in results[0]["matching_commands"]
    
    # 7. Test search_sessions matching project name
    results = db.search_sessions("Termstory")
    assert len(results) == 1
    assert results[0]["session_id"] == 2
    
    # 8. Test filters
    # Filter by project
    results = db.search_sessions("tests", project_filter="Termstory")
    assert len(results) == 1
    
    results = db.search_sessions("tests", project_filter="HugeGraph")
    assert len(results) == 0

    # 9. Test search_sessions matching session AI summary
    db.save_session_ai_summary(1, "Refactored Docker process supervision scripts")
    
    results = db.search_sessions("supervision")
    assert len(results) == 1
    assert results[0]["session_id"] == 1
    # 10. Test active session (end_time is None) doesn't crash search_sessions
    cmd3 = Command(timestamp=now + 20000, command="python3 script.py", session_id=3, project_id=1)
    s3 = Session(id=3, start_time=now + 20000, end_time=None, duration_seconds=0, project_id=1, commands=[cmd3])
    db.save_data([], [s3], [cmd3])
    
    results = db.search_sessions("script.py")
    assert len(results) == 1
    assert results[0]["session_id"] == 3



def test_project_filter_matches_per_command_project(tmp_path, monkeypatch):
    """#339: project-filtered search must surface sessions whose *commands*
    ran in the filtered project, even when the session's final project is
    different (i.e. the session ``cd``-ed between projects mid-stream).
    """
    monkeypatch.setenv("TERMSTORY_DATE_OVERRIDE", "2026-06-06 12:00:00")
    db_file = tmp_path / "test_per_cmd_search.db"
    db = Database(str(db_file))
    db.init_db()

    from datetime import datetime
    now = int(datetime(2026, 6, 6, 12, 0, 0).timestamp())

    # Two distinct projects
    p_acme = Project(
        id=1, name="Acme Billing", path="~/Projects/acme-billing",
        first_seen=now, last_seen=now, session_count=1, total_time=100,
    )
    p_mobile = Project(
        id=2, name="Mobile Companion", path="~/Projects/mobile-companion",
        first_seen=now, last_seen=now, session_count=1, total_time=100,
    )

    # Session that ran in Acme, then cd'd to Mobile and finished there.
    # session.project_id is Mobile (the final project), but the matching
    # command "stripe ..." ran in Acme and is attributed to Acme via
    # cmd.project_id == p_acme.id.
    cmd1 = Command(timestamp=now, command="stripe curl https://api.stripe.com/v1/charges", session_id=1, project_id=1)
    cmd2 = Command(timestamp=now + 100, command="cd ~/Projects/mobile-companion", session_id=1, project_id=1)
    cmd3 = Command(timestamp=now + 200, command="npm run test", session_id=1, project_id=2)
    s1 = Session(
        id=1, start_time=now, end_time=now + 300, duration_seconds=300,
        project_id=2, commands=[cmd1, cmd2, cmd3],
    )

    db.save_data([p_acme, p_mobile], [s1], [cmd1, cmd2, cmd3])

    from termstory.search import advanced_search

    # Filter by Acme: should return s1 because cmd1 ran in Acme.
    results = advanced_search(db, query="stripe", project_filter="Acme Billing")
    assert len(results) == 1
    assert results[0]["session_id"] == 1

    # Filter by Mobile: should also return s1 because cmd3 ran in Mobile
    # (and the session itself ended in Mobile).
    results = advanced_search(db, query="npm", project_filter="Mobile Companion")
    assert len(results) == 1

    # Filter by a project that didn't see this command at all — no match.
    results = advanced_search(db, query="stripe", project_filter="Nonexistent Project")
    assert len(results) == 0


def test_search_result_attributed_to_matched_command_project(tmp_path, monkeypatch):
    """#457: a search hit must carry the project of the *command* that matched,
    not the session's final project. One session runs in Acme Billing, then
    cd's into Mobile Companion and finishes there; searching for a command
    that ran in Acme must report Acme Billing even though session.project_id
    points at Mobile Companion. Verified across all three search backends.
    """
    monkeypatch.setenv("TERMSTORY_DATE_OVERRIDE", "2026-06-06 12:00:00")
    db_file = tmp_path / "test_per_cmd_attribution.db"
    db = Database(str(db_file))
    db.init_db()

    from datetime import datetime
    now = int(datetime(2026, 6, 6, 12, 0, 0).timestamp())

    p_acme = Project(
        id=1, name="Acme Billing", path="~/Projects/acme-billing",
        first_seen=now, last_seen=now, session_count=1, total_time=100,
    )
    p_mobile = Project(
        id=2, name="Mobile Companion", path="~/Projects/mobile-companion",
        first_seen=now, last_seen=now, session_count=1, total_time=100,
    )

    # Session ends in Mobile, but the "stripe" command ran in Acme.
    cmd1 = Command(timestamp=now, command="stripe curl https://api.stripe.com/v1/charges", session_id=1, project_id=1)
    cmd2 = Command(timestamp=now + 100, command="cd ~/Projects/mobile-companion", session_id=1, project_id=1)
    cmd3 = Command(timestamp=now + 200, command="npm run test", session_id=1, project_id=2)
    s1 = Session(
        id=1, start_time=now, end_time=now + 300, duration_seconds=300,
        project_id=2, commands=[cmd1, cmd2, cmd3],
    )

    db.save_data([p_acme, p_mobile], [s1], [cmd1, cmd2, cmd3])

    from termstory.search import advanced_search

    def run_all_backends(query, **kwargs):
        """Yield (backend_name, results) for the two FTS search implementations."""
        # _search_new_fts5
        yield "new_fts5", advanced_search(db, query=query, fts=True, **kwargs)
        # _search_fts5
        yield "fts5", advanced_search(db, query=query, **kwargs)

    for backend, results in run_all_backends("stripe"):
        assert len(results) == 1, f"{backend}: expected exactly one result"
        assert results[0]["session_id"] == 1
        # The matched command ran in Acme, even though the session ended in
        # Mobile. save_data remaps project ids, so compare against the
        # persisted Project objects.
        assert results[0]["project_name"] == "Acme Billing", (
            f"{backend}: expected matched-command project, got {results[0]['project_name']}"
        )
        assert results[0]["project_id"] == p_acme.id

    for backend, results in run_all_backends("npm"):
        assert len(results) == 1, f"{backend}: expected exactly one result"
        assert results[0]["session_id"] == 1
        assert results[0]["project_name"] == "Mobile Companion", (
            f"{backend}: expected matched-command project, got {results[0]['project_name']}"
        )
        assert results[0]["project_id"] == p_mobile.id

    # Force the non-FTS fallback path (_search_standard) by removing the index.
    conn = db.get_connection()
    conn.execute("DROP TABLE IF EXISTS search_index")
    conn.commit()
    conn.close()

    results = advanced_search(db, query="stripe")
    assert len(results) == 1
    assert results[0]["project_name"] == "Acme Billing"
    assert results[0]["project_id"] == p_acme.id

    results = advanced_search(db, query="npm")
    assert len(results) == 1
    assert results[0]["project_name"] == "Mobile Companion"
    assert results[0]["project_id"] == p_mobile.id

    # --project filtering keeps working and the surviving result still carries
    # the matched command's attribution.
    results = advanced_search(db, query="stripe", project_filter="Acme")
    assert len(results) == 1
    assert results[0]["project_name"] == "Acme Billing"


def test_search_falls_back_to_session_project_without_command_attribution(tmp_path, monkeypatch):
    """#457: when the matched command carries NO explicit per-command project,
    the result must keep the session-level project (existing behaviour)."""
    monkeypatch.setenv("TERMSTORY_DATE_OVERRIDE", "2026-06-06 12:00:00")
    db_file = tmp_path / "test_session_fallback.db"
    db = Database(str(db_file))
    db.init_db()

    from datetime import datetime
    now = int(datetime(2026, 6, 6, 12, 0, 0).timestamp())

    p_mobile = Project(
        id=2, name="Mobile Companion", path="~/Projects/mobile-companion",
        first_seen=now, last_seen=now, session_count=1, total_time=100,
    )

    # Matching command without any per-command attribution (project_id=None).
    cmd1 = Command(timestamp=now, command="terraform plan -out tfplan", session_id=1, project_id=None)
    s1 = Session(
        id=1, start_time=now, end_time=now + 300, duration_seconds=300,
        project_id=2, commands=[cmd1],
    )

    db.save_data([p_mobile], [s1], [cmd1])

    from termstory.search import advanced_search

    def run_all_backends(query, **kwargs):
        yield "new_fts5", advanced_search(db, query=query, fts=True, **kwargs)
        yield "fts5", advanced_search(db, query=query, **kwargs)

    for backend, results in run_all_backends("terraform"):
        assert len(results) == 1, f"{backend}: expected exactly one result"
        assert results[0]["session_id"] == 1
        assert results[0]["project_name"] == "Mobile Companion", (
            f"{backend}: expected session-project fallback, got {results[0]['project_name']}"
        )
        # save_data remaps project ids, so compare against the persisted one.
        assert results[0]["project_id"] == p_mobile.id

    # Non-FTS fallback path (_search_standard).
    conn = db.get_connection()
    conn.execute("DROP TABLE IF EXISTS search_index")
    conn.commit()
    conn.close()

    results = advanced_search(db, query="terraform")
    assert len(results) == 1
    assert results[0]["project_name"] == "Mobile Companion"
    assert results[0]["project_id"] == p_mobile.id


def test_advanced_search(tmp_path, monkeypatch):
    monkeypatch.setenv("TERMSTORY_DATE_OVERRIDE", "2026-06-06 12:00:00")
    db_file = tmp_path / "test_advanced_search.db"
    db = Database(str(db_file))
    db.init_db()
    
    from datetime import datetime
    now = int(datetime(2026, 6, 6, 12, 0, 0).timestamp())
    
    p1 = Project(id=1, name="Project A", path="~/projects/a", first_seen=now, last_seen=now, session_count=1, total_time=100)
    p2 = Project(id=2, name="Project B", path="~/projects/b", first_seen=now, last_seen=now, session_count=1, total_time=150)
    
    cmd1 = Command(timestamp=now, command="docker compose up", session_id=1, project_id=1)
    s1 = Session(id=1, start_time=now, end_time=now + 100, duration_seconds=100, project_id=1, commands=[cmd1])
    s1.tags = "deploy,debug"
    
    cmd2 = Command(timestamp=now + 10000, command="npm run build", session_id=2, project_id=2)
    s2 = Session(id=2, start_time=now + 10000, end_time=now + 10100, duration_seconds=100, project_id=2, commands=[cmd2])
    s2.tags = "setup,test"
    
    db.save_data([p1, p2], [s1, s2], [cmd1, cmd2])
    
    from termstory.search import advanced_search
    
    # 1. Search with no query but project filter
    results = advanced_search(db, project_filter="Project A")
    assert len(results) == 1
    assert results[0]["session_id"] == 1
    
    # 2. Search with query and project filter
    results = advanced_search(db, query="compose", project_filter="Project A")
    assert len(results) == 1
    
    # 3. Search with tag filters
    results = advanced_search(db, tag_filters=["deploy"])
    assert len(results) == 1
    assert results[0]["session_id"] == 1
    
    # 4. Search with multiple tag filters (match all)
    results = advanced_search(db, tag_filters=["deploy", "debug"])
    assert len(results) == 1
    
    # Non-existent combo
    results = advanced_search(db, tag_filters=["deploy", "setup"])
    assert len(results) == 0
    
    # 5. Search with date ranges
    # Since filter
    results = advanced_search(db, since_ts=now + 5000)
    assert len(results) == 1
    assert results[0]["session_id"] == 2
    
    # Until filter
    results = advanced_search(db, until_ts=now + 5000)
    assert len(results) == 1
    assert results[0]["session_id"] == 1
    
    # Both filters
    results = advanced_search(db, since_ts=now - 100, until_ts=now + 15000)
    assert len(results) == 2


# ---------------------------------------------------------------------------
# Issue #467: recovery from stale or incomplete FTS indexes
# ---------------------------------------------------------------------------

def _build_probe_db(tmp_path, session_tags=None):
    """One project/session/command containing the distinctive token
    'probemarker' so stale-index scenarios have a deterministic needle."""
    from datetime import datetime

    db_file = tmp_path / "probe.db"
    db = Database(str(db_file))
    db.init_db()
    now = int(datetime(2026, 6, 6, 12, 0, 0).timestamp())

    p1 = Project(id=1, name="Probe Project", path="~/projects/probe",
                 first_seen=now, last_seen=now, session_count=1, total_time=100)
    cmd1 = Command(timestamp=now, command="probemarker deploy script", session_id=1, project_id=1)
    s1 = Session(id=1, start_time=now, end_time=now + 100, duration_seconds=100,
                 project_id=1, commands=[cmd1])
    if session_tags:
        s1.tags = session_tags
    db.save_data([p1], [s1], [cmd1])
    return db, now


def _raw_insert_command(db, session_id, project_id, command, timestamp):
    """Insert a command row directly into the authoritative commands table,
    bypassing Database.save_data's manual search_index synchronization (and,
    if triggers were dropped, the commands_fts triggers as well)."""
    conn = db.get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO commands (command, timestamp, exit_code, session_id, project_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (command, timestamp, 0, session_id, project_id),
        )
        conn.commit()
    finally:
        conn.close()


def _spy_backend(monkeypatch, name):
    """Wrap termstory.search.<name> to record whether that backend ran while
    fully preserving its behavior."""
    import termstory.search as search_mod

    calls = []
    original = getattr(search_mod, name)

    def wrapper(*args, **kwargs):
        calls.append(name)
        return original(*args, **kwargs)

    monkeypatch.setattr(search_mod, name, wrapper)
    return calls


def test_healthy_index_uses_fts_paths(tmp_path, monkeypatch):
    """#467 case A: with an intact index both FTS backends serve queries and
    no standard-SQL recovery kicks in."""
    db, now = _build_probe_db(tmp_path)
    from termstory.search import advanced_search

    std_calls = _spy_backend(monkeypatch, "_search_standard")
    fts_calls = _spy_backend(monkeypatch, "_search_fts5")
    new_calls = _spy_backend(monkeypatch, "_search_new_fts5")

    # Default backend (_search_fts5 via search_index).
    results = advanced_search(db, query="probemarker")
    assert len(results) == 1
    assert results[0]["session_id"] == 1
    assert results[0]["matching_commands"] == ["probemarker deploy script"]
    assert fts_calls == ["_search_fts5"]
    assert std_calls == []

    # New backend (_search_new_fts5 via commands_fts).
    results = advanced_search(db, query="probemarker", fts=True)
    assert len(results) == 1
    assert results[0]["session_id"] == 1
    assert new_calls == ["_search_new_fts5"]
    assert std_calls == []


def test_healthy_zero_result_query_is_not_treated_as_corruption(tmp_path, monkeypatch):
    """A healthy index legitimately matching nothing must stay on the FTS path:
    recovery may never trigger off an empty result set (#467)."""
    db, now = _build_probe_db(tmp_path)
    from termstory.search import advanced_search

    std_calls = _spy_backend(monkeypatch, "_search_standard")
    fts_calls = _spy_backend(monkeypatch, "_search_fts5")

    results = advanced_search(db, query="zzznomatchzzz")
    assert results == []
    assert std_calls == []                    # no fallback for legit zero results
    assert fts_calls == ["_search_fts5"]      # healthy index still served it


def test_missing_search_index_entry_recovers_via_standard_sql(tmp_path, monkeypatch):
    """#467 case B: a command written into the authoritative table without
    going through save_data never reaches the manually-synced search_index.
    Search must surface that session instead of silently omitting it."""
    db, now = _build_probe_db(tmp_path)
    from termstory.search import advanced_search

    _raw_insert_command(db, session_id=1, project_id=1,
                        command="orphanmarker audit logs", timestamp=now + 10)

    # Sanity: the index really is incomplete relative to authoritative data.
    conn = db.get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM search_index WHERE type = 'command'")
        indexed = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM commands WHERE session_id IS NOT NULL")
        authoritative = cursor.fetchone()[0]
    finally:
        conn.close()
    assert indexed < authoritative

    fts_calls = _spy_backend(monkeypatch, "_search_fts5")
    std_calls = _spy_backend(monkeypatch, "_search_standard")

    results = advanced_search(db, query="orphanmarker")
    assert len(results) == 1
    assert results[0]["session_id"] == 1
    assert any("orphanmarker" in c for c in results[0]["matching_commands"])
    # Recovery routed through the authoritative standard SQL path and the
    # demonstrably incomplete index was skipped.
    assert std_calls == ["_search_standard"]
    assert fts_calls == []


def test_stale_commands_fts_recovers_via_standard_sql(tmp_path, monkeypatch):
    """#467 case C: with the commands_fts sync trigger dropped, a raw insert
    leaves the external-content index permanently behind the authoritative
    commands table; search must not silently omit those records."""
    db, now = _build_probe_db(tmp_path)
    from termstory.search import advanced_search

    conn = db.get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("DROP TRIGGER IF EXISTS commands_ai")
        cursor.execute(
            "INSERT INTO commands (command, timestamp, exit_code, session_id, project_id) "
            "VALUES (?, ?, ?, ?, ?)",
            ("ftsorphanmarker rebuild cache", now + 10, 0, 1, 1),
        )
        conn.commit()
    finally:
        conn.close()

    new_calls = _spy_backend(monkeypatch, "_search_new_fts5")
    std_calls = _spy_backend(monkeypatch, "_search_standard")

    results = advanced_search(db, query="ftsorphanmarker", fts=True)
    assert len(results) == 1
    assert results[0]["session_id"] == 1
    assert any("ftsorphanmarker" in c for c in results[0]["matching_commands"])
    assert new_calls == []
    assert std_calls == ["_search_standard"]


def test_filters_remain_correct_during_recovery(tmp_path, monkeypatch):
    """#467 case D: while recovering through the standard SQL path, project,
    date, and tag filters keep working — including per-command project
    attribution (#457/#339), which must not degrade to session-level."""
    from datetime import datetime

    db_file = tmp_path / "recovery_filters.db"
    db = Database(str(db_file))
    db.init_db()
    now = int(datetime(2026, 6, 6, 12, 0, 0).timestamp())

    p_acme = Project(id=1, name="Acme Billing", path="~/projects/acme",
                     first_seen=now, last_seen=now, session_count=1, total_time=100)
    p_other = Project(id=2, name="Other Project", path="~/projects/other",
                      first_seen=now, last_seen=now, session_count=1, total_time=100)

    # Session starts in Acme, cd's to Other and finishes there (#457 shape):
    # session.project_id is Other, but the matching commands ran in Acme.
    cmd1 = Command(timestamp=now, command="recovmarker stripe charge", session_id=1, project_id=1)
    cmd2 = Command(timestamp=now + 50, command="cd ~/projects/other", session_id=1, project_id=1)
    s1 = Session(id=1, start_time=now, end_time=now + 100, duration_seconds=100,
                 project_id=2, commands=[cmd1, cmd2])
    s1.tags = "debug"

    db.save_data([p_acme, p_other], [s1], [cmd1, cmd2])

    # Make search_index stale w.r.t. a later raw command so recovery is active.
    _raw_insert_command(db, session_id=1, project_id=1,
                        command="recovmarker refund run", timestamp=now + 60)

    from termstory.search import advanced_search

    # Baseline: recovery actually triggered.
    results = advanced_search(db, query="recovmarker")
    assert len(results) == 1
    assert results[0]["session_id"] == 1

    # Project filter — per-command attribution survives recovery.
    results = advanced_search(db, query="recovmarker", project_filter="Acme Billing")
    assert len(results) == 1
    results = advanced_search(db, query="recovmarker", project_filter="Nonexistent Project")
    assert results == []

    # Date filters apply to session start_time as before.
    results = advanced_search(db, query="recovmarker", since_ts=now + 5000)
    assert results == []
    results = advanced_search(db, query="recovmarker", until_ts=now + 5000)
    assert len(results) == 1

    # Tag filter.
    results = advanced_search(db, query="recovmarker", tag_filters=["debug"])
    assert len(results) == 1
    results = advanced_search(db, query="recovmarker", tag_filters=["docs"])
    assert results == []


def test_operational_error_fallback_preserved(tmp_path, monkeypatch):
    """#467 case E: the pre-existing OperationalError fallback still routes to
    the next backend instead of raising out of advanced_search."""
    import sqlite3
    import termstory.search as search_mod

    db, now = _build_probe_db(tmp_path)
    from termstory.search import advanced_search

    def boom(*args, **kwargs):
        raise sqlite3.OperationalError("simulated FTS corruption")

    monkeypatch.setattr(search_mod, "_search_new_fts5", boom)
    results = advanced_search(db, query="probemarker", fts=True)
    assert len(results) == 1
    assert results[0]["session_id"] == 1

    # Fully unavailable FTS tables also fall back cleanly.
    conn = db.get_connection()
    try:
        conn.execute("DROP TABLE IF EXISTS search_index")
        conn.execute("DROP TABLE IF EXISTS commands_fts")
        conn.commit()
    finally:
        conn.close()

    results = advanced_search(db, query="probemarker", fts=True)
    assert len(results) == 1
    results = advanced_search(db, query="probemarker")
    assert len(results) == 1


def test_stale_command_text_recovers_via_standard_sql(tmp_path, monkeypatch):
    """#471 case: stale indexed TEXT with structurally identical counts/IDs.

    The authoritative command text is changed while the commands_au sync
    trigger is disabled, so the FTS row keeps the OLD tokens while IDs and
    row counts remain identical. Searching for the NEW text through the stale
    index would silently return nothing; search must detect the disabled
    synchronization and recover through standard SQL instead.
    """
    db, now = _build_probe_db(tmp_path)
    from termstory.search import advanced_search

    conn = db.get_connection()
    try:
        cursor = conn.cursor()
        # Disable ONLY the text-update sync path.
        cursor.execute("DROP TRIGGER IF EXISTS commands_au")
        cursor.execute(
            "UPDATE commands SET command = 'renovated payload marker' WHERE id = 1"
        )
        conn.commit()

        # Sanity: counts AND ids are still perfectly aligned, so any
        # COUNT/MAX-based structural check alone would call this healthy.
        cursor.execute("""
            SELECT (SELECT COUNT(*) FROM commands),
                   (SELECT COUNT(*) FROM commands_fts_docsize),
                   (SELECT MAX(id) FROM commands),
                   (SELECT MAX(id) FROM commands_fts_docsize)
        """)
        c_count, d_count, c_max, d_max = cursor.fetchone()
        assert c_count == d_count and c_max == d_max
    finally:
        conn.close()

    new_calls = _spy_backend(monkeypatch, "_search_new_fts5")
    std_calls = _spy_backend(monkeypatch, "_search_standard")

    results = advanced_search(db, query="renovated", fts=True)
    assert len(results) == 1
    assert results[0]["session_id"] == 1
    assert any("renovated payload marker" in c for c in results[0]["matching_commands"])
    # Stale-content index was skipped; recovery served authoritative data.
    assert new_calls == []
    assert std_calls == ["_search_standard"]


def test_offsetting_missing_and_extra_ids_detected(tmp_path, monkeypatch):
    """#471 case: same COUNT and same MAX(id) but different ID members.

    Simulates lost-delete + lost-insert drift: authoritative ids become
    {2, 3} while indexed ids are {1, 3}. Counts (2 == 2) and maxima (3 == 3)
    match, so the previous COUNT/MAX consistency check declared this index
    healthy even though doc 2 is missing from the index entirely. The sync
    triggers are restored afterwards so ONLY exact bidirectional membership
    equality can catch the drift.
    """
    from datetime import datetime

    db_file = tmp_path / "offsetting.db"
    db = Database(str(db_file))
    db.init_db()
    now = int(datetime(2026, 6, 6, 12, 0, 0).timestamp())

    p1 = Project(id=1, name="Probe Project", path="~/projects/probe",
                 first_seen=now, last_seen=now, session_count=1, total_time=100)
    cmd1 = Command(timestamp=now, command="alphamarker first probe", session_id=1, project_id=1)
    cmd2 = Command(timestamp=now + 10, command="betamarker second probe", session_id=1, project_id=1)
    s1 = Session(id=1, start_time=now, end_time=now + 100, duration_seconds=100,
                 project_id=1, commands=[cmd1, cmd2])
    db.save_data([p1], [s1], [cmd1, cmd2])  # healthy: ids {1, 2} on both sides

    from termstory.search import advanced_search

    conn = db.get_connection()
    try:
        cursor = conn.cursor()
        # Lose the insert/delete sync paths, then drift the two sides apart.
        cursor.execute("DROP TRIGGER IF EXISTS commands_ai")
        cursor.execute("DROP TRIGGER IF EXISTS commands_ad")
        cursor.execute("DROP TRIGGER IF EXISTS commands_au")
        cursor.execute("DELETE FROM commands WHERE id = 1")            # cmds {2}
        cursor.execute(
            "INSERT INTO commands (command, timestamp, exit_code, session_id, project_id) "
            "VALUES ('gammamarker third probe', ?, 0, 1, 1)", (now + 20,)
        )                                                               # cmds {2, 3}
        # Re-shape the indexed side to {1, 3}: drop real doc 2, forge doc 3.
        cursor.execute("DELETE FROM commands_fts_docsize WHERE id = 2")
        cursor.execute("INSERT INTO commands_fts_docsize (id, sz) VALUES (3, X'00')")

        # The exact flaw being regressed: COUNT and MAX both look healthy.
        cursor.execute("""
            SELECT (SELECT COUNT(*) FROM commands),
                   (SELECT COUNT(*) FROM commands_fts_docsize),
                   (SELECT MAX(id) FROM commands),
                   (SELECT MAX(id) FROM commands_fts_docsize)
        """)
        c_count, d_count, c_max, d_max = cursor.fetchone()
        assert (c_count, c_max) == (d_count, d_max) == (2, 3)

        # Restore the sync triggers so the trigger-presence half of the gate
        # passes and ONLY bidirectional membership equality can detect this.
        cursor.execute("""
            CREATE TRIGGER commands_ai AFTER INSERT ON commands BEGIN
                INSERT INTO commands_fts(rowid, command, exit_code)
                VALUES (new.id, new.command, new.exit_code);
            END;
        """)
        cursor.execute("""
            CREATE TRIGGER commands_ad AFTER DELETE ON commands BEGIN
                INSERT INTO commands_fts(commands_fts, rowid, command, exit_code)
                VALUES ('delete', old.id, old.command, old.exit_code);
            END;
        """)
        cursor.execute("""
            CREATE TRIGGER commands_au AFTER UPDATE OF command, exit_code ON commands BEGIN
                INSERT INTO commands_fts(commands_fts, rowid, command, exit_code)
                VALUES ('delete', old.id, old.command, old.exit_code);
                INSERT INTO commands_fts(rowid, command, exit_code)
                VALUES (new.id, new.command, new.exit_code);
            END;
        """)
        conn.commit()
    finally:
        conn.close()

    new_calls = _spy_backend(monkeypatch, "_search_new_fts5")
    fts_calls = _spy_backend(monkeypatch, "_search_fts5")
    std_calls = _spy_backend(monkeypatch, "_search_standard")

    # Doc 2 ("betamarker ...") is missing from commands_fts: the drifted
    # backend must be rejected even though COUNT/MAX look identical.
    # Additionally stale out the legacy index as well (one of its two
    # session-1 command rows removed), so NOTHING derived remains trustworthy
    # and recovery must complete through the authoritative standard SQL path.
    conn = db.get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM search_index WHERE type = 'command' "
            "AND ref_id = '1' AND content LIKE '%betamarker%'"
        )
        conn.commit()
    finally:
        conn.close()

    results = advanced_search(db, query="betamarker", fts=True)
    assert len(results) == 1
    assert results[0]["session_id"] == 1
    assert any("betamarker second probe" in c for c in results[0]["matching_commands"])
    assert new_calls == []
    assert fts_calls == []
    assert std_calls == ["_search_standard"]

