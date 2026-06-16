import os
import json
import pytest
from datetime import datetime
from typer.testing import CliRunner

from termstory.cli import app
from termstory.database import Database
from termstory.models import Project, Session, Command
from termstory.web import get_web_data, generate_and_open_report

def test_get_web_data_empty_db(tmp_path):
    db_file = tmp_path / "empty.db"
    db = Database(str(db_file))
    db.init_db()
    
    data = get_web_data(db)
    
    assert "stats" in data
    assert "projects" in data
    assert "sessions" in data
    assert "highlights" in data
    
    assert data["stats"]["total_sessions"] == 0
    assert data["stats"]["total_commands"] == 0
    assert data["stats"]["total_projects"] == 0
    assert data["stats"]["streak"] == 0
    assert len(data["projects"]) == 0
    assert len(data["sessions"]) == 0
    assert len(data["highlights"]) == 0

def test_get_web_data_populated_db(tmp_path):
    db_file = tmp_path / "populated.db"
    db = Database(str(db_file))
    db.init_db()
    
    now = int(datetime(2026, 6, 14, 12, 0, 0).timestamp())
    
    p1 = Project(id=1, name="Project A", path="/path/to/a", first_seen=now, last_seen=now, session_count=1, total_time=120)
    cmd1 = Command(timestamp=now, command="git commit -m 'initial'", exit_code=0, session_id=1, project_id=1)
    s1 = Session(id=1, start_time=now, end_time=now + 120, duration_seconds=120, project_id=1, commands=[cmd1], ai_summary="Started project A")
    
    # Save Project 2 which is general / general mapper to other
    p2 = Project(id=2, name="General / No Project", path=None, first_seen=now, last_seen=now, session_count=1, total_time=60)
    cmd2 = Command(timestamp=now + 200, command="ls", exit_code=0, session_id=2, project_id=2)
    s2 = Session(id=2, start_time=now + 200, end_time=now + 260, duration_seconds=60, project_id=2, commands=[cmd2])
    
    db.save_data([p1, p2], [s1, s2], [cmd1, cmd2])
    db.save_session_ai_summary(s1.id, "Started project A")
    
    # Save a commit
    commits = [
        {"hash": "abcdef1234567890", "timestamp": now, "message": "feat: init", "cleaned_message": "init"}
    ]
    db.save_commits(1, commits)
    
    data = get_web_data(db)
    
    # Verify stats
    assert data["stats"]["total_sessions"] == 2
    assert data["stats"]["total_commands"] == 2
    assert data["stats"]["total_projects"] == 2
    
    # Verify projects: General/No Project should map to Other
    project_names = [p["name"] for p in data["projects"]]
    assert "Project A" in project_names
    assert "Other" in project_names
    
    # Verify sessions
    assert len(data["sessions"]) == 2
    
    # The first session (ID 1, start_time now) should have the commit and AI summary
    s1_data = next(s for s in data["sessions"] if s["id"] == 1)
    assert s1_data["project_name"] == "Project A"
    assert s1_data["ai_summary"] == "Started project A"
    assert len(s1_data["commits"]) == 1
    assert s1_data["commits"][0]["hash"] == "abcdef1234567890"
    
    # The second session should have commands, with noise classifications
    s2_data = next(s for s in data["sessions"] if s["id"] == 2)
    assert s2_data["project_name"] == "Other"
    assert len(s2_data["commands"]) == 1
    assert s2_data["commands"][0]["command"] == "ls"
    assert s2_data["commands"][0]["is_noise"] is True
    
    # Verify highlights
    assert len(data["highlights"]) == 1
    assert data["highlights"][0]["project_name"] == "Project A"
    assert data["highlights"][0]["ai_summary"] == "Started project A"

