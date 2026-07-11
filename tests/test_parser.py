import os
from termstory.parser import parse_zsh_history, parse_bash_history, parse_fish_history, parse_powershell_history, parse_all_histories, clean_command
from termstory.models import Command

def test_clean_command():
    assert clean_command("   git    status   ") == "git status"
    assert clean_command("echo \\\n  hello \\\n  world") == "echo hello world"
    assert clean_command("   ") is None

def test_parse_zsh_history_valid_file():
    # Use our fixture
    fixture_path = os.path.join(os.path.dirname(__file__), "fixtures", "sample_history.txt")
    commands = parse_zsh_history(fixture_path)
    
    # 7 commands are in the fixture
    assert len(commands) == 7
    assert all(isinstance(c, Command) for c in commands)
    # Check that they are sorted
    assert commands[0].timestamp < commands[-1].timestamp
    
    # Check commands content
    assert commands[0].command == "git status"
    assert commands[0].timestamp == 1748851200
    assert commands[2].command == "cd ~/Project/incubator-hugegraph"
    assert commands[4].command == 'echo "Hello World"'  # multiline joined

def test_parse_zsh_history_malformed_lines(tmp_path):
    # Create a history file with valid and malformed lines
    temp_file = tmp_path / "zsh_malformed_test"
    temp_file.write_text(
        ": 1748851200:0;git status\n"
        "random malformed line without colon\n"
        ": 1748851210:0;docker ps\n"
        ": invalid_timestamp:0;should skip\n"
    )
    
    commands = parse_zsh_history(str(temp_file))
    assert len(commands) == 2
    assert commands[0].command == "git status"
    assert commands[1].command == "docker ps"

def test_parse_bash_history_with_timestamps(tmp_path):
    temp_file = tmp_path / "bash_timestamps_test"
    temp_file.write_text(
        "#1748851200\n"
        "git status\n"
        "#1748851210\n"
        "docker ps\n"
    )
    
    commands = parse_bash_history(str(temp_file))
    assert len(commands) == 2
    assert commands[0].timestamp == 1748851200
    assert commands[0].command == "git status"
    assert commands[1].timestamp == 1748851210
    assert commands[1].command == "docker ps"

def test_parse_bash_history_without_timestamps(tmp_path):
    temp_file = tmp_path / "bash_no_timestamps_test"
    temp_file.write_text(
        "git status\n"
        "docker ps\n"
    )
    
    # Set the file's modification time to a known value
    known_mtime = 1748851220
    os.utime(str(temp_file), (known_mtime, known_mtime))
    
    commands = parse_bash_history(str(temp_file))
    assert len(commands) == 2
    # With session clustering, they are in the same chunk and exactly 10s apart.
    assert commands[1].timestamp - commands[0].timestamp == 10
    assert commands[0].timestamp < known_mtime - 31536000 + 86400*3
    assert commands[0].command == "git status"
    assert commands[1].command == "docker ps"

def test_parse_zsh_history_legacy_fallback(tmp_path):
    temp_file = tmp_path / "zsh_legacy_test"
    temp_file.write_text(
        "git status\n"
        "docker ps\n"
    )
    
    # Set the file's modification time to a known value
    known_mtime = 1748851220
    os.utime(str(temp_file), (known_mtime, known_mtime))
    
    commands = parse_zsh_history(str(temp_file))
    assert len(commands) == 2
    
    # With session clustering, they are in the same chunk (CHUNK_SIZE=20)
    # So they should be exactly 10 seconds apart
    assert commands[1].timestamp - commands[0].timestamp == 10
    # And their base timestamp should have been snapped back by ~1 year
    assert commands[0].timestamp < known_mtime - 31536000 + 86400*3
    assert commands[0].command == "git status"
    assert commands[1].command == "docker ps"

def test_parse_zsh_history_hybrid_mode(tmp_path):
    temp_file = tmp_path / "zsh_hybrid_test"
    temp_file.write_text(
        "git pull\n"
        "git status\n"
        ": 1748851200:0;git commit -m 'feat'\n"
        "malformed line to ignore\n"
        ": 1748851210:0;git push\n"
        ": invalid:0;ignored too\n"
    )
    
    commands = parse_zsh_history(str(temp_file))
    assert len(commands) == 4
    
    # With session clustering, they are in the same chunk
    assert commands[1].timestamp - commands[0].timestamp == 10
    assert commands[0].timestamp < 1748851200 - 31536000 + 86400*3
    assert commands[0].command == "git pull"
    assert commands[1].command == "git status"

    assert commands[2].command == "git commit -m 'feat'"
    assert commands[2].timestamp == 1748851200

    assert commands[3].command == "git push"
    assert commands[3].timestamp == 1748851210


