import os
import tempfile
import pytest
from termstory.database import Database
from termstory.models import Project, Session, Command
from termstory.tags import compute_tags_from_text, auto_tag_all_sessions

def test_compute_tags_from_text():
    # Test deploy tag
    assert "deploy" in compute_tags_from_text(["docker push myorg/myapp"], [])
    assert "deploy" in compute_tags_from_text([], [{"message": "release: production v1.0.0"}])
    
    # Test debug tag
    assert "debug" in compute_tags_from_text(["python3 -m pdb script.py"], [])
    assert "debug" in compute_tags_from_text([], [{"message": "fix bug: fix null pointer exception"}])
    
    # Test setup tag
    assert "setup" in compute_tags_from_text(["pip install flask"], [])
    assert "setup" in compute_tags_from_text([], [{"message": "add dependency flask"}])
    
    # Test test tag
    assert "test" in compute_tags_from_text(["pytest tests/"], [])
    assert "test" in compute_tags_from_text([], [{"message": "add unit tests for auth"}])
    
    # Test docs tag
    assert "docs" in compute_tags_from_text(["mkdocs build"], [])
    assert "docs" in compute_tags_from_text([], [{"message": "update README.md"}])
    
    # Test multiple tags & ordering
    tags = compute_tags_from_text(["pip install pytest", "docker push myorg/myapp"], [])
    # deploy (from docker push), setup (from pip), test (from pytest)
    # Ordering should be: deploy, debug, setup, test, docs
    assert tags == ["deploy", "setup", "test"]

def test_network_utilities_not_tagged_debug():
    """Regression test for issue #452: ordinary network utilities must not
    receive the debug tag. Assertions check 'debug' specifically so another
    tag cannot mask the result."""
    for cmd in [
        "curl https://example.com",
        "ping 8.8.8.8",
        "dig example.com",
        "nslookup example.com",
    ]:
        assert "debug" not in compute_tags_from_text([cmd], []), (
            f"{cmd!r} must not be tagged debug"
        )

def test_debug_tools_still_tagged_debug():
    """Regression test for issue #452: removing the bare network-utility
    patterns must not affect legitimate debugger/profiler commands."""
    for cmd in [
        "python3 -m pdb script.py",
        "gdb ./program",
        "lldb ./program",
        "strace ./program",
    ]:
        assert "debug" in compute_tags_from_text([cmd], []), (
            f"{cmd!r} must be tagged debug"
        )

def test_auto_tag_all_sessions():
    fd, temp_db_path = tempfile.mkstemp()
    os.close(fd)
    
    try:
        db = Database(temp_db_path)
        db.init_db()
        
        # Save a project
        p = Project(id=None, name="TestProj", path="/path/to/testproj", first_seen=1000, last_seen=2000, session_count=0, total_time=0)
        
        # Save some sessions and commands
        # Session 1: Setup and Test
        s1 = Session(id=1, start_time=1000, end_time=1100, duration_seconds=100, project_id=None)
        c1 = Command(id=None, timestamp=1010, command="npm install", session_id=1, project_id=None)
        c2 = Command(id=None, timestamp=1050, command="npm test", session_id=1, project_id=None)
        
        # Session 2: Deploy and Docs via Commits
        s2 = Session(id=2, start_time=1500, end_time=1600, duration_seconds=100, project_id=None)
        c3 = Command(id=None, timestamp=1510, command="git commit -m 'release and update docs'", session_id=2, project_id=None)
        
        # Save them using save_data (this will save project and assign IDs)
        db.save_data([p], [s1, s2], [c1, c2, c3])
        
        # Wait, since project was saved, let's verify project ID mapping worked
        assert p.id is not None
        
        # Let's save a commit for the project that matches session 2's timeframe
        # Session 2 is 1500 to 1600. Commit timestamp is 1510.
        commits = [
            {
                "hash": "abc1234",
                "timestamp": 1510,
                "message": "release production & update documentation",
                "cleaned_message": "release production & update documentation"
            }
        ]
        db.save_commits(p.id, commits)
        
        # Run auto-tagging
        auto_tag_all_sessions(db)
        
        # Retrieve sessions back from DB and verify tags
        sessions = db.get_sessions_by_ids([s1.id, s2.id])
        assert len(sessions) == 2
        
        session_map = {s.id: s for s in sessions}
        
        # s1 should have tags: setup, test
        s1_tags = session_map[s1.id].tags
        assert s1_tags is not None
        assert set(s1_tags.split(",")) == {"setup", "test"}
        
        # s2 should have tags: deploy, docs (from commit)
        s2_tags = session_map[s2.id].tags
        assert s2_tags is not None
        assert set(s2_tags.split(",")) == {"deploy", "docs"}

        # Test active session (end_time = None)
        # Verify it doesn't crash during auto_tag_all_sessions
        # We manually insert a session with NULL end_time to ensure it remains NULL in sqlite
        conn = db.get_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO sessions (id, start_time, end_time, duration_seconds, project_id) VALUES (?, ?, ?, ?, ?)", (3, 1700, None, 0, None))
        cursor.execute("INSERT INTO commands (command, timestamp, session_id) VALUES (?, ?, ?)", ("pip install requests", 1710, 3))
        conn.commit()
        conn.close()

        # Run auto-tagging incrementally (force=False)
        auto_tag_all_sessions(db, force=False)

        # Verify active session got tagged
        sessions = db.get_sessions_by_ids([3])
        assert len(sessions) == 1
        assert sessions[0].tags == "setup"

        # Verify that if we manually change s1's tag to something else,
        # incremental auto-tagging (force=False) preserves it (does not overwrite it),
        # but force=True overrides/rebuilds it.
        conn = db.get_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE sessions SET tags = 'custom_tag' WHERE id = 1")
        conn.commit()
        conn.close()

        # Run incremental auto-tagging (force=False)
        auto_tag_all_sessions(db, force=False)
        # Retrieve s1
        sessions = db.get_sessions_by_ids([1])
        assert sessions[0].tags == "custom_tag"

        # Run force auto-tagging (force=True)
        auto_tag_all_sessions(db, force=True)
        # Retrieve s1 again
        sessions = db.get_sessions_by_ids([1])
        assert set(sessions[0].tags.split(",")) == {"setup", "test"}
        
    finally:
        if os.path.exists(temp_db_path):
            os.remove(temp_db_path)
