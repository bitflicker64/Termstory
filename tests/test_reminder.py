import os
import json
import time
from datetime import datetime
import pytest
from typer.testing import CliRunner

from termstory.cli import app
from termstory.database import Database
from termstory.models import Project, Session, Command
from termstory.reminder import (
    parse_reminder_text,
    add_reminder,
    complete_reminder,
    load_reminders,
    save_reminders,
    get_reminders_file_path,
    cluster_commands,
    _DEFAULT_CLUSTERING_THRESHOLD,
    consolidate_sleep_contexts
)

def test_parse_reminder_text():
    # Success cases
    assert parse_reminder_text("remind me about fixing the bug in 3 days") == ("fixing the bug", 3)
    assert parse_reminder_text("remind me to write unit tests in 1 day") == ("write unit tests", 1)
    assert parse_reminder_text("about deploy code in 5 days") == ("deploy code", 5)
    assert parse_reminder_text("to code features in 0 days") == ("code features", 0)
    assert parse_reminder_text("finish project in 12 days") == ("finish project", 12)
    assert parse_reminder_text("   finish project   in   12   days   ") == ("finish project", 12)
    
    # Error cases
    with pytest.raises(ValueError, match="Could not parse reminder phrase"):
        parse_reminder_text("remind me about fixing the bug")
    with pytest.raises(ValueError, match="Could not parse reminder phrase"):
        parse_reminder_text("fixing the bug in days")
    with pytest.raises(ValueError, match="Could not parse reminder phrase"):
        parse_reminder_text("fixing the bug in -5 days")

def test_add_and_complete_reminder(tmp_path, monkeypatch):
    reminders_file = tmp_path / "reminders.json"
    monkeypatch.setattr("termstory.reminder.get_reminders_file_path", lambda: str(reminders_file))
    
    # Test setting reminder without DB
    rem1 = add_reminder("remind me about code review in 2 days")
    assert rem1["id"] == 1
    assert rem1["about"] == "code review"
    assert rem1["days"] == 2
    assert rem1["status"] == "pending"
    assert rem1["project_name"] == "Other"
    assert rem1["session_id"] is None
    
    # Verify file is saved
    reminders = load_reminders()
    assert len(reminders) == 1
    assert reminders[0]["about"] == "code review"
    
    # Test setting reminder with DB
    db_file = tmp_path / "test_reminder.db"
    db = Database(str(db_file))
    db.init_db()
    
    now = int(time.time())
    p = Project(id=1, name="termstory", path="~/projects/termstory", first_seen=now, last_seen=now, session_count=1, total_time=100)
    cmd = Command(timestamp=now, command="git commit", session_id=1, project_id=1)
    s = Session(id=1, start_time=now, end_time=now + 100, duration_seconds=100, project_id=1, commands=[cmd])
    db.save_data([p], [s], [cmd])
    
    rem2 = add_reminder("test parsing in 4 days", db=db)
    assert rem2["id"] == 2
    assert rem2["about"] == "test parsing"
    assert rem2["days"] == 4
    assert rem2["project_name"] == "termstory"
    assert rem2["session_id"] == 1
    
    # Test complete reminder
    assert complete_reminder(2) is True
    assert load_reminders()[1]["status"] == "completed"
    
    # Try completing non-existent
    assert complete_reminder(999) is False

def test_add_reminder_logs_warning_on_db_error(tmp_path, monkeypatch, caplog):
    """When the DB lookup raises, the reminder is still saved with defaults
    and a warning is logged. Regression test for issue #111."""
    reminders_file = tmp_path / "reminders.json"
    monkeypatch.setattr("termstory.reminder.get_reminders_file_path", lambda: str(reminders_file))

    class BrokenCursor:
        def execute(self, *args, **kwargs):
            raise RuntimeError("simulated DB failure")

    class BrokenConn:
        def cursor(self):
            return BrokenCursor()
        def close(self):
            pass

    class BrokenDB:
        def get_connection(self):
            return BrokenConn()

    with caplog.at_level("WARNING", logger="termstory.reminder"):
        rem = add_reminder("review code in 2 days", db=BrokenDB())

    # Reminder is still created with default fallback values
    assert rem["about"] == "review code"
    assert rem["days"] == 2
    assert rem["session_id"] is None
    assert rem["project_name"] == "Other"

    # Warning was emitted with the simulated error context
    assert any("add_reminder" in r.message and "simulated DB failure" in r.message
               for r in caplog.records)