def test_parse_zsh_history_legacy_spread(tmp_path):
    """Large legacy history must spread across more than one calendar day.

    With N=500 legacy commands and 1 real timestamped command, the
    step-back window must exceed 86400 seconds (one day).
    """
    # Build a file with 500 legacy commands + 1 real timestamp at the end
    lines = [f"echo command_{i}\n" for i in range(500)]
    lines.append(": 1748851200:0;git push\n")
    temp_file = tmp_path / "zsh_spread_test"
    temp_file.write_text("".join(lines))

    commands = parse_zsh_history(str(temp_file))

    # All 501 commands should be present
    assert len(commands) == 501

    legacy_cmds = [c for c in commands if c.command != "git push"]
    assert len(legacy_cmds) == 500

    earliest = min(c.timestamp for c in legacy_cmds)
    latest   = max(c.timestamp for c in legacy_cmds)
    span = latest - earliest

    # window = max(500*1728, 365*86400) = 31536000 (1-year floor)
    # span ≈ 31536000 * (499/500) ≈ 31472928 — well over 30 days
    assert span > 30 * 86400, f"Legacy commands should span more than 30 days, got {span}s"

def test_parse_zsh_history_locking(tmp_path):
    temp_file = tmp_path / "zsh_locking_test"
    temp_file.write_text(
        "git status\n"
        ": 1748851200:0;git commit\n"
    )
    
    existing_lookup = {
        "git status": [1748850000],
        "git commit": [1748851200]
    }
    
    commands = parse_zsh_history(str(temp_file), existing_lookup=existing_lookup)
    assert len(commands) == 2
    
    assert commands[0].command == "git status"
    assert commands[0].timestamp == 1748850000
    
    assert commands[1].command == "git commit"
    assert commands[1].timestamp == 1748851200

def test_parse_all_histories_project_paths_propagation(monkeypatch, tmp_path):
    monkeypatch.delenv("TERMSTORY_MISSING_TIMESTAMPS", raising=False)
    temp_file = tmp_path / "zsh_test_history"
    temp_file.write_text("git status\n")
    
    received_project_paths = []
    
    class MockTimestampDetective:
        def __init__(self, search_root, project_paths):
            received_project_paths.extend(project_paths)
            
        def resolve_all(self, items):
            return [{"command": "git status", "is_legacy_still": True, "detected_ts": 1748851220, "detected_source": "Mock"}]
            
    monkeypatch.setattr("termstory.parser.TimestampDetective", MockTimestampDetective)
    
    parse_all_histories([str(temp_file)], project_paths=["/path/to/project-a", "/path/to/project-b"])
    
    assert "/path/to/project-a" in received_project_paths
    assert "/path/to/project-b" in received_project_paths


def test_parse_all_histories_project_paths_propagation_callable(monkeypatch, tmp_path):
    monkeypatch.delenv("TERMSTORY_MISSING_TIMESTAMPS", raising=False)
    temp_file = tmp_path / "zsh_test_history"
    temp_file.write_text("git status\n")
    
    received_project_paths = []
    
    class MockTimestampDetective:
        def __init__(self, search_root, project_paths):
            received_project_paths.extend(project_paths)
            
        def resolve_all(self, items):
            return [{"command": "git status", "is_legacy_still": True, "detected_ts": 1748851220, "detected_source": "Mock"}]
            
    monkeypatch.setattr("termstory.parser.TimestampDetective", MockTimestampDetective)
    
    callable_called = False
    def get_paths():
        nonlocal callable_called
        callable_called = True
        return ["/path/to/project-c"]
        
    parse_all_histories([str(temp_file)], project_paths=get_paths)
    
    assert callable_called is True
    assert "/path/to/project-c" in received_project_paths

def test_parse_fish_history(tmp_path):
    temp_file = tmp_path / "fish_history"
    temp_file.write_text(
        "- cmd: git status\n"
        "  when: 1748851200\n"
        "- cmd: echo \"hello \\n world\"\n"
        "  when: 1748851210\n"
    )
    
    commands = parse_fish_history(str(temp_file))
    assert len(commands) == 2
    assert commands[0].timestamp == 1748851200
    assert commands[0].command == "git status"
    assert commands[1].timestamp == 1748851210
    assert commands[1].command == 'echo "hello world"'

def test_parse_powershell_history(tmp_path):
    temp_file = tmp_path / "consolehost_history.txt"
    temp_file.write_text(
        "git status\n"
        "docker ps `\n"
        "  -a\n"
    )
    
    commands = parse_powershell_history(str(temp_file))
    assert len(commands) == 2
    assert commands[0].command == "git status"
    assert commands[1].command == "docker ps ` -a"

def test_parser_multiplexer_boundary_resets(tmp_path):
    # Zsh multiline interrupted by kitty +kitten
    zsh_file = tmp_path / "zsh_multiplexer"
    zsh_file.write_text(
        ": 1748851200:0;git commit \\\n"
        "kitty +kitten themes\n"
        ": 1748851210:0;git push\n"
    )
    commands = parse_zsh_history(str(zsh_file))
    # The interrupted command should be discarded due to multiplexer boundary reset,
    # and the next command should be parsed successfully.
    assert len(commands) == 1
    assert commands[0].command == "git push"
    assert commands[0].timestamp == 1748851210

    # Bash multiline interrupted by prompt_command
    bash_file = tmp_path / "bash_multiplexer"
    bash_file.write_text(
        "#1748851200\n"
        "git commit \\\n"
        "__vte_prompt_command\n"
        "#1748851210\n"
        "git push\n"
    )
    commands = parse_bash_history(str(bash_file))
    assert len(commands) == 1
    assert commands[0].command == "git push"
    assert commands[0].timestamp == 1748851210

