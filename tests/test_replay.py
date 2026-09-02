import sys
import time
from unittest.mock import MagicMock, patch
import pytest
from typer.testing import CliRunner

from termstory.database import Database
from termstory.models import Project, Session, Command
from termstory.replay import format_relative_time, list_recent_sessions, run_replay
from termstory.cli import app

def test_format_relative_time():
    assert format_relative_time(0) == "+00:00"
    assert format_relative_time(59) == "+00:59"
    assert format_relative_time(60) == "+01:00"
    assert format_relative_time(3599) == "+59:59"
    assert format_relative_time(3600) == "+01:00:00"
    assert format_relative_time(3665) == "+01:01:05"
    assert format_relative_time(-5) == "-00:05"
    assert format_relative_time(-3665) == "-01:01:05"

def test_list_recent_sessions(tmp_path):
    db_file = tmp_path / "test_replay_list.db"
    db = Database(str(db_file))
    db.init_db()

    now = int(time.time())
    p = Project(id=1, name="My Awesome Project", path="~/awesome", first_seen=now, last_seen=now, session_count=1, total_time=100)
    cmd = Command(timestamp=now, command="echo 'hello'", session_id=1, project_id=1)
    s = Session(id=1, start_time=now, end_time=now + 100, duration_seconds=100, project_id=1, commands=[cmd], ai_summary="Test Summary")
    db.save_data([p], [s], [cmd])

    with patch("termstory.replay.console.print") as mock_print:
        list_recent_sessions(db)
        assert mock_print.called
        # Verify a table was printed
        table_arg = mock_print.call_args[0][0]
        assert table_arg.title == "🎬 Recent TermStory Sessions"

def test_run_replay_no_sessions(tmp_path):
    db_file = tmp_path / "test_replay_empty.db"
    db = Database(str(db_file))
    db.init_db()

    with patch("termstory.replay.console.print") as mock_print:
        run_replay(db, session_id=None)
        # Should inform that no sessions were found
        printed_texts = [call[0][0] for call in mock_print.call_args_list if call[0]]
        any_no_session_msg = any("No sessions found" in str(txt) for txt in printed_texts)
        assert any_no_session_msg

def test_run_replay_invalid_speed(tmp_path):
    db_file = tmp_path / "test_replay_speed.db"
    db = Database(str(db_file))
    db.init_db()

    with patch("termstory.replay.console.print") as mock_print, pytest.raises(SystemExit):
        run_replay(db, speed=0.0)
    
    mock_print.assert_any_call("[bold red]Error: Playback speed must be greater than 0.[/bold red]")

def test_run_replay_not_found(tmp_path):
    db_file = tmp_path / "test_replay_nf.db"
    db = Database(str(db_file))
    db.init_db()

    with patch("termstory.replay.console.print") as mock_print, pytest.raises(SystemExit):
        run_replay(db, session_id=999)
        
    mock_print.assert_any_call("[bold red]Error: Session #999 not found.[/bold red]")

def test_run_replay_successful_playback(tmp_path):
    db_file = tmp_path / "test_replay_success.db"
    db = Database(str(db_file))
    db.init_db()

    now = int(time.time())
    p = Project(id=1, name="My Awesome Project", path="~/awesome", first_seen=now, last_seen=now, session_count=1, total_time=100)
    # 2 commands with a 10s gap, first exit 0, second exit 1
    cmd1 = Command(timestamp=now, command="git status", exit_code=0, session_id=1, project_id=1)
    cmd2 = Command(timestamp=now + 10, command="make build", exit_code=1, session_id=1, project_id=1)
    s = Session(id=1, start_time=now, end_time=now + 10, duration_seconds=10, project_id=1, commands=[cmd1, cmd2])
    db.save_data([p], [s], [cmd1, cmd2])

    written_chars = []
    def mock_write(char):
        written_chars.append(char)

    with patch("time.sleep") as mock_sleep, \
         patch("sys.stdout.write", side_effect=mock_write), \
         patch("sys.stdout.flush"), \
         patch("termstory.replay.console.print") as mock_print:
        
        run_replay(db, session_id=1, speed=2.0)
        
        # Verify commands were typed out
        full_output = "".join(written_chars)
        assert "git status" in full_output
        assert "make build" in full_output
        
        # Check sleep calls for speed scaling (should divide delay by speed multiplier of 2.0)
        # We had a 10s gap, which is scaled by speed (10.0 / 2.0 = 5.0) and capped at 2.0s max delay.
        # So we should see a sleep of 2.0s
        sleep_times = [args[0][0] for args in mock_sleep.call_args_list]
        assert any(abs(t - 2.0) < 0.01 for t in sleep_times) # Capped wait time
        assert any(abs(t - (0.5 / 2.0)) < 0.01 for t in sleep_times) # Initial delay

        # Verify exit indicators were printed
        printed_texts = [call[0][0] for call in mock_print.call_args_list if call[0]]
        assert any("✔" in str(txt) for txt in printed_texts)
        assert any("✘ (exit: 1)" in str(txt) for txt in printed_texts)