def test_cli_remind_commands(tmp_path, monkeypatch):
    reminders_file = tmp_path / "reminders.json"
    monkeypatch.setattr("termstory.reminder.get_reminders_file_path", lambda: str(reminders_file))
    
    db_file = tmp_path / "test_reminder_cli.db"
    monkeypatch.setattr("termstory.cli.get_db_path", lambda: str(db_file))
    monkeypatch.setattr("termstory.cli.get_history_files", lambda: [])
    
    runner = CliRunner()
    
    # Test empty list
    result = runner.invoke(app, ["remind"])
    assert result.exit_code == 0
    assert "No reminders found" in result.stdout
    
    # Test add reminder via phrase
    result = runner.invoke(app, ["remind", "remind me to fix issues in 5 days"])
    assert result.exit_code == 0
    assert "Reminder set successfully" in result.stdout
    assert "#1" in result.stdout
    assert "fix issues" in result.stdout
    assert "5 days" in result.stdout
    
    # Test add reminder via phrase with explicit days override
    result = runner.invoke(app, ["remind", "do task in 3 days", "--days", "1"])
    assert result.exit_code == 0
    assert "Reminder set successfully" in result.stdout
    assert "#2" in result.stdout
    assert "do task" in result.stdout
    assert "1 days" in result.stdout
    
    # Test list reminders
    result = runner.invoke(app, ["remind"])
    assert result.exit_code == 0
    assert "TermStory Reminders" in result.stdout
    assert "fix issues" in result.stdout
    assert "do task" in result.stdout
    
    # Test complete reminder
    result = runner.invoke(app, ["remind", "--complete", "1"])
    assert result.exit_code == 0
    assert "Marked reminder #1 as completed" in result.stdout
    
    # Test listing filters out completed by default
    result = runner.invoke(app, ["remind"])
    assert result.exit_code == 0
    assert "fix issues" not in result.stdout
    assert "do task" in result.stdout
    
    # Test listing showing completed
    result = runner.invoke(app, ["remind", "--show-completed"])
    assert result.exit_code == 0
    assert "fix issues" in result.stdout
    assert "Completed" in result.stdout
    assert "do task" in result.stdout
    
    # Test completing invalid
    result = runner.invoke(app, ["remind", "--complete", "999"])
    assert result.exit_code == 1
    assert "Reminder #999 not found" in result.stdout


def test_run_sleep_daemon_uses_configured_poll_interval(tmp_path, monkeypatch):
    from unittest.mock import patch, MagicMock
    import termstory.reminder

    monkeypatch.setattr("termstory.reminder.get_app_dir", lambda name: str(tmp_path))
    monkeypatch.setattr("termstory.config.load_config", lambda: {"reminder_poll_interval": 60})

    sleep_calls = []

    def fake_sleep(n):
        sleep_calls.append(n)
        raise SystemExit(0)

    mock_db = MagicMock()
    with patch("termstory.reminder.time.sleep", fake_sleep):
        with patch("termstory.database.Database.__init__", lambda self, path: None):
            with patch("termstory.reminder.consolidate_sleep_contexts", return_value=None):
                with pytest.raises(SystemExit):
                    termstory.reminder.run_sleep_daemon("dummy_path")

    assert sleep_calls == [60]


def test_run_sleep_daemon_accepts_float_poll_interval(tmp_path, monkeypatch):
    from unittest.mock import patch
    import termstory.reminder

    monkeypatch.setattr("termstory.reminder.get_app_dir", lambda name: str(tmp_path))
    monkeypatch.setattr("termstory.config.load_config", lambda: {"reminder_poll_interval": 60.0})

    sleep_calls = []

    def fake_sleep(n):
        sleep_calls.append(n)
        raise SystemExit(0)

    with patch("termstory.reminder.time.sleep", fake_sleep):
        with patch("termstory.database.Database.__init__", lambda self, path: None):
            with patch("termstory.reminder.consolidate_sleep_contexts", return_value=None):
                with pytest.raises(SystemExit):
                    termstory.reminder.run_sleep_daemon("dummy_path")

    assert sleep_calls == [60.0]


