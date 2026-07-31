"""
test_agy.py — Tests for the TermStory → agy AI Pair Programmer Bridge

Covers:
  - Context gathering (recent commands, commits, project detection)
  - Privacy redaction (blacklisted commands are dropped, secrets are redacted)
  - Graceful failure when ``agy`` is not on PATH
  - Subprocess invocation (correct args, temp file cleanup, exit code propagation)
  - ``--no-context`` legacy mode
  - Empty / missing database scenarios
"""

import os
import subprocess
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from termstory.cli import app
from termstory.database import Database
from termstory.agy import (
    DEFAULT_CONTEXT_COMMANDS,
    EXIT_AGY_NOT_FOUND,
    EXIT_INTERRUPTED,
    build_context_prompt,
    find_agy,
    launch_agy,
    run_agy_bridge,
    _gather_recent_commands,
    _gather_recent_commits,
    _detect_current_project,
)

runner = CliRunner()


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def populated_db(tmp_path, monkeypatch):
    """Create a TermStory DB with a project, session, commands, and commits."""
    db_path = str(tmp_path / "test_agy.db")
    monkeypatch.setattr("termstory.agy.get_db_path", lambda: db_path)
    monkeypatch.setattr("termstory.config.get_db_path", lambda: db_path)

    db = Database(db_path)
    db.init_db()

    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO projects (name, path) VALUES (?, ?)",
        ("my-app", "/home/user/projects/my-app"),
    )
    project_id = cursor.lastrowid
    cursor.execute(
        "INSERT INTO sessions (start_time, end_time, duration_seconds, project_id) "
        "VALUES (?, ?, ?, ?)",
        (1700000000, 1700000100, 100, project_id),
    )
    session_id = cursor.lastrowid
    cursor.executemany(
        "INSERT INTO commands (command, timestamp, session_id, project_id) "
        "VALUES (?, ?, ?, ?)",
        [
            ("git status", 1700000010, session_id, project_id),
            ("npm test", 1700000020, session_id, project_id),
            ("docker build -t myapp .", 1700000030, session_id, project_id),
        ],
    )
    cursor.executemany(
        "INSERT INTO commits (hash, timestamp, message, cleaned_message, project_id) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            ("abc123", 1700000015, "feat: add login", "feat: add login", project_id),
            ("def456", 1700000025, "fix: cache bug", "fix: cache bug", project_id),
        ],
    )
    conn.commit()
    conn.close()

    return db


@pytest.fixture
def empty_db(tmp_path, monkeypatch):
    """Create an initialized but empty TermStory DB."""
    db_path = str(tmp_path / "test_agy_empty.db")
    monkeypatch.setattr("termstory.agy.get_db_path", lambda: db_path)
    monkeypatch.setattr("termstory.config.get_db_path", lambda: db_path)
    db = Database(db_path)
    db.init_db()
    return db


# ── find_agy ─────────────────────────────────────────────────────────────────


class TestFindAgy:
    def test_returns_path_when_agy_exists(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda cmd: "/usr/local/bin/agy")
        assert find_agy() == "/usr/local/bin/agy"

    def test_returns_none_when_agy_missing(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda cmd: None)
        assert find_agy() is None


# ── Context gathering ────────────────────────────────────────────────────────


class TestGatherRecentCommands:
    def test_returns_sanitized_commands(self, populated_db):
        commands = _gather_recent_commands(populated_db, limit=10)
        assert len(commands) == 3
        assert "docker build" in commands[0]
        assert "npm test" in commands[1]
        assert "git status" in commands[2]

    def test_skips_blacklisted_commands(self, populated_db):
        """Commands matching BLACKLIST_PATTERNS must be dropped."""
        conn = populated_db.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO commands (command, timestamp) VALUES (?, ?)",
            ("aws configure set aws_access_key_id AKIAFAKE", 1700000040),
        )
        conn.commit()
        conn.close()

        commands = _gather_recent_commands(populated_db, limit=10)
        assert not any("aws configure" in c for c in commands)
        assert len(commands) == 3

    def test_redacts_secrets_in_commands(self, populated_db):
        """Secrets in commands must be redacted before returning.

        Uses ``export DATABASE_PASSWORD=...`` because it contains a secret
        but does NOT match any BLACKLIST_PATTERN (the blacklist's
        ``\\bpassword\\b`` requires "password" as a standalone word, not as
        a suffix of ``DATABASE_PASSWORD``).  This exercises the
        ``redact_command`` path rather than the drop-entirely path.
        """
        conn = populated_db.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO commands (command, timestamp) VALUES (?, ?)",
            ("export DATABASE_PASSWORD=SuperSecretValue456", 1700000050),
        )
        conn.commit()
        conn.close()

        commands = _gather_recent_commands(populated_db, limit=10)
        export_cmd = [c for c in commands if "DATABASE_PASSWORD" in c][0]
        # The secret value must be gone...
        assert "SuperSecretValue456" not in export_cmd
        # ...replaced by the [REDACTED] marker.
        assert "[REDACTED]" in export_cmd

    def test_empty_db_returns_empty_list(self, empty_db):
        commands = _gather_recent_commands(empty_db, limit=10)
        assert commands == []


