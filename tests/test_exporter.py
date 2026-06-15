import os
import json
import csv
import sys
from datetime import datetime, timedelta
from typer.testing import CliRunner
import pytest

from termstory.cli import app
from termstory.database import Database
from termstory.models import Project, Session, Command
from termstory.exporter import parse_since, fetch_export_data, serialize_sessions_to_dict, export_json, export_csv

@pytest.fixture
def temp_db(tmp_path):
    db_file = tmp_path / "test_exporter.db"
    db = Database(str(db_file))
    db.init_db()
    
    # Insert mock projects
    p1 = Project(id=1, name="Project Alpha", path="~/src/alpha", first_seen=1000, last_seen=2000, session_count=1, total_time=100)
    p2 = Project(id=2, name="Project Beta", path="~/src/beta", first_seen=2000, last_seen=3000, session_count=1, total_time=200)
    
    # Mock commands
    c1 = Command(id=101, timestamp=1000, command="git status", exit_code=0, session_id=1, project_id=1)
    c2 = Command(id=102, timestamp=1050, command="git commit -m 'feat'", exit_code=0, session_id=1, project_id=1)
    c3 = Command(id=103, timestamp=2000, command="python test.py", exit_code=1, session_id=2, project_id=2)
    c4 = Command(id=104, timestamp=2500, command="ls -la", exit_code=0, session_id=3, project_id=None) # No project (Other)
    
    # Mock sessions
    s1 = Session(id=1, start_time=1000, end_time=1050, duration_seconds=50, project_id=1, commands=[c1, c2])
    s2 = Session(id=2, start_time=2000, end_time=2000, duration_seconds=60, project_id=2, commands=[c3])
    s3 = Session(id=3, start_time=2500, end_time=2500, duration_seconds=60, project_id=None, commands=[c4])
    
    db.save_data([p1, p2], [s1, s2, s3], [c1, c2, c3, c4])
    
    # Save a commit
    db.save_commits(1, [{"hash": "a1b2c3d4e5f6", "timestamp": 1020, "message": "feat: init alpha", "cleaned_message": "init alpha"}])
    
    return db

def test_parse_since():
    # Test digit parse
    parsed_days = parse_since("3")
    assert parsed_days is not None
    # 3 days ago start of day
    expected = int(datetime.combine((datetime.now() - timedelta(days=3)).date(), datetime.min.time()).timestamp())
    assert parsed_days == expected

    # Test date parse
    parsed_date = parse_since("2026-06-03")
    assert parsed_date == int(datetime(2026, 6, 3, 0, 0).timestamp())
    
    # Test invalid format
    with pytest.raises(ValueError):
        parse_since("not-a-date")

def test_fetch_export_data(temp_db):
    # Test fetch all
    sessions = fetch_export_data(temp_db)
    assert len(sessions) == 3
    
    # Test fetch with project filter (case insensitive, match name)
    sessions_alpha = fetch_export_data(temp_db, project_filter="alpha")
    assert len(sessions_alpha) == 1
    assert sessions_alpha[0].id == 1
    
    # Test fetch with project filter (match path)
    sessions_beta = fetch_export_data(temp_db, project_filter="src/beta")
    assert len(sessions_beta) == 1
    assert sessions_beta[0].id == 2
    
    # Test fetch with project filter "Other" (matching None project_id)
    sessions_other = fetch_export_data(temp_db, project_filter="other")
    assert len(sessions_other) == 1
    assert sessions_other[0].id == 3

    # Test fetch with since filter (timestamp range)
    # Session 1 starts at 1000, session 2 at 2000, session 3 at 2500
    sessions_since = fetch_export_data(temp_db, since_str="1970-01-01") # Since start of 1970
    assert len(sessions_since) == 3
    
    sessions_since_recent = fetch_export_data(temp_db, since_str="2020-01-01")
    # All mock session timestamps (1000, 2000, 2500) are in the far past (1970), so they should be filtered out
    assert len(sessions_since_recent) == 0

def test_serialize_sessions_to_dict(temp_db):
    sessions = fetch_export_data(temp_db)
    data = serialize_sessions_to_dict(sessions, temp_db)
    
    assert len(data) == 3
    # Verify Session 1
    s1_dict = data[0]
    assert s1_dict["session_id"] == 1
    assert s1_dict["project_name"] == "Project Alpha"
    assert s1_dict["project_path"] == "~/src/alpha"
    assert len(s1_dict["commands"]) == 2
    assert s1_dict["commands"][0]["command"] == "git status"
    assert len(s1_dict["commits"]) == 1
    assert s1_dict["commits"][0]["hash"] == "a1b2c3d4e5f6"
    assert s1_dict["commits"][0]["cleaned_message"] == "init alpha"
    
    # Verify Session 3 (Other)
    s3_dict = data[2]
    assert s3_dict["session_id"] == 3
    assert s3_dict["project_name"] == "Other"
    assert s3_dict["project_path"] is None
    assert len(s3_dict["commands"]) == 1
    assert s3_dict["commands"][0]["command"] == "ls -la"