def test_run_replay_keyboard_interrupt(tmp_path):
    db_file = tmp_path / "test_replay_ki.db"
    db = Database(str(db_file))
    db.init_db()

    now = int(time.time())
    p = Project(id=1, name="My Awesome Project", path="~/awesome", first_seen=now, last_seen=now, session_count=1, total_time=100)
    cmd = Command(timestamp=now, command="git status", exit_code=0, session_id=1, project_id=1)
    s = Session(id=1, start_time=now, end_time=now, duration_seconds=0, project_id=1, commands=[cmd])
    db.save_data([p], [s], [cmd])

    with patch("time.sleep", side_effect=KeyboardInterrupt), \
         patch("sys.stdout.write"), \
         patch("sys.stdout.flush"), \
         patch("termstory.replay.console.print") as mock_print:
        
        run_replay(db, session_id=1)
        
        printed_texts = [call[0][0] for call in mock_print.call_args_list if call[0]]
        assert any("Playback interrupted by user." in str(txt) for txt in printed_texts)

def test_cli_replay_command(tmp_path, monkeypatch):
    db_file = tmp_path / "test_cli_replay.db"
    monkeypatch.setattr("termstory.cli.get_db_path", lambda: str(db_file))
    monkeypatch.setattr("termstory.config.get_db_path", lambda: str(db_file))
    monkeypatch.setattr("termstory.cli.get_history_files", lambda: [])
    
    db = Database(str(db_file))
    db.init_db()
    
    now = int(time.time())
    p = Project(id=1, name="My Awesome Project", path="~/awesome", first_seen=now, last_seen=now, session_count=1, total_time=100)
    cmd = Command(timestamp=now, command="echo 'test cli'", exit_code=0, session_id=1, project_id=1)
    s = Session(id=1, start_time=now, end_time=now, duration_seconds=0, project_id=1, commands=[cmd])
    db.save_data([p], [s], [cmd])
    
    runner = CliRunner()
    
    # Test with mock run_replay to ensure it's called
    with patch("termstory.replay.run_replay") as mock_run_replay:
        result = runner.invoke(app, ["replay", "1", "--speed", "4.0"])
        assert result.exit_code == 0
        mock_run_replay.assert_called_once()
        args, kwargs = mock_run_replay.call_args
        assert kwargs["session_id"] == 1
        assert kwargs["speed"] == 4.0


def test_cli_replay_mcp_command(tmp_path, monkeypatch):
    db_file = tmp_path / "test_cli_replay_mcp.db"
    monkeypatch.setattr("termstory.cli.get_db_path", lambda: str(db_file))
    monkeypatch.setattr("termstory.config.get_db_path", lambda: str(db_file))
    monkeypatch.setattr("termstory.cli.get_history_files", lambda: [])
    
    db = Database(str(db_file))
    db.init_db()
    
    now = int(time.time())
    p = Project(id=1, name="My Awesome Project", path="~/awesome", first_seen=now, last_seen=now, session_count=1, total_time=100)
    cmd = Command(timestamp=now, command="echo 'test cli'", exit_code=0, session_id=1, project_id=1)
    s = Session(id=1, start_time=now, end_time=now, duration_seconds=0, project_id=1, commands=[cmd])
    db.save_data([p], [s], [cmd])
    
    # Save a mock snapshot
    db.save_mcp_snapshot(1, "cli", {"cwd": "/mock/cwd", "ide": {"ide_name": "VS Code", "env_vars": {}}, "git": {"is_repo": False}}, now)
    
    runner = CliRunner()
    
    result = runner.invoke(app, ["replay", "1", "--mcp"])
    assert result.exit_code == 0
    assert "MCP Workspace Snapshots" in result.output
    assert "/mock/cwd" in result.output
    assert "VS Code" in result.output



def _record_command_order(db, session_id, cmds):
    """Run replay against an injected session and return command texts in the
    order they are typed out (i.e. the replay execution order).

    ``db.get_sessions_by_ids`` is expected to be pre-monkeypatched by the
    caller so the session/commands used by run_replay are fully controlled.
    """
    written = []

    def _write(char):
        written.append(char)

    with patch("time.sleep"), \
         patch("sys.stdout.write", side_effect=_write), \
         patch("sys.stdout.flush"), \
         patch("termstory.replay.console.print"):
        run_replay(db, session_id=session_id, speed=100.0)

    typed = "".join(written)
    order = []
    for c in cmds:
        idx = typed.find(c)
        if idx == -1:
            continue
        order.append((idx, c))
    order.sort()
    return [c for _, c in order]