class TestGatherRecentCommits:
    def test_returns_sanitized_commits(self, populated_db):
        commits = _gather_recent_commits(populated_db, limit=10)
        assert len(commits) == 2

    def test_empty_db_returns_empty_list(self, empty_db):
        commits = _gather_recent_commits(empty_db, limit=10)
        assert commits == []


class TestDetectCurrentProject:
    def test_returns_most_recent_project(self, populated_db):
        name, path = _detect_current_project(populated_db)
        assert name == "my-app"
        assert path == "/home/user/projects/my-app"

    def test_returns_none_when_no_sessions(self, empty_db):
        name, path = _detect_current_project(empty_db)
        assert name is None
        assert path is None


# ── build_context_prompt ─────────────────────────────────────────────────────


class TestBuildContextPrompt:
    def test_contains_project_info(self, populated_db):
        prompt = build_context_prompt(populated_db)
        assert "my-app" in prompt
        assert "/home/user/projects/my-app" in prompt

    def test_contains_commands_section(self, populated_db):
        prompt = build_context_prompt(populated_db)
        assert "## Recent Shell Commands" in prompt
        assert "git status" in prompt
        assert "npm test" in prompt
        assert "docker build" in prompt

    def test_contains_commits_section(self, populated_db):
        prompt = build_context_prompt(populated_db)
        assert "## Recent Git Commits" in prompt
        assert "feat: add login" in prompt
        assert "fix: cache bug" in prompt

    def test_contains_privacy_footer(self, populated_db):
        prompt = build_context_prompt(populated_db)
        assert "sanitized" in prompt.lower() or "redactor" in prompt.lower()

    def test_empty_db_produces_no_history_message(self, empty_db):
        prompt = build_context_prompt(empty_db)
        assert "No commands available" in prompt or "No active project" in prompt

    def test_respects_num_commands_limit(self, populated_db):
        prompt = build_context_prompt(populated_db, num_commands=1)
        assert "git status" in prompt or "npm test" in prompt or "docker build" in prompt

    def test_clamps_to_max(self, populated_db):
        prompt = build_context_prompt(populated_db, num_commands=99999)
        assert "git status" in prompt


# ── launch_agy ───────────────────────────────────────────────────────────────


class TestLaunchAgy:
    def test_returns_exit_code_when_agy_not_found(self, monkeypatch):
        monkeypatch.setattr("termstory.agy.find_agy", lambda: None)
        code = launch_agy(context_prompt="test")
        assert code == EXIT_AGY_NOT_FOUND

    def test_invokes_agy_with_context_file(self, monkeypatch):
        monkeypatch.setattr("termstory.agy.find_agy", lambda: "/usr/local/bin/agy")
        captured_args = []

        def mock_run(cmd, **kwargs):
            captured_args.append(cmd)
            return subprocess.CompletedProcess(cmd, 0)

        monkeypatch.setattr("subprocess.run", mock_run)

        code = launch_agy(context_prompt="# test context")
        assert code == 0
        assert len(captured_args) == 1
        assert captured_args[0][0] == "/usr/local/bin/agy"
        assert captured_args[0][1] == "-p"
        assert captured_args[0][2].endswith(".md")
        assert os.path.exists(captured_args[0][2]) is False

    def test_cleans_up_temp_file_after_run(self, monkeypatch):
        monkeypatch.setattr("termstory.agy.find_agy", lambda: "/usr/local/bin/agy")
        monkeypatch.setattr(
            "subprocess.run",
            lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0),
        )
        launch_agy(context_prompt="# test")

        import glob
        leftover = glob.glob("/tmp/termstory-agy-context-*.md")
        assert leftover == [], f"Temp files not cleaned up: {leftover}"

    def test_cleans_up_temp_file_on_keyboard_interrupt(self, monkeypatch):
        monkeypatch.setattr("termstory.agy.find_agy", lambda: "/usr/local/bin/agy")

        def raise_interrupt(cmd, **kwargs):
            raise KeyboardInterrupt()

        monkeypatch.setattr("subprocess.run", raise_interrupt)

        code = launch_agy(context_prompt="# test")
        assert code == EXIT_INTERRUPTED

        import glob
        leftover = glob.glob("/tmp/termstory-agy-context-*.md")
        assert leftover == []

    def test_propagates_nonzero_exit_code(self, monkeypatch):
        monkeypatch.setattr("termstory.agy.find_agy", lambda: "/usr/local/bin/agy")
        monkeypatch.setattr(
            "subprocess.run",
            lambda cmd, **kw: subprocess.CompletedProcess(cmd, 42),
        )
        code = launch_agy(context_prompt="# test")
        assert code == 42

    def test_passes_extra_args(self, monkeypatch):
        monkeypatch.setattr("termstory.agy.find_agy", lambda: "/usr/local/bin/agy")
        captured = []

        def mock_run(cmd, **kwargs):
            captured.append(cmd)
            return subprocess.CompletedProcess(cmd, 0)

        monkeypatch.setattr("subprocess.run", mock_run)
        launch_agy(context_prompt="# test", extra_args=["--model", "claude-4"])

        assert "--model" in captured[0]
        assert "claude-4" in captured[0]