def test_export_json_stdout(temp_db, capsys):
    sessions = fetch_export_data(temp_db)
    export_json(sessions, temp_db, output_file=None)
    
    captured = capsys.readouterr()
    exported_data = json.loads(captured.out)
    assert len(exported_data) == 3
    assert exported_data[0]["session_id"] == 1
    assert len(exported_data[0]["commands"]) == 2

def test_export_json_file(temp_db, tmp_path):
    sessions = fetch_export_data(temp_db)
    out_file = tmp_path / "export.json"
    export_json(sessions, temp_db, output_file=str(out_file))
    
    with open(out_file, "r", encoding="utf-8") as f:
        exported_data = json.load(f)
        
    assert len(exported_data) == 3
    assert exported_data[1]["session_id"] == 2
    assert len(exported_data[1]["commands"]) == 1

def test_export_csv_stdout(temp_db, capsys):
    sessions = fetch_export_data(temp_db)
    export_csv(sessions, temp_db, output_file=None)
    
    captured = capsys.readouterr()
    reader = csv.DictReader(captured.out.splitlines())
    rows = list(reader)
    
    # There are 4 commands total across 3 sessions, so we expect 4 rows in CSV
    assert len(rows) == 4
    
    # Check Project Alpha row
    assert rows[0]["session_id"] == "1"
    assert rows[0]["project_name"] == "Project Alpha"
    assert rows[0]["command_text"] == "git status"
    assert rows[0]["session_commits"] == "a1b2c3d: init alpha"
    
    # Check Other project row
    assert rows[3]["session_id"] == "3"
    assert rows[3]["project_name"] == "Other"
    assert rows[3]["command_text"] == "ls -la"

def test_export_csv_file(temp_db, tmp_path):
    sessions = fetch_export_data(temp_db)
    out_file = tmp_path / "export.csv"
    export_csv(sessions, temp_db, output_file=str(out_file))
    
    with open(out_file, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        
    assert len(rows) == 4
    assert rows[2]["session_id"] == "2"
    assert rows[2]["project_name"] == "Project Beta"
    assert rows[2]["command_text"] == "python test.py"
    assert rows[2]["command_exit_code"] == "1"

def test_cli_export_command(tmp_path, monkeypatch):
    db_file = tmp_path / "test_cli_export.db"
    monkeypatch.setattr("termstory.cli.get_db_path", lambda: str(db_file))
    monkeypatch.setattr("termstory.config.get_db_path", lambda: str(db_file))
    monkeypatch.setattr("termstory.cli.get_history_files", lambda: [])
    monkeypatch.setattr("termstory.cli.run_ingestion", lambda db: None)
    
    db = Database(str(db_file))
    db.init_db()
    
    p = Project(id=1, name="CLI Project", path="~/projects/cli", first_seen=2000, last_seen=2000, session_count=1, total_time=100)
    cmd = Command(id=50, timestamp=2000, command="echo 'CLI test'", exit_code=0, session_id=1, project_id=1)
    s = Session(id=1, start_time=2000, end_time=2000, duration_seconds=100, project_id=1, commands=[cmd])
    db.save_data([p], [s], [cmd])
    
    runner = CliRunner()
    
    # Test JSON stdout export
    result = runner.invoke(app, ["export", "--format", "json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert len(data) == 1
    assert data[0]["project_name"] == "CLI Project"
    assert data[0]["commands"][0]["command"] == "echo 'CLI test'"
    
    # Test CSV stdout export
    result_csv = runner.invoke(app, ["export", "-f", "csv"])
    assert result_csv.exit_code == 0
    reader = csv.DictReader(result_csv.stdout.splitlines())
    rows = list(reader)
    assert len(rows) == 1
    assert rows[0]["project_name"] == "CLI Project"
    assert rows[0]["command_text"] == "echo 'CLI test'"
    
    # Test file export
    json_path = tmp_path / "cli_out.json"
    result_file = runner.invoke(app, ["export", "--format", "json", "-o", str(json_path)])
    assert result_file.exit_code == 0
    assert os.path.exists(json_path)
    with open(json_path, "r") as f:
        data_file = json.load(f)
    assert data_file[0]["project_name"] == "CLI Project"
    
    # Test filter matching nothing
    result_empty = runner.invoke(app, ["export", "--project", "non-existent"])
    assert result_empty.exit_code == 0
    assert "No sessions found matching filters" in result_empty.stderr
    
    # Test invalid format
    result_invalid = runner.invoke(app, ["export", "--format", "xml"])
    assert result_invalid.exit_code == 1
    assert "Error: Unsupported format" in result_invalid.stderr
