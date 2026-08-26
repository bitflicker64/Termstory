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

