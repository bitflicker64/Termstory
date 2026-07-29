import pytest
from typer.testing import CliRunner
from termstory.cli import app
from termstory.database import Database
from termstory.models import Project, Session, Command
from datetime import datetime


def test_archive_rejects_days_zero(tmp_path, monkeypatch):
    """archive --days 0 should fail before touching the archive logic."""
    monkeypatch.setenv("TERMSTORY_DATE_OVERRIDE", "2026-06-14 12:00:00")

    db_file = tmp_path / "main.db"
    monkeypatch.setattr("termstory.cli.get_db_path", lambda: str(db_file))
    monkeypatch.setattr("termstory.config.get_db_path", lambda: str(db_file))
    monkeypatch.setattr("termstory.cli.get_history_files", lambda: [])

    db = Database(str(db_file))
    db.init_db()

    base_time = int(datetime(2026, 6, 14, 12, 0, 0).timestamp())
    old_time = base_time - (45 * 24 * 3600)
    project = Project(id=1, name="Old Project", path="~/old", first_seen=old_time, last_seen=old_time, session_count=1, total_time=60)
    cmd = Command(id=1, timestamp=old_time, command="git commit -m 'old'", session_id=1, project_id=1)
    session = Session(id=1, start_time=old_time, end_time=old_time + 60, duration_seconds=60, project_id=1, commands=[cmd])
    db.save_data([project], [session], [cmd])

    runner = CliRunner()
    result = runner.invoke(app, ["archive", "--days", "0"])

    assert result.exit_code != 0
    output = result.output
    assert "--days must be greater than 0" in output

    conn = db.get_connection()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM sessions")
    assert c.fetchone()[0] == 1
    conn.close()


def test_archive_rejects_days_negative(tmp_path, monkeypatch):
    """archive --days -5 should fail before touching the archive logic."""
    monkeypatch.setenv("TERMSTORY_DATE_OVERRIDE", "2026-06-14 12:00:00")

    db_file = tmp_path / "main.db"
    monkeypatch.setattr("termstory.cli.get_db_path", lambda: str(db_file))
    monkeypatch.setattr("termstory.config.get_db_path", lambda: str(db_file))
    monkeypatch.setattr("termstory.cli.get_history_files", lambda: [])

    db = Database(str(db_file))
    db.init_db()

    base_time = int(datetime(2026, 6, 14, 12, 0, 0).timestamp())
    old_time = base_time - (45 * 24 * 3600)
    project = Project(id=1, name="Old Project", path="~/old", first_seen=old_time, last_seen=old_time, session_count=1, total_time=60)
    cmd = Command(id=1, timestamp=old_time, command="git commit -m 'old'", session_id=1, project_id=1)
    session = Session(id=1, start_time=old_time, end_time=old_time + 60, duration_seconds=60, project_id=1, commands=[cmd])
    db.save_data([project], [session], [cmd])

    runner = CliRunner()
    result = runner.invoke(app, ["archive", "--days", "-5"])

    assert result.exit_code != 0
    output = result.output
    assert "--days must be greater than 0" in output

    conn = db.get_connection()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM sessions")
    assert c.fetchone()[0] == 1
    conn.close()


def test_archive_accepts_positive_days(tmp_path, monkeypatch):
    """archive --days 30 should still succeed and archive eligible data."""
    monkeypatch.setenv("TERMSTORY_DATE_OVERRIDE", "2026-06-14 12:00:00")

    db_file = tmp_path / "main.db"
    archive_file = tmp_path / "archive.db"
    monkeypatch.setattr("termstory.cli.get_db_path", lambda: str(db_file))
    monkeypatch.setattr("termstory.config.get_db_path", lambda: str(db_file))
    monkeypatch.setattr("termstory.cli.get_history_files", lambda: [])

    db = Database(str(db_file))
    db.init_db()

    base_time = int(datetime(2026, 6, 14, 12, 0, 0).timestamp())
    old_time = base_time - (45 * 24 * 3600)
    project = Project(id=1, name="Old Project", path="~/old", first_seen=old_time, last_seen=old_time, session_count=1, total_time=60)
    cmd = Command(id=1, timestamp=old_time, command="git commit -m 'old'", session_id=1, project_id=1)
    session = Session(id=1, start_time=old_time, end_time=old_time + 60, duration_seconds=60, project_id=1, commands=[cmd])
    db.save_data([project], [session], [cmd])

    runner = CliRunner()
    result = runner.invoke(app, ["archive", "--days", "30", "--archive-db", str(archive_file)])

    assert result.exit_code == 0, result.stdout
    assert "Archiving data older than 30 days" in result.stdout
    assert "Archiving completed successfully" in result.stdout

    conn = db.get_connection()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM sessions")
    assert c.fetchone()[0] == 0
    conn.close()