def test_run_replay_orders_equal_timestamps_by_id(tmp_path):
    """Issue #494: replay must execute commands with equal timestamps in
    (timestamp ASC, id ASC) order, NOT list/insertion order.

    The session.commands list is deliberately scrambled so its list order
    DIFFERS from the persisted id order. run_replay is fed this session via a
    monkeypatched get_sessions_by_ids (bypassing the DB, so the in-memory
    sort in replay.py is what we are exercising), and we assert the typed
    output follows (timestamp, id) rather than the scrambled list arrangement.
    """
    db_file = tmp_path / "test_replay_order.db"
    db = Database(str(db_file))
    db.init_db()

    now = int(time.time())

    # Explicit persisted ids: lower id first, higher id second.
    cmd_lower_id = Command(id=1, timestamp=now, command="lower-id-cmd",
                           exit_code=0, session_id=1, project_id=None)
    cmd_higher_id = Command(id=2, timestamp=now, command="higher-id-cmd",
                            exit_code=0, session_id=1, project_id=None)

    # List arrangement is REVERSED relative to id order on purpose.
    scrambled_session = Session(
        id=1, start_time=now, end_time=now, duration_seconds=0,
        project_id=None, commands=[cmd_higher_id, cmd_lower_id],
    )

    with patch.object(db, "get_sessions_by_ids", return_value=[scrambled_session]):
        order = _record_command_order(db, 1, [cmd_lower_id.command, cmd_higher_id.command])

    # Replay's (timestamp, id) sort must override the scrambled list arrangement:
    # lower id first, despite higher id appearing first in session.commands.
    assert order == [cmd_lower_id.command, cmd_higher_id.command]


def test_run_replay_preserves_chronological_order(tmp_path):
    """Issue #494: different timestamps must keep chronological ordering; the id
    tiebreaker must NOT override a later timestamp."""
    db_file = tmp_path / "test_replay_chrono.db"
    db = Database(str(db_file))
    db.init_db()

    now = int(time.time())

    # Three commands: two share a timestamp (ids 1, 2) and one is later (id 3).
    cmd_a = Command(id=1, timestamp=now, command="cmd-a",
                    exit_code=0, session_id=1, project_id=None)
    cmd_b = Command(id=2, timestamp=now, command="cmd-b",
                    exit_code=0, session_id=1, project_id=None)
    cmd_c = Command(id=3, timestamp=now + 1, command="cmd-c",
                    exit_code=0, session_id=1, project_id=None)

    # List arrangement intentionally scrambled.
    scrambled_session = Session(
        id=1, start_time=now, end_time=now + 1, duration_seconds=1,
        project_id=None, commands=[cmd_c, cmd_a, cmd_b],
    )

    with patch.object(db, "get_sessions_by_ids", return_value=[scrambled_session]):
        order = _record_command_order(db, 1, [cmd_a.command, cmd_b.command, cmd_c.command])

    # Chronological order wins; for the equal-timestamp pair (a, b) id order wins.
    assert order == [cmd_a.command, cmd_b.command, cmd_c.command]


def test_run_replay_with_none_command_ids_does_not_crash(tmp_path):
    """Issue #494: replay must not raise TypeError when an in-memory command has
    id=None.

    - a None-ID command is accepted (no crash);
    - timestamp stays primary (a later None-ID command still comes last);
    - for equal timestamps, persisted IDs (>=1) sort after a None command whose
      missing ID falls back to 0, so ordering stays deterministic.
    """
    db_file = tmp_path / "test_replay_none_ids.db"
    db = Database(str(db_file))
    db.init_db()

    now = int(time.time())

    # Persisted command with an explicit id.
    cmd_persisted = Command(id=5, timestamp=now, command="persisted-cmd",
                            exit_code=0, session_id=1, project_id=None)
    # In-memory command without a persisted id (the default is None).
    cmd_none = Command(id=None, timestamp=now, command="none-id-cmd",
                       exit_code=0, session_id=1, project_id=None)
    # Another in-memory command with a later timestamp (id stays None).
    cmd_later = Command(id=None, timestamp=now + 5, command="later-none-id-cmd",
                        exit_code=0, session_id=1, project_id=None)

    # Scramble the list order so only the (timestamp, id or 0) sort decides.
    scrambled_session = Session(
        id=1, start_time=now, end_time=now + 5, duration_seconds=5,
        project_id=None, commands=[cmd_later, cmd_persisted, cmd_none],
    )

    with patch.object(db, "get_sessions_by_ids", return_value=[scrambled_session]):
        order = _record_command_order(
            db, 1,
            [cmd_none.command, cmd_persisted.command, cmd_later.command],
        )

    # Equal timestamps: the None-ID command (fallback 0) precedes the persisted
    # id command. The later-timestamp command stays last regardless of order.
    assert order == [cmd_none.command, cmd_persisted.command, cmd_later.command]