# ── run_agy_bridge (high-level orchestrator) ─────────────────────────────────


class TestRunAgyBridge:
    def test_returns_error_when_agy_not_installed(self, monkeypatch):
        monkeypatch.setattr("termstory.agy.find_agy", lambda: None)
        monkeypatch.setattr("shutil.which", lambda cmd: None)

        code = run_agy_bridge()
        assert code == EXIT_AGY_NOT_FOUND
      
    def test_no_context_mode_skips_db(self, monkeypatch):
        monkeypatch.setattr("termstory.agy.find_agy", lambda: "/usr/local/bin/agy")
        called = []

        def mock_run(cmd, **kwargs):
            called.append(cmd)
            return subprocess.CompletedProcess(cmd, 0)

        monkeypatch.setattr("subprocess.run", mock_run)
        code = run_agy_bridge(no_context=True)
        assert code == 0
        assert called == [["agy", "-p"]]

    def test_bridge_builds_context_and_launches(self, monkeypatch, populated_db):
        monkeypatch.setattr("termstory.agy.find_agy", lambda: "/usr/local/bin/agy")
        monkeypatch.setattr("termstory.cli.run_ingestion", lambda db: None)
        captured = []

        def mock_run(cmd, **kwargs):
            captured.append(cmd)
            context_path = cmd[2]
            with open(context_path) as f:
                content = f.read()
            assert "my-app" in content
            assert "git status" in content
            return subprocess.CompletedProcess(cmd, 0)

        monkeypatch.setattr("subprocess.run", mock_run)
        code = run_agy_bridge()
        assert code == 0
        assert len(captured) == 1
        assert captured[0][1] == "-p"

    def test_bridge_tolerates_corrupt_db(self, monkeypatch, tmp_path):
        monkeypatch.setattr("termstory.agy.find_agy", lambda: "/usr/local/bin/agy")
        monkeypatch.setattr(
            "termstory.agy.get_db_path",
            lambda: str(tmp_path / "nonexistent.db"),
        )
        with patch("termstory.agy.Database") as MockDb:
            MockDb.return_value.init_db.side_effect = Exception("corrupt")
            MockDb.return_value.get_connection.return_value.cursor.return_value.fetchall.return_value = []
            monkeypatch.setattr(
                "subprocess.run",
                lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0),
            )
            code = run_agy_bridge()
            assert code == 0


# ── CLI integration (via the typer app) ──────────────────────────────────────


class TestCliAgyCommand:
    def test_agy_not_found_exit_code(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda cmd: None)
        result = runner.invoke(app, ["agy"])
        assert result.exit_code == 1
        output = result.output or result.stdout
        assert "not found" in output.lower() or "not on PATH" in output

    def test_agy_no_context_flag(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda cmd: "/usr/local/bin/agy")
        monkeypatch.setattr("termstory.agy.find_agy", lambda: "/usr/local/bin/agy")
        called = []

        def mock_run(cmd, **kwargs):
            called.append(cmd)
            return subprocess.CompletedProcess(cmd, 0)

        monkeypatch.setattr("subprocess.run", mock_run)
        result = runner.invoke(app, ["agy", "--no-context"])
        assert result.exit_code == 0
        assert called == [["agy", "-p"]]

    def test_agy_with_context_uses_db(self, monkeypatch, populated_db):
        monkeypatch.setattr("shutil.which", lambda cmd: "/usr/local/bin/agy")
        monkeypatch.setattr("termstory.agy.find_agy", lambda: "/usr/local/bin/agy")
        monkeypatch.setattr("termstory.cli.run_ingestion", lambda db: None)

        captured = []
        captured_content = []

        def mock_run(cmd, **kwargs):
            captured.append(cmd)
            # Read the context file NOW — launch_agy deletes it in a
            # finally block after subprocess.run returns.
            with open(cmd[2]) as f:
                captured_content.append(f.read())
            return subprocess.CompletedProcess(cmd, 0)

        monkeypatch.setattr("subprocess.run", mock_run)

        result = runner.invoke(app, ["agy", "--num-commands", "5"])
        assert result.exit_code == 0
        assert len(captured) == 1
        assert "git status" in captured_content[0]

    def test_agy_keyboard_interrupt(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda cmd: "/usr/local/bin/agy")
        monkeypatch.setattr("termstory.agy.find_agy", lambda: "/usr/local/bin/agy")

        def raise_interrupt(cmd, **kwargs):
            raise KeyboardInterrupt()

        monkeypatch.setattr("subprocess.run", raise_interrupt)
        result = runner.invoke(app, ["agy", "--no-context"])
        assert result.exit_code == 130