def test_run_sleep_daemon_rejects_bool_poll_interval(tmp_path, monkeypatch):
    from unittest.mock import patch
    import termstory.reminder

    monkeypatch.setattr("termstory.reminder.get_app_dir", lambda name: str(tmp_path))
    monkeypatch.setattr("termstory.config.load_config", lambda: {"reminder_poll_interval": True})

    sleep_calls = []

    def fake_sleep(n):
        sleep_calls.append(n)
        raise SystemExit(0)

    with patch("termstory.reminder.time.sleep", fake_sleep):
        with patch("termstory.database.Database.__init__", lambda self, path: None):
            with patch("termstory.reminder.consolidate_sleep_contexts", return_value=None):
                with pytest.raises(SystemExit):
                    termstory.reminder.run_sleep_daemon("dummy_path")

    assert sleep_calls == [300]


def test_run_sleep_daemon_cleanup_on_initialization_failure(tmp_path, monkeypatch):
    from unittest.mock import patch
    import termstory.reminder
    
    # Set get_app_dir("data") to tmp_path
    monkeypatch.setattr("termstory.reminder.get_app_dir", lambda name: str(tmp_path))
    pid_file = tmp_path / "sleep_daemon.pid"
    
    # Mock Database to raise an error during init
    def mock_db_init(self, db_path):
        raise ValueError("Initialization failure simulation")
        
    with patch("termstory.database.Database.__init__", mock_db_init):
        with pytest.raises(ValueError, match="Initialization failure simulation"):
            termstory.reminder.run_sleep_daemon("dummy_path")
            
    # The PID file should have been cleaned up and not exist on disk
    assert not pid_file.exists()


def test_add_reminder_explicit_days_prefix_suffix_stripping(tmp_path, monkeypatch):
    reminders_file = tmp_path / "reminders.json"
    monkeypatch.setattr("termstory.reminder.get_reminders_file_path", lambda: str(reminders_file))
    
    # Prefix and suffix stripping with explicit days
    rem = add_reminder("remind me about code review in 2 days", days=5)
    assert rem["about"] == "code review"
    assert rem["days"] == 5
    
    rem2 = add_reminder("remind me to write tests", days=1)
    assert rem2["about"] == "write tests"
    
    rem3 = add_reminder("deploy application in 3 days", days=10)
    assert rem3["about"] == "deploy application"


def test_add_reminder_days_validation(tmp_path, monkeypatch):
    reminders_file = tmp_path / "reminders.json"
    monkeypatch.setattr("termstory.reminder.get_reminders_file_path", lambda: str(reminders_file))
    
    # Test invalid types
    with pytest.raises(TypeError, match="Days must be an integer."):
        add_reminder("do something", days=2.5)
    
    with pytest.raises(TypeError, match="Days must be an integer."):
        add_reminder("do something", days="5")

    with pytest.raises(TypeError, match="Days must be an integer."):
        add_reminder("do something", days=True)

    # Test invalid boundary values
    with pytest.raises(ValueError, match="Days must be between 0 and 3650."):
        add_reminder("do something", days=-1)
    
    with pytest.raises(ValueError, match="Days must be between 0 and 3650."):
        add_reminder("do something", days=3651)

     # Test parsed phrase that yields an invalid range
    with pytest.raises(ValueError, match="Days must be between 0 and 3650."):
        add_reminder("do something in 4000 days")


def _fake_get_embeddings(monkeypatch, mapping):
    import termstory.rag as rag

    def fake(texts, model_name="all-MiniLM-L6-v2"):
        return [mapping[t] for t in texts]

    monkeypatch.setattr(rag, "get_embeddings", fake)
    monkeypatch.setattr(rag, "SENTENCE_TRANSFORMERS_AVAILABLE", True)


def test_cluster_commands_merges_above_threshold(monkeypatch):
    embeddings = {
        "git status": [1.0, 0.0],
        "git status -s": [0.99, 0.14107],  # cos sim with [1,0] ≈ 0.99
    }
    _fake_get_embeddings(monkeypatch, embeddings)

    clusters = cluster_commands(list(embeddings.keys()), threshold=0.6)

    assert len(clusters) == 1
    assert set(clusters[0]) == set(embeddings.keys())


