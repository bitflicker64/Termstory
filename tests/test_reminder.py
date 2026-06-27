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
    get_reminders_file_path
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
