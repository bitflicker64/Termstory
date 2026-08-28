"""
Tests for the `termstory restore` command confirmation pattern (Issue #293).

These tests verify the safety pattern added to `restore_cmd`:
  * Non-interactive shell without `--yes` exits non-zero (refuses).
  * Non-interactive shell with `--yes` performs the restore.
  * `--dry-run` prints paths and does not touch the live DB.
  * Interactive shell with a "no" response aborts.
  * Interactive shell with a "yes" response performs the restore.
  * Missing backup file exits non-zero BEFORE any prompt.
  * Existing `--yes` automation path still works end-to-end.
"""

import os
import pytest
from typer.testing import CliRunner
from termstory.cli import app
from termstory.database import Database
from termstory.models import Project, Session, Command


def _seed_db(db_path: str, project_name: str = "Live Project") -> None:
    """Initialize a DB and seed it with one project/session/command."""
    db = Database(db_path)
    db.init_db()
    now = 1730000000
    project = Project(
        id=1, name=project_name, path="~/demo",
        first_seen=now, last_seen=now,
        session_count=1, total_time=100,
    )
    command = Command(timestamp=now, command="echo live", session_id=1, project_id=1)
    session = Session(
        id=1, start_time=now, end_time=now + 100,
        duration_seconds=100, project_id=1, commands=[command],
    )
    db.save_data([project], [session], [command])


def _make_backup(db_path: str, backup_path: str) -> None:
    """Copy db_path -> backup_path to simulate a pre-existing backup file."""
    import shutil
    shutil.copy2(db_path, backup_path)


def _patch_paths(monkeypatch, tmp_path, live_db_name: str = "live.db"):
    """Patch all DB-path lookups to point under tmp_path."""
    live_db = tmp_path / live_db_name
    monkeypatch.setattr("termstory.cli.get_db_path", lambda: str(live_db))
    monkeypatch.setattr("termstory.config.get_db_path", lambda: str(live_db))
    monkeypatch.setattr("termstory.backup.get_db_path", lambda: str(live_db))
    return live_db


def _force_non_interactive(monkeypatch):
    """
    Force `sys.stdin.isatty()` to return False inside `restore_cmd`.

    `typer.testing.CliRunner` replaces `sys.stdin` with a BytesIO-backed
    wrapper DURING `invoke()`, so monkeypatching `sys.stdin.isatty` on the
    real stdin object doesn't survive into the invoked command. We replace
    the `sys` module reference inside `termstory.cli` with a lightweight
    fake whose `stdin.isatty()` returns False. This survives the
    CliRunner's stdin replacement because the lookup goes through our fake
    `sys` object, not the real one.
    """
    import types
    fake_stdin = types.SimpleNamespace(isatty=lambda: False)
    fake_sys = types.SimpleNamespace(stdin=fake_stdin)
    monkeypatch.setattr("termstory.cli.sys", fake_sys)


def _force_interactive(monkeypatch):
    """
    Force `sys.stdin.isatty()` to return True inside `restore_cmd`, so the
    interactive prompt branch is taken. See `_force_non_interactive` for
    why we replace the whole `sys` module reference.
    """
    import types
    fake_stdin = types.SimpleNamespace(isatty=lambda: True)
    fake_sys = types.SimpleNamespace(stdin=fake_stdin)
    monkeypatch.setattr("termstory.cli.sys", fake_sys)


# ---------------------------------------------------------------------------
# Non-interactive shell behavior (the regression this issue is about)
# ---------------------------------------------------------------------------