def test_cluster_commands_splits_below_threshold(monkeypatch):
    embeddings = {
        "git status": [1.0, 0.0],
        "docker ps": [0.0, 1.0],  # cos sim = 0.0, well below 0.6
    }
    _fake_get_embeddings(monkeypatch, embeddings)

    clusters = cluster_commands(list(embeddings.keys()), threshold=0.6)

    assert len(clusters) == 2


def test_cluster_commands_respects_explicit_threshold_override(monkeypatch):
    embeddings = {
        "a": [1.0, 0.0],
        "b": [0.7, 0.7141],  # cos sim ≈ 0.7 — merges at 0.6, splits at 0.9
    }
    _fake_get_embeddings(monkeypatch, embeddings)

    # Lower threshold (0.5): merges
    merged = cluster_commands(list(embeddings.keys()), threshold=0.5)
    assert len(merged) == 1

    # Higher threshold (0.9): splits
    split = cluster_commands(list(embeddings.keys()), threshold=0.9)
    assert len(split) == 2


def test_cluster_commands_reads_threshold_from_config_when_unset(monkeypatch):
    embeddings = {
        "a": [1.0, 0.0],
        "b": [0.7, 0.7141],  # cos sim ≈ 0.7
    }
    _fake_get_embeddings(monkeypatch, embeddings)

    # Config sets a high threshold (0.9) — must split even though the old
    # hardcoded 0.6 default would have merged these.
    monkeypatch.setattr(
        "termstory.reminder.load_config",
        lambda: {"clustering_threshold": 0.9},
    )
    clusters = cluster_commands(list(embeddings.keys()))
    assert len(clusters) == 2


def test_cluster_commands_falls_back_to_default_when_config_raises(monkeypatch):
    embeddings = {
        "git status": [1.0, 0.0],
        "git status -s": [0.99, 0.14107],
    }
    _fake_get_embeddings(monkeypatch, embeddings)

    monkeypatch.setattr(
        "termstory.reminder.load_config",
        lambda: (_ for _ in ()).throw(OSError("no config")),
    )
    clusters = cluster_commands(list(embeddings.keys()))
    # _DEFAULT_CLUSTERING_THRESHOLD (0.6) merges these — fallback succeeded
    assert len(clusters) == 1
    assert _DEFAULT_CLUSTERING_THRESHOLD == 0.6


def test_consolidate_sleep_contexts_reads_config_once_not_per_chunk(tmp_path, monkeypatch):
    import termstory.rag as rag

    db_file = tmp_path / "test_consolidate.db"
    db = Database(str(db_file))
    db.init_db()

    now = int(time.time())
    p = Project(id=1, name="termstory", path="~/projects/termstory", first_seen=now, last_seen=now, session_count=1, total_time=100)

    # 3 chunks separated by >= 1800s idle gaps, 2 commands each.
    commands = []
    for chunk_idx in range(3):
        base = now - (3 - chunk_idx) * 3600
        commands.append(Command(timestamp=base, command=f"git status {chunk_idx}", session_id=1, project_id=1))
        commands.append(Command(timestamp=base + 10, command=f"git log {chunk_idx}", session_id=1, project_id=1))

    s = Session(id=1, start_time=commands[0].timestamp, end_time=commands[-1].timestamp, duration_seconds=3600, project_id=1, commands=commands)
    db.save_data([p], [s], commands)

    # Force the embeddings path (not the verb-fallback path) so cluster_commands
    # actually reaches the threshold-resolution / load_config() call.
    def fake_get_embeddings(texts, model_name="all-MiniLM-L6-v2"):
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr(rag, "get_embeddings", fake_get_embeddings)
    monkeypatch.setattr(rag, "SENTENCE_TRANSFORMERS_AVAILABLE", True)

    call_count = [0]
    real_defaults = {"clustering_threshold": 0.6}

    def counting_load_config():
        call_count[0] += 1
        return real_defaults

    monkeypatch.setattr("termstory.reminder.load_config", counting_load_config)

    consolidate_sleep_contexts(db, force=True)

    assert call_count[0] == 1, (
        f"load_config() was called {call_count[0]} times for 3 chunks — "
        "expected exactly 1 (resolved once per run, not once per chunk)"
    )