def test_generate_and_open_report(tmp_path, monkeypatch):
    db_file = tmp_path / "report.db"
    db = Database(str(db_file))
    db.init_db()
    
    # Mock expanduser to use a path in tmp_path
    report_dir = tmp_path / ".termstory"
    report_file = report_dir / "report.html"
    
    monkeypatch.setattr("os.path.expanduser", lambda path: str(report_dir / "report.html") if "report.html" in path else str(report_dir))
    
    # Mock webbrowser.open
    opened_urls = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened_urls.append(url))
    
    # Generate report
    generate_and_open_report(db)
    
    # Assert file was written
    assert report_file.exists()
    
    # Assert URL was opened
    assert len(opened_urls) == 1
    assert opened_urls[0].startswith("file://")
    
    # Verify content of the generated file
    with open(report_file, "r", encoding="utf-8") as f:
        html = f.read()
        
    assert "<title>TermStory Web Report</title>" in html
    assert "const reportData = " in html

def test_cli_web_subcommand(tmp_path, monkeypatch):
    db_file = tmp_path / "cli_web.db"
    monkeypatch.setattr("termstory.cli.get_db_path", lambda: str(db_file))
    monkeypatch.setattr("termstory.config.get_db_path", lambda: str(db_file))
    monkeypatch.setattr("termstory.cli.get_history_files", lambda: [])
    
    # Mock run_ingestion to avoid attempting to parse real shells
    monkeypatch.setattr("termstory.cli.run_ingestion", lambda db: None)
    
    # Mock generate_and_open_report to take kwargs
    called_args = []
    monkeypatch.setattr(
        "termstory.web.generate_and_open_report",
        lambda db, **kwargs: called_args.append((db, kwargs))
    )
    
    runner = CliRunner()
    result = runner.invoke(app, ["web", "--template", "retro", "--date-range", "today"])
    
    assert result.exit_code == 0
    assert len(called_args) == 1
    db_arg, kwargs = called_args[0]
    assert isinstance(db_arg, Database)
    assert kwargs["template"] == "retro"
    assert kwargs["start_ts"] is not None
    assert kwargs["end_ts"] is not None

def test_parse_date_range_helper():
    from termstory.date_utils import parse_date_range_helper
    
    # Test relative ranges
    start_today, end_today = parse_date_range_helper("today")
    assert start_today < end_today
    
    start_yesterday, end_yesterday = parse_date_range_helper("yesterday")
    assert start_yesterday < end_yesterday
    
    start_7d, end_7d = parse_date_range_helper("7days")
    assert start_7d < end_7d
    
    # Test absolute range
    start_custom, end_custom = parse_date_range_helper("2026-06-01:2026-06-15")
    assert start_custom < end_custom


def test_escaping_of_json_script_tags(tmp_path, monkeypatch):
    db_file = tmp_path / "escaping.db"
    db = Database(str(db_file))
    db.init_db()
    
    now = int(datetime(2026, 6, 14, 12, 0, 0).timestamp())
    p1 = Project(id=1, name="<script>alert(1)</script>", path="&some_path", first_seen=now, last_seen=now, session_count=1, total_time=120)
    cmd1 = Command(timestamp=now, command="git commit", exit_code=0, session_id=1, project_id=1)
    s1 = Session(id=1, start_time=now, end_time=now + 120, duration_seconds=120, project_id=1, commands=[cmd1], ai_summary="<script>alert('ai')</script>")
    
    db.save_data([p1], [s1], [cmd1])
    db.save_session_ai_summary(s1.id, "<script>alert('ai')</script>")
    
    report_dir = tmp_path / ".termstory"
    report_file = report_dir / "report.html"
    monkeypatch.setattr("os.path.expanduser", lambda path: str(report_dir / "report.html") if "report.html" in path else str(report_dir))
    monkeypatch.setattr("webbrowser.open", lambda url: None)
    
    generate_and_open_report(db)
    
    with open(report_file, "r", encoding="utf-8") as f:
        html = f.read()
        
    # The literal script tags of the page structure should be present
    assert "<script>" in html
    
    # The script tags in the JSON data should be safely escaped as unicode escape sequences
    assert "</script><script>" not in html
    assert "\\u003cscript\\u003ealert(1)\\u003c/script\\u003e" in html
    assert "\\u003cscript\\u003ealert('ai')\\u003c/script\\u003e" in html
    assert "\\u0026some_path" in html