def test_restore_non_interactive_without_yes_refuses(tmp_path, monkeypatch):
    """
    In a non-interactive shell (no TTY on stdin), `termstory restore <path>`
    must refuse to proceed and exit non-zero. This prevents scripts / CI
    from silently destroying the live database.
    """
    live_db = _patch_paths(monkeypatch, tmp_path)
    _seed_db(str(live_db), project_name="Live Project")

    backup_file = tmp_path / "backup.db"
    _make_backup(str(live_db), str(backup_file))

    # Force non-interactive: stdin has no TTY.
    _force_non_interactive(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(app, ["restore", str(backup_file)])
    assert result.exit_code == 1, (
        f"Expected exit code 1 in non-interactive shell without --yes, "
        f"got {result.exit_code}. Output:\n{result.output}"
    )
    combined = result.output.lower()
    assert "non-interactive" in combined or "refusing" in combined
    # The live DB must NOT have been touched — the source backup path
    # was never even passed to restore_db.
    assert "refusing to restore" in combined


def test_restore_non_interactive_with_yes_succeeds(tmp_path, monkeypatch):
    """
    `termstory restore --yes <path>` must succeed in a non-interactive
    shell. This is the automation-friendly path.
    """
    live_db = _patch_paths(monkeypatch, tmp_path)
    _seed_db(str(live_db), project_name="Live Project")

    # Create a backup containing DIFFERENT data so we can verify the
    # restore actually replaced the live DB.
    backup_db = tmp_path / "backup.db"
    _seed_db(str(backup_db), project_name="Backup Project")
    # The seed function initializes a fresh DB at backup_db. Now copy
    # it to the expected backup path — but actually we can just pass
    # backup_db directly to the restore command.

    _force_non_interactive(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(app, ["restore", "--yes", str(backup_db)])
    assert result.exit_code == 0, (
        f"Expected exit code 0 with --yes, got {result.exit_code}. "
        f"Output:\n{result.output}"
    )
    assert "database restored" in result.output.lower()

    # Verify the live DB now reflects the BACKUP's data, not the original.
    restored = Database(str(live_db))
    projects = restored.search_projects("")
    assert len(projects) == 1
    assert projects[0].name == "Backup Project"


def test_restore_short_flag_y_also_works(tmp_path, monkeypatch):
    """The `-y` short flag must behave identically to `--yes`."""
    live_db = _patch_paths(monkeypatch, tmp_path)
    _seed_db(str(live_db), project_name="Live Project")

    backup_db = tmp_path / "backup.db"
    _seed_db(str(backup_db), project_name="Backup Project")

    _force_non_interactive(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(app, ["restore", "-y", str(backup_db)])
    assert result.exit_code == 0, result.output
    assert "database restored" in result.output.lower()


# ---------------------------------------------------------------------------
# --dry-run
# ---------------------------------------------------------------------------

def test_restore_dry_run_does_not_modify_live_db(tmp_path, monkeypatch):
    """
    `--dry-run` prints source + target paths and exits 0 without calling
    restore_db. The live DB must be byte-for-byte unchanged.
    """
    live_db = _patch_paths(monkeypatch, tmp_path)
    _seed_db(str(live_db), project_name="Live Project")

    backup_db = tmp_path / "backup.db"
    _seed_db(str(backup_db), project_name="Backup Project")

    live_size_before = live_db.stat().st_size
    live_mtime_before = live_db.stat().st_mtime

    _force_non_interactive(monkeypatch)

    runner = CliRunner()
    # --dry-run should NOT require --yes (it is non-destructive).
    result = runner.invoke(app, ["restore", "--dry-run", str(backup_db)])
    assert result.exit_code == 0, result.output

    combined = result.output.lower()
    assert "dry run" in combined
    assert "no files were modified" in combined
    # Both paths should be echoed back to the user.
    assert str(backup_db) in result.output
    assert str(live_db) in result.output

    # The live DB must be unchanged.
    assert live_db.stat().st_size == live_size_before
    assert live_db.stat().st_mtime == live_mtime_before


# ---------------------------------------------------------------------------
# Interactive shell behavior
# ---------------------------------------------------------------------------

def test_restore_interactive_default_no_aborts(tmp_path, monkeypatch):
    """
    In an interactive shell, the user is prompted and the default is "no".
    Any response other than y/yes must abort the restore without modifying
    the live DB.
    """
    live_db = _patch_paths(monkeypatch, tmp_path)
    _seed_db(str(live_db), project_name="Live Project")

    backup_db = tmp_path / "backup.db"
    _seed_db(str(backup_db), project_name="Backup Project")

    _force_interactive(monkeypatch)

    # Simulate the user just pressing Enter (default = no).
    monkeypatch.setattr("builtins.input", lambda prompt="": "")

    runner = CliRunner()
    result = runner.invoke(app, ["restore", str(backup_db)])
    assert result.exit_code == 0, result.output  # abort is a clean exit
    assert "restore aborted" in result.output.lower()

    # The live DB must still contain the original "Live Project" data.
    restored = Database(str(live_db))
    projects = restored.search_projects("")
    assert any(p.name == "Live Project" for p in projects)


def test_restore_interactive_yes_proceeds(tmp_path, monkeypatch):
    """
    In an interactive shell, answering "y" to the prompt proceeds with
    the restore.
    """
    live_db = _patch_paths(monkeypatch, tmp_path)
    _seed_db(str(live_db), project_name="Live Project")

    backup_db = tmp_path / "backup.db"
    _seed_db(str(backup_db), project_name="Backup Project")

    _force_interactive(monkeypatch)
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")

    runner = CliRunner()
    result = runner.invoke(app, ["restore", str(backup_db)])
    assert result.exit_code == 0, result.output
    assert "database restored" in result.output.lower()

    restored = Database(str(live_db))
    projects = restored.search_projects("")
    assert len(projects) == 1
    assert projects[0].name == "Backup Project"


def test_restore_interactive_keyboard_interrupt_aborts(tmp_path, monkeypatch):
    """A Ctrl-C / EOF at the prompt must abort cleanly with exit code 1."""
    live_db = _patch_paths(monkeypatch, tmp_path)
    _seed_db(str(live_db), project_name="Live Project")

    backup_db = tmp_path / "backup.db"
    _seed_db(str(backup_db), project_name="Backup Project")

    _force_interactive(monkeypatch)

    def raise_eof(prompt=""):
        raise EOFError()

    monkeypatch.setattr("builtins.input", raise_eof)

    runner = CliRunner()
    result = runner.invoke(app, ["restore", str(backup_db)])
    assert result.exit_code == 1, result.output
    assert "cancelled" in result.output.lower()


# ---------------------------------------------------------------------------
# Preflight validation
# ---------------------------------------------------------------------------

def test_restore_missing_backup_file_exits_nonzero_before_prompt(tmp_path, monkeypatch):
    """
    A mistyped backup path must produce a clear "file not found" error and
    exit non-zero WITHOUT ever prompting the user.
    """
    live_db = _patch_paths(monkeypatch, tmp_path)
    _seed_db(str(live_db), project_name="Live Project")

    missing_path = str(tmp_path / "does_not_exist.db")

    prompt_called = []

    def fail_if_called(prompt=""):
        prompt_called.append(True)
        return "y"

    _force_interactive(monkeypatch)
    monkeypatch.setattr("builtins.input", fail_if_called)

    runner = CliRunner()
    result = runner.invoke(app, ["restore", missing_path])
    assert result.exit_code == 1, result.output
    assert "not found" in result.output.lower()
    # The prompt must never have been shown.
    assert prompt_called == []


def test_restore_preserves_existing_error_contract_on_missing_file(tmp_path, monkeypatch):
    """
    Regression: the existing `restore_db` raises FileNotFoundError if the
    file disappears between our preflight check and the actual restore
    call. The CLI must still translate that into exit code 1 (the
    pre-issue-#293 behavior).
    """
    live_db = _patch_paths(monkeypatch, tmp_path)
    _seed_db(str(live_db), project_name="Live Project")

    backup_db = tmp_path / "backup.db"
    _seed_db(str(backup_db), project_name="Backup Project")

    # Make restore_db raise FileNotFoundError to simulate a race where
    # the backup file is deleted after our preflight check.
    def fake_restore_db(path):
        raise FileNotFoundError(f"Backup file not found at {path}")

    monkeypatch.setattr("termstory.backup.restore_db", fake_restore_db)
    _force_non_interactive(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(app, ["restore", "--yes", str(backup_db)])
    assert result.exit_code == 1, result.output
    assert "error" in result.output.lower()


# ---------------------------------------------------------------------------
# Issue #479 — CLI validation error handling
# ---------------------------------------------------------------------------

def _make_corrupt_termstory_db(path):
    """Create a valid TermStory DB then corrupt a data page so
    PRAGMA integrity_check fails."""
    from termstory.database import Database
    db = Database(path)
    db.init_db()
    import sqlite3
    conn = sqlite3.connect(path)
    conn.executemany(
        "INSERT INTO commands (timestamp, command, session_id, project_id) "
        "VALUES (?, ?, ?, ?)",
        [(1730000000 + i, "cmd%d" % i, 1, 1) for i in range(5000)],
    )
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.commit()
    conn.close()
    page_size = 4096
    with open(path, "r+b") as f:
        f.seek(page_size * 2)
        f.write(b"\xFF" * page_size)


def test_restore_cli_rejects_non_sqlite_backup_cleanly(tmp_path, monkeypatch):
    """A non-SQLite backup file must produce a clean CLI error (no traceback)
    and the live database must remain untouched."""
    live_db = _patch_paths(monkeypatch, tmp_path)
    _seed_db(str(live_db), project_name="Live Project")

    # Create a non-SQLite file as a fake backup
    fake_backup = tmp_path / "not_sqlite.db"
    fake_backup.write_text("This is not a database file at all.")

    _force_non_interactive(monkeypatch)
    runner = CliRunner()
    result = runner.invoke(app, ["restore", "--yes", str(fake_backup)])

    assert result.exit_code == 1, result.output
    assert "error" in result.output.lower()
    assert "Traceback" not in result.output
    # Active DB must be unchanged
    restored = Database(str(live_db))
    projects = restored.search_projects("")
    assert len(projects) == 1
    assert projects[0].name == "Live Project"


def test_restore_cli_rejects_corrupt_backup_cleanly(tmp_path, monkeypatch):
    """A corrupt (integrity_check-failing) backup must produce a clean CLI
    error (no traceback) and the live database must remain untouched."""
    live_db = _patch_paths(monkeypatch, tmp_path)
    _seed_db(str(live_db), project_name="Live Project")

    corrupt_backup = tmp_path / "corrupt.db"
    _make_corrupt_termstory_db(str(corrupt_backup))

    _force_non_interactive(monkeypatch)
    runner = CliRunner()
    result = runner.invoke(app, ["restore", "--yes", str(corrupt_backup)])

    assert result.exit_code == 1, result.output
    assert "error" in result.output.lower()
    assert "Traceback" not in result.output
    # Active DB must be unchanged
    restored = Database(str(live_db))
    projects = restored.search_projects("")
    assert len(projects) == 1
    assert projects[0].name == "Live Project"
