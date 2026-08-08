# tests/test_timeline.py
"""Tests for the new `timeline` CLI command and render_timeline function."""

import os
from datetime import datetime, timedelta
from typer.testing import CliRunner

from termstory.cli import app
from termstory.database import Database
from termstory.models import Project, Session, Command


def test_cli_timeline_command(tmp_path, monkeypatch):
    # Prepare temporary database path
    db_file = tmp_path / "test_timeline.db"
    monkeypatch.setattr("termstory.cli.get_db_path", lambda: str(db_file))
    monkeypatch.setattr("termstory.config.get_db_path", lambda: str(db_file))
    # Disable history ingestion (no files)
    monkeypatch.setattr("termstory.cli.get_history_files", lambda: [])
    monkeypatch.setattr("termstory.cli.run_ingestion", lambda db: None)

    # Initialise DB and insert data for three consecutive days
    db = Database(str(db_file))
    db.init_db()
    now = datetime.now()
    base_ts = int(now.timestamp())
    # Project
    project = Project(id=1, name="DemoProject", path="~/demo", first_seen=base_ts, last_seen=base_ts, session_count=2, total_time=300)
    # Sessions on day 0 and day -2
    session_today = Session(id=1, start_time=base_ts, end_time=base_ts + 100, duration_seconds=100, project_id=1, commands=[])
    two_days_ago_ts = int((now - timedelta(days=2)).timestamp())
    session_older = Session(id=2, start_time=two_days_ago_ts, end_time=two_days_ago_ts + 200, duration_seconds=200, project_id=1, commands=[])
    # Commands for each session (required for foreign key integrity)
    cmd_today = Command(timestamp=base_ts, command="echo today", session_id=1, project_id=1)
    cmd_older = Command(timestamp=two_days_ago_ts, command="echo older", session_id=2, project_id=1)
    # Save data
    db.save_data([project], [session_today, session_older], [cmd_today, cmd_older])

    runner = CliRunner()
    result = runner.invoke(app, ["timeline", "--days", "3"]).stdout
    # Verify header and dates appear in output
    assert "Date" in result
    assert "Activity" in result
    # Expect three dates: two days ago, yesterday, today
    dates = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(2, -1, -1)]
    for d in dates:
        assert d in result
    # Ensure non‑zero bars are present for days with sessions
    assert "█" in result


def test_render_timeline_skips_legacy_sessions(tmp_path):
    db_file = tmp_path / "test_timeline_legacy.db"
    db = Database(str(db_file))
    db.init_db()
    
    now = datetime.now()
    base_ts = int(now.timestamp())
    
    project = Project(id=1, name="DemoProject", path="~/demo", first_seen=base_ts, last_seen=base_ts, session_count=1, total_time=300)
    session = Session(id=1, start_time=base_ts, end_time=base_ts + 100, duration_seconds=100, project_id=1, commands=[])
    cmd = Command(timestamp=base_ts, command="echo legacy", session_id=1, project_id=1, is_legacy=True)
    
    db.save_data([project], [session], [cmd])
    
    from termstory.timeline import render_timeline
    result = render_timeline(db, days=1)
    
    assert "█" not in result


def test_render_timeline_invalid_days(tmp_path):
    db_file = tmp_path / "test_timeline_invalid.db"
    db = Database(str(db_file))
    db.init_db()
    
    from termstory.timeline import render_timeline
    import pytest
    with pytest.raises(ValueError, match="days must be greater than 0"):
        render_timeline(db, days=0)
        
    with pytest.raises(ValueError, match="days must be greater than 0"):
        render_timeline(db, days=-5)


def test_render_timeline_scales_only_visible_days(tmp_path, monkeypatch):
    """A heavy day just outside the window must not shrink the visible bars."""
    from termstory.timeline import render_timeline

    db_file = tmp_path / "test_timeline_window.db"
    db = Database(str(db_file))
    db.init_db()

    # Freeze the clock. The pre-fix window started at ``now - days``, so the session
    # planted at midday below only fell inside it when the suite happened to run
    # before noon; pinning a morning time keeps the guard effective around the clock.
    now = datetime.now().replace(hour=8, minute=30, second=0, microsecond=0)
    monkeypatch.setattr("termstory.timeline.get_current_time", lambda: now)
    days = 3
    # One calendar day before the oldest rendered date (old code fetched this).
    outside = datetime.combine((now - timedelta(days=days)).date(), datetime.min.time()) + timedelta(hours=12)
    outside_ts = int(outside.timestamp())
    today_ts = int(now.timestamp())

    project = Project(
        id=1, name="DemoProject", path="~/demo",
        first_seen=outside_ts, last_seen=today_ts, session_count=2, total_time=1100,
    )
    session_outside = Session(
        id=1, start_time=outside_ts, end_time=outside_ts + 1000,
        duration_seconds=1000, project_id=1, commands=[],
    )
    session_today = Session(
        id=2, start_time=today_ts, end_time=today_ts + 100,
        duration_seconds=100, project_id=1, commands=[],
    )
    cmds = [
        Command(timestamp=outside_ts, command="echo outside", session_id=1, project_id=1),
        Command(timestamp=today_ts, command="echo today", session_id=2, project_id=1),
    ]
    db.save_data([project], [session_outside, session_today], cmds)

    result = render_timeline(db, days=days)
    today = now.strftime("%Y-%m-%d")
    today_line = next(line for line in result.splitlines() if line.startswith(today))
    # Today is the heaviest visible day, so its bar should be full width.
    assert today_line.count("█") == 40
    # The outside day must not appear in the rendered window.
    assert outside.strftime("%Y-%m-%d") not in result