def test_parser_max_history_age(tmp_path, monkeypatch):
    import time
    from unittest.mock import patch
    
    now = int(time.time())
    three_years_ago = now - (3 * 365 * 24 * 60 * 60)
    
    temp_file = tmp_path / "zsh_history_age_test"
    temp_file.write_text(f": {three_years_ago}:0;git status\n")
    
    with patch("termstory.config.load_config", return_value={"max_history_age": 5}):
        cmds = parse_zsh_history(str(temp_file))
        assert len(cmds) == 1
        assert cmds[0].command == "git status"
        
    with patch("termstory.config.load_config", return_value={"max_history_age": 2}):
        cmds = parse_zsh_history(str(temp_file))
        assert len(cmds) == 0


def test_parse_bash_history_out_of_order_anchors(tmp_path):
    # Create a history file with anchors that are not in chronological order
    temp_file = tmp_path / "bash_out_of_order_test"
    temp_file.write_text(
        "missing ts cmd\n"
        "#1748851250\n"
        "second cmd\n"
        "#1748851200\n"
        "third cmd\n"
    )
    
    commands = parse_bash_history(str(temp_file))
    assert len(commands) == 3
    # The output is sorted by resolved timestamp.
    # Correct (non-shuffled) mapping should be:
    # missing ts cmd: 1748851240
    # second cmd: 1748851250
    # third cmd: 1748851200
    # So after sorting:
    assert commands[0].command == "third cmd"
    assert commands[0].timestamp == 1748851200
    assert commands[1].command == "missing ts cmd"
    assert commands[1].timestamp == 1748851240
    assert commands[2].command == "second cmd"
    assert commands[2].timestamp == 1748851250


def test_parse_zsh_history_corrupt_timestamps(tmp_path):
    # History containing legacy commands followed by a corrupted 0 timestamp
    temp_file = tmp_path / "zsh_corrupt_ts_test"
    temp_file.write_text(
        "legacy cmd 1\n"
        "legacy cmd 2\n"
        ": 0:0;git status\n"
    )
    # Even with 0 timestamp, legacy commands should be parsed and mapped to a reasonable range
    commands = parse_zsh_history(str(temp_file))
    assert len(commands) == 2  # The corrupted 'git status' at timestamp 0 is filtered out, but legacy commands remain
    assert commands[0].command == "legacy cmd 1"
    assert commands[1].command == "legacy cmd 2"

def test_assign_missing_timestamps_fallback_clamping():
    from termstory.parser import _assign_missing_timestamps_fallback
    # Pass a list containing a very old out-of-bound timestamp
    temp_commands = [
        (0, "old cmd"),
        (1748851200, "valid cmd"),
        (None, "missing cmd")
    ]
    # Under _assign_missing_timestamps_fallback, the '0' timestamp is cleaned to None,
    # leaving only 'valid cmd' as the sole anchor.
    commands = _assign_missing_timestamps_fallback(temp_commands, 1748851200, None)
    assert len(commands) == 3
    assert commands[0].command == "old cmd"
    # Its timestamp should be resolved reasonably relative to the valid anchor rather than being clamped to 5 years ago
    assert commands[0].timestamp == 1748851190


def test_parse_zsh_history_getmtime_oserror_falls_back(tmp_path):
    from unittest.mock import patch

    temp_file = tmp_path / "zsh_getmtime_fail_test"
    temp_file.write_text(": 1748851200:0;git status\n")

    # os.path.getmtime raising must not abort the parse, it should fall
    # back to the current time as the anchor instead of propagating.
    with patch("termstory.parser.os.path.getmtime", side_effect=OSError("stat failed")):
        commands = parse_zsh_history(str(temp_file))
        assert len(commands) == 1
        assert commands[0].command == "git status"


def test_parse_zsh_history_project_paths_callback_raises(tmp_path):
    from unittest.mock import patch

    temp_file = tmp_path / "zsh_project_paths_raise_test"
    temp_file.write_text("legacy cmd\n")

    def bad_callback():
        raise RuntimeError("resolver blew up")

    # A raising project_paths callback must not abort the parse, legacy
    # commands should still come through with project-path enrichment
    # simply skipped for this run.
    commands = parse_zsh_history(str(temp_file), project_paths=bad_callback)
    assert len(commands) == 1
    assert commands[0].command == "legacy cmd"


def test_parse_all_histories_db_lookup_error_does_not_raise(tmp_path):
    from unittest.mock import MagicMock
    import sqlite3

    temp_file = tmp_path / "zsh_history_db_error_test"
    temp_file.write_text(": 1748851200:0;git status\n")

    db = MagicMock()
    db.get_all_commands_lookup.side_effect = sqlite3.OperationalError("database is locked")

    # A failing lookup must not abort ingestion, it should just proceed
    # without timestamp-locking against prior runs.
    commands = parse_all_histories([str(temp_file)], db=db)
    assert len(commands) == 1
    assert commands[0].command == "git status"
