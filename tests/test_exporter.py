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
from termstory.exporter import parse_since, parse_until, fetch_export_data, serialize_sessions_to_dict, export_json, export_csv, export_markdown, _session_matches_project_filter

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

def test_parse_since_with_date_override(monkeypatch):
    monkeypatch.setenv("TERMSTORY_DATE_OVERRIDE", "2026-06-03 12:00:00")
    parsed = parse_since("3")
    assert parsed is not None
    expected = int(datetime(2026, 5, 31, 0, 0, 0).timestamp())
    assert parsed == expected

def test_parse_since_date_string_unaffected(monkeypatch):
    monkeypatch.setenv("TERMSTORY_DATE_OVERRIDE", "2026-06-03 12:00:00")
    parsed = parse_since("2026-06-01")
    assert parsed == int(datetime(2026, 6, 1, 0, 0).timestamp())

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
    try:
        empty_out = result_empty.stderr + result_empty.stdout
    except ValueError:
        empty_out = result_empty.stdout
    assert "No sessions found matching filters" in empty_out
    
    # Test invalid format
    result_invalid = runner.invoke(app, ["export", "--format", "xml"])
    assert result_invalid.exit_code == 1
    try:
        invalid_out = result_invalid.stderr + result_invalid.stdout
    except ValueError:
        invalid_out = result_invalid.stdout
    assert "Error: Unsupported format" in invalid_out


def test_export_csv_null_end_time(temp_db, capsys):
    # Retrieve a session and set its end_time to None
    sessions = fetch_export_data(temp_db)
    sessions[0].end_time = None
    
    # Should not raise TypeError
    export_csv(sessions, temp_db, output_file=None)
    
    captured = capsys.readouterr()
    reader = csv.DictReader(captured.out.splitlines())
    rows = list(reader)
    assert len(rows) == 4
    assert rows[0]["session_end_time"] == ""


AWS_SAMPLE_SECRET = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"


def _build_single_session_db(db_path, commands, commits=None):
    """Create an in-memory-on-disk DB with an optional single-session export."""
    db = Database(str(db_path))
    db.init_db()
    p = Project(id=1, name="Proj", path="~/proj", first_seen=1000, last_seen=3000,
                session_count=1, total_time=100)
    s = Session(id=1, start_time=1000, end_time=3000, duration_seconds=2000,
                project_id=1, commands=commands)
    db.save_data([p], [s], commands)
    if commits:
        db.save_commits(1, commits)
    return db


def test_export_json_redacts_command_secret(tmp_path):
    db = _build_single_session_db(
        tmp_path / "secret_json.db",
        [Command(id=1, timestamp=1000,
                 command=f"export AWS_SECRET_ACCESS_KEY={AWS_SAMPLE_SECRET}",
                 exit_code=0, session_id=1, project_id=1)],
    )
    data = serialize_sessions_to_dict(fetch_export_data(db), db)
    raw = json.dumps(data)
    assert AWS_SAMPLE_SECRET not in raw
    assert "export AWS_SECRET_ACCESS_KEY=[REDACTED]" in raw


def test_export_json_redacts_commit_messages(tmp_path):
    db = _build_single_session_db(
        tmp_path / "secret_commits_json.db",
        [Command(id=1, timestamp=1000, command="git commit", exit_code=0,
                 session_id=1, project_id=1)],
        commits=[{
            "hash": "abc123def456",
            "timestamp": 1500,
            "message": "fix: add login password: hunter2",
            "cleaned_message": "add login password: s3cr3t-value",
        }],
    )
    data = serialize_sessions_to_dict(fetch_export_data(db), db)
    raw = json.dumps(data)
    assert "hunter2" not in raw
    assert "s3cr3t-value" not in raw
    assert "[REDACTED]" in raw


def test_export_csv_redacts_command_and_commit(tmp_path):
    out_file = tmp_path / "secret.csv"
    db = _build_single_session_db(
        tmp_path / "secret_csv.db",
        [Command(id=1, timestamp=1000,
                 command=f"export AWS_SECRET_ACCESS_KEY={AWS_SAMPLE_SECRET}",
                 exit_code=0, session_id=1, project_id=1)],
        commits=[{
            "hash": "abc123def456",
            "timestamp": 1500,
            "message": "fix: add login password: hunter2",
            "cleaned_message": "add login password: s3cr3t-value",
        }],
    )
    export_csv(fetch_export_data(db), db, output_file=str(out_file))
    content = out_file.read_text(encoding="utf-8")
    assert AWS_SAMPLE_SECRET not in content
    assert "hunter2" not in content
    assert "s3cr3t-value" not in content
    assert "[REDACTED]" in content


def test_export_json_blacklisted_session_redacted(tmp_path):
    db = _build_single_session_db(
        tmp_path / "bl_json.db",
        [Command(id=1, timestamp=1000, command="vault read secret/data",
                 exit_code=0, session_id=1, project_id=1)],
    )
    data = serialize_sessions_to_dict(fetch_export_data(db), db)
    raw = json.dumps(data)
    assert "vault read secret" not in raw
    assert "Security/Authentication Operations" in raw


def test_export_csv_blacklisted_session_redacted(tmp_path):
    out_file = tmp_path / "bl.csv"
    db = _build_single_session_db(
        tmp_path / "bl_csv.db",
        [Command(id=1, timestamp=1000,
                 command="aws configure set aws_secret_access_key ABC",
                 exit_code=0, session_id=1, project_id=1)],
    )
    export_csv(fetch_export_data(db), db, output_file=str(out_file))
    content = out_file.read_text(encoding="utf-8")
    assert "aws configure" not in content
    assert "Security/Authentication Operations" in content

def test_export_markdown_stdout(temp_db, capsys):
    sessions = fetch_export_data(temp_db)
    export_markdown(sessions, temp_db, output_file=None)
    out = capsys.readouterr().out

    assert out.startswith("# Termstory Export")
    assert "Project Alpha" in out
    assert "Session #1" in out
    assert "Start" in out
    assert "Duration" in out
    assert "git status" in out
    assert "a1b2c3d" in out
    assert "init alpha" in out
    assert "```text" in out


def test_export_markdown_file(temp_db, tmp_path):
    sessions = fetch_export_data(temp_db)
    out_file = tmp_path / "export.md"
    export_markdown(sessions, temp_db, output_file=str(out_file))
    content = out_file.read_text(encoding="utf-8")

    assert content.startswith("# Termstory Export")
    assert "## Session #1" in content
    assert "Project Alpha" in content
    assert "git status" in content
    assert content.endswith("\n")


def test_export_markdown_multiple_sessions(temp_db, capsys):
    sessions = fetch_export_data(temp_db)
    export_markdown(sessions, temp_db, output_file=None)
    out = capsys.readouterr().out

    assert "## Session #1" in out
    assert "## Session #2" in out
    assert "## Session #3" in out
    assert "Project Beta" in out
    assert "Other" in out
    assert out.count("```text") == 3
    assert out.count("### Commits") == 3
    assert "a1b2c3d" in out


def test_export_markdown_empty(tmp_path, capsys):
    db = Database(str(tmp_path / "empty_md.db"))
    db.init_db()
    export_markdown([], db, output_file=None)
    out = capsys.readouterr().out

    assert "# Termstory Export" in out
    assert "No sessions to export" in out


def test_export_markdown_sessions_without_commands_and_commits(temp_db, capsys):
    sessions = fetch_export_data(temp_db)
    sessions[0].commands = []
    sessions[0].commits = []
    export_markdown(sessions, temp_db, output_file=None)
    out = capsys.readouterr().out

    assert "# Termstory Export" in out
    assert "_(no commands recorded)_" in out
    assert "_(no commits)_" in out


def test_export_markdown_null_end_time(temp_db, capsys):
    sessions = fetch_export_data(temp_db)
    sessions[0].end_time = None
    export_markdown(sessions, temp_db, output_file=None)
    out = capsys.readouterr().out

    assert "## Session #1" in out
    assert "End" in out


def test_export_markdown_redacts_command_secret(tmp_path):
    out_file = tmp_path / "secret.md"
    db = _build_single_session_db(
        tmp_path / "secret_md.db",
        [Command(id=1, timestamp=1000,
                 command="export AWS_SECRET_ACCESS_KEY=" + AWS_SAMPLE_SECRET,
                 exit_code=0, session_id=1, project_id=1)],
    )
    export_markdown(fetch_export_data(db), db, output_file=str(out_file))
    content = out_file.read_text(encoding="utf-8")
    assert AWS_SAMPLE_SECRET not in content
    assert "export AWS_SECRET_ACCESS_KEY=[REDACTED]" in content


def test_export_markdown_redacts_commit_messages(tmp_path):
    out_file = tmp_path / "secret_commits.md"
    db = _build_single_session_db(
        tmp_path / "secret_commits_md.db",
        [Command(id=1, timestamp=1000, command="git commit", exit_code=0,
                 session_id=1, project_id=1)],
        commits=[{
            "hash": "abc123def456",
            "timestamp": 1500,
            "message": "fix: add login password: hunter2",
            "cleaned_message": "add login password: s3cr3t-value",
        }],
    )
    export_markdown(fetch_export_data(db), db, output_file=str(out_file))
    content = out_file.read_text(encoding="utf-8")
    assert "hunter2" not in content
    assert "s3cr3t-value" not in content
    assert "[REDACTED]" in content
    assert "abc123d" in content


def test_export_markdown_blacklisted_session(tmp_path):
    out_file = tmp_path / "bl.md"
    db = _build_single_session_db(
        tmp_path / "bl_md.db",
        [Command(id=1, timestamp=1000, command="vault read secret/data",
                 exit_code=0, session_id=1, project_id=1)],
    )
    export_markdown(fetch_export_data(db), db, output_file=str(out_file))
    content = out_file.read_text(encoding="utf-8")
    assert "vault read secret" not in content
    assert "Security/Authentication Operations" in content


def test_export_markdown_escaping(tmp_path):
    out_file = tmp_path / "escape.md"
    # Build special Markdown characters via chr() so the test source itself
    # stays free of escaping pitfalls while the strings hold real bytes.
    BT = chr(96)       # backtick
    PIPE = chr(124)    # |
    HASH = chr(35)     # #
    STAR = chr(42)     # *
    UNDER = chr(95)    # _
    LB = chr(91)       # [
    RB = chr(93)       # ]
    BACK = chr(92)     # backslash
    special_cmd = (
        "echo " + PIPE + " " + BT + "c" + BT + " " + HASH + "d "
        + STAR + "e" + STAR + " " + UNDER + "f" + UNDER + " "
        + LB + "g" + RB + " " + BACK + "h"
    )
    multiline_cmd = "first " + PIPE + " line\nsecond " + BT + "q" + BT + " tail"
    db = _build_single_session_db(
        tmp_path / "escape_md.db",
        [Command(id=1, timestamp=1000, command=special_cmd,
                 exit_code=0, session_id=1, project_id=1),
         Command(id=2, timestamp=1100, command=multiline_cmd,
                 exit_code=0, session_id=1, project_id=1)],
        commits=[{
            "hash": "def012345678",
            "timestamp": 1200,
            "message": "msg with " + PIPE + " pipe",
            "cleaned_message": "msg with " + PIPE + " pipe",
        }],
    )
    export_markdown(fetch_export_data(db), db, output_file=str(out_file))
    content = out_file.read_text(encoding="utf-8")

    # Special Markdown chars inside commands are preserved verbatim within the
    # fenced code block (code-block contents are never parsed as Markdown).
    assert special_cmd in content
    assert ("first " + PIPE + " line") in content
    assert ("second " + BT + "q" + BT + " tail") in content

    # The command block is a balanced fenced code block.
    fence_lines = [ln.strip() for ln in content.splitlines() if ln.strip().startswith(BT + BT + BT)]
    opens = [f for f in fence_lines if f.lstrip(BT) != ""]
    closers = [f for f in fence_lines if f.lstrip(BT) == ""]
    assert len(opens) == 1
    assert len(closers) == 1
    assert opens[0].startswith(BT + BT + BT + "text")
    assert len(opens[0]) - len(opens[0].lstrip(BT)) == len(closers[0])

    # A pipe in a commit message is escaped to | so the table cell is intact.
    assert ("msg with " + BACK + PIPE + " pipe") in content
    assert ("msg with " + PIPE + " pipe") not in content

    # Markdown table separator is well-formed.
    assert "| --- | --- |" in content


def test_export_markdown_project_name_newline_escaped(tmp_path):
    out_file = tmp_path / "proj_newline.md"
    db = Database(str(tmp_path / "proj_newline_md.db"))
    db.init_db()
    # A project name containing a newline and Markdown heading syntax must not
    # escape the session heading or introduce a new block.
    p = Project(id=1, name="Normal Project\n# Injected Heading", path="~/proj",
                first_seen=1000, last_seen=3000, session_count=1, total_time=100)
    cmd = Command(id=1, timestamp=1000, command="git status", exit_code=0,
                  session_id=1, project_id=1)
    s = Session(id=1, start_time=1000, end_time=3000, duration_seconds=2000,
                project_id=1, commands=[cmd])
    db.save_data([p], [s], [cmd])
    export_markdown(fetch_export_data(db), db, output_file=str(out_file))
    content = out_file.read_text(encoding="utf-8")

    # The newline is collapsed onto a single heading line and the injected '#'
    # is escaped, so it stays part of the "## Session #1" heading.
    assert "Normal Project \\# Injected Heading" in content
    assert "## Session #1 — Normal Project \\# Injected Heading" in content
    # The injected text cannot create a standalone heading/block.
    assert all(not ln.startswith("# Injected Heading") for ln in content.splitlines())


def test_export_markdown_ai_summary_heading_like_escaped(tmp_path):
    out_file = tmp_path / "summary_heading.md"
    db = _build_single_session_db(
        tmp_path / "summary_heading_md.db",
        [Command(id=1, timestamp=1000, command="git status", exit_code=0,
                 session_id=1, project_id=1)],
    )
    # A heading-like AI summary must not introduce headings into the document.
    db.save_session_ai_summary(
        1,
        "Normal summary\n# Injected Heading\n## Another Heading",
    )
    export_markdown(fetch_export_data(db), db, output_file=str(out_file))
    content = out_file.read_text(encoding="utf-8")

    assert "### AI Summary" in content
    # The summary stays escaped prose on a single line: readable text is
    # preserved but the '#' markers are neutralized.
    assert "Normal summary \\# Injected Heading \\#\\# Another Heading" in content
    # No line starts with an injected heading.
    assert all(not ln.startswith("# Injected Heading") for ln in content.splitlines())
    assert all(not ln.startswith("## Another Heading") for ln in content.splitlines())


def test_cli_export_markdown(tmp_path, monkeypatch):
    db_file = tmp_path / "test_cli_md_export.db"
    monkeypatch.setattr("termstory.cli.get_db_path", lambda: str(db_file))
    monkeypatch.setattr("termstory.config.get_db_path", lambda: str(db_file))
    monkeypatch.setattr("termstory.cli.get_history_files", lambda: [])
    monkeypatch.setattr("termstory.cli.run_ingestion", lambda db: None)

    db = Database(str(db_file))
    db.init_db()
    p = Project(id=1, name="CLI Project", path="~/projects/cli",
                first_seen=2000, last_seen=2000, session_count=1, total_time=100)
    cmd = Command(id=50, timestamp=2000, command="echo 'CLI test'", exit_code=0,
                  session_id=1, project_id=1)
    s = Session(id=1, start_time=2000, end_time=2000, duration_seconds=100,
                project_id=1, commands=[cmd])
    db.save_data([p], [s], [cmd])

    runner = CliRunner()

    result = runner.invoke(app, ["export", "--format", "markdown"])
    assert result.exit_code == 0
    assert "# Termstory Export" in result.stdout
    assert "CLI Project" in result.stdout
    assert "echo 'CLI test'" in result.stdout

    result_md = runner.invoke(app, ["export", "-f", "md"])
    assert result_md.exit_code == 0
    assert "# Termstory Export" in result_md.stdout

    md_path = tmp_path / "cli_out.md"
    result_file = runner.invoke(app, ["export", "--format", "markdown", "-o", str(md_path)])
    assert result_file.exit_code == 0
    assert os.path.exists(md_path)
    assert "# Termstory Export" in md_path.read_text(encoding="utf-8")

    result_invalid = runner.invoke(app, ["export", "--format", "xml"])
    assert result_invalid.exit_code == 1


# ---------------------------------------------------------------------------
# Issue #434: --since / --until date filters for `termstory export`
# ---------------------------------------------------------------------------

def _build_session_db(db_path, sessions_and_projects):
    """Create a DB with the given (start_ts, project_name_or_None) sessions.

    Returns a fresh :class:`Database` populated with one project per unique name
    and one (command-less) session per entry.
    """
    db = Database(str(db_path))
    db.init_db()
    projects = {}
    session_rows = []
    commands = []
    for start_ts, proj_name in sessions_and_projects:
        pid = None
        if proj_name is not None:
            if proj_name not in projects:
                proj_id = len(projects) + 1
                projects[proj_name] = Project(
                    id=proj_id, name=proj_name, path=f"~/src/{proj_name.lower()}",
                    first_seen=start_ts, last_seen=start_ts, session_count=1, total_time=0,
                )
                pid = proj_id
            else:
                pid = projects[proj_name].id
        session_rows.append(
            Session(id=len(session_rows) + 1, start_time=int(start_ts), end_time=int(start_ts),
                    duration_seconds=0, project_id=pid, commands=[])
        )
    db.save_data(list(projects.values()), session_rows, commands)
    return db


def test_parse_since_relative_expressions(monkeypatch):
    monkeypatch.setenv("TERMSTORY_DATE_OVERRIDE", "2026-06-10 12:00:00")
    # 7d -> start of the day 7 days before Jun 10 (Jun 3)
    assert parse_since("7d") == int(datetime(2026, 6, 3, 0, 0).timestamp())
    # 1w -> start of the day 1 week before Jun 10 (Jun 3)
    assert parse_since("1w") == int(datetime(2026, 6, 3, 0, 0).timestamp())
    # yesterday -> start of Jun 9
    assert parse_since("yesterday") == int(datetime(2026, 6, 9, 0, 0).timestamp())


def test_parse_until_relative_expressions(monkeypatch):
    monkeypatch.setenv("TERMSTORY_DATE_OVERRIDE", "2026-06-10 12:00:00")
    # 7d -> end of the day 7 days before Jun 10 (Jun 3)
    assert parse_until("7d") == int(datetime(2026, 6, 3, 23, 59, 59).timestamp())
    # 1w -> end of the day 1 week before Jun 10 (Jun 3)
    assert parse_until("1w") == int(datetime(2026, 6, 3, 23, 59, 59).timestamp())
    # yesterday -> end of Jun 9
    assert parse_until("yesterday") == int(datetime(2026, 6, 9, 23, 59, 59).timestamp())


def test_parse_until_iso_includes_whole_day():
    # A date-only --until includes the entire specified day.
    assert parse_until("2026-06-10") == int(datetime(2026, 6, 10, 23, 59, 59).timestamp())


def test_parse_until_invalid():
    with pytest.raises(ValueError):
        parse_until("not-a-real-date")
    with pytest.raises(ValueError):
        parse_until("zzz")



def test_fetch_export_data_until_boundary(tmp_path):
    db = _build_session_db(tmp_path / "until.db", [
        (datetime(2026, 5, 31, 12, 0).timestamp(), None),   # before since  -> excluded
        (datetime(2026, 6, 1, 0, 0).timestamp(), None),    # exact since    -> included
        (datetime(2026, 6, 5, 12, 0).timestamp(), None),    # inside range   -> included
        (datetime(2026, 6, 10, 23, 59).timestamp(), None),  # exact until    -> included
        (datetime(2026, 6, 11, 0, 0).timestamp(), None),   # after until    -> excluded
    ])
    sessions = fetch_export_data(db, since_str="2026-06-01", until_str="2026-06-10")
    start_ids = sorted(s.start_time for s in sessions)
    assert start_ids == [
        int(datetime(2026, 6, 1, 0, 0).timestamp()),
        int(datetime(2026, 6, 5, 12, 0).timestamp()),
        int(datetime(2026, 6, 10, 23, 59).timestamp()),
    ]


def test_fetch_export_data_since_only(tmp_path):
    db = _build_session_db(tmp_path / "since_only.db", [
        (datetime(2026, 5, 31, 12, 0).timestamp(), None),
        (datetime(2026, 6, 3, 9, 0).timestamp(), None),
        (datetime(2026, 6, 10, 23, 59).timestamp(), None),
    ])
    # Without --until the upper bound remains open (far future).
    sessions = fetch_export_data(db, since_str="2026-06-01")
    assert len(sessions) == 2


def test_fetch_export_data_until_only(tmp_path):
    db = _build_session_db(tmp_path / "until_only.db", [
        (datetime(2026, 5, 30, 12, 0).timestamp(), None),
        (datetime(2026, 6, 10, 23, 59).timestamp(), None),
        (datetime(2026, 6, 12, 0, 0).timestamp(), None),
    ])
    # Without --since the lower bound remains open (0).
    sessions = fetch_export_data(db, until_str="2026-06-10")
    assert len(sessions) == 2


def test_fetch_export_data_until_full_day(tmp_path):
    # --until 2026-06-10 must include the entire day at multiple times.
    db = _build_session_db(tmp_path / "full_day.db", [
        (datetime(2026, 6, 10, 0, 0).timestamp(), None),
        (datetime(2026, 6, 10, 12, 0).timestamp(), None),
        (datetime(2026, 6, 10, 23, 59).timestamp(), None),
        (datetime(2026, 6, 11, 0, 0).timestamp(), None),  # next day -> excluded
    ])
    sessions = fetch_export_data(db, until_str="2026-06-10")
    assert len(sessions) == 3
    assert all(s.start_time <= int(datetime(2026, 6, 10, 23, 59, 59).timestamp())
               for s in sessions)


def test_fetch_export_data_relative_range(tmp_path, monkeypatch):
    monkeypatch.setenv("TERMSTORY_DATE_OVERRIDE", "2026-06-10 12:00:00")
    # 1w (Jun 3 00:00) .. yesterday (Jun 9 23:59:59)
    db = _build_session_db(tmp_path / "rel_range.db", [
        (datetime(2026, 6, 2, 23, 59).timestamp(), None),  # before -> excluded
        (datetime(2026, 6, 3, 0, 0).timestamp(), None),    # exact since -> included
        (datetime(2026, 6, 9, 23, 59).timestamp(), None),  # exact until -> included
        (datetime(2026, 6, 10, 0, 0).timestamp(), None),   # after -> excluded
    ])
    sessions = fetch_export_data(db, since_str="1w", until_str="yesterday")
    assert len(sessions) == 2


def test_fetch_export_data_project_and_date(tmp_path):
    db = _build_session_db(tmp_path / "proj_date.db", [
        (datetime(2026, 6, 2, 12, 0).timestamp(), "Alpha"),
        (datetime(2026, 6, 8, 12, 0).timestamp(), "Alpha"),
        (datetime(2026, 6, 8, 14, 0).timestamp(), "Beta"),  # different time, same day
        (datetime(2026, 6, 20, 12, 0).timestamp(), "Alpha"),
    ])
    sessions = fetch_export_data(
        db, project_filter="alpha", since_str="2026-06-01", until_str="2026-06-10"
    )
    # Only Alpha sessions within Jun 1..Jun 10: the two on Jun 2 and Jun 8.
    assert len(sessions) == 2


def test_fetch_export_data_backward_compatible_since_only(tmp_path):
    # Existing callers calling fetch_export_data(since_str=...) with no until_str
    # must continue working unchanged.
    db = _build_session_db(tmp_path / "bc.db", [
        (datetime(2026, 5, 30, 12, 0).timestamp(), None),
        (datetime(2026, 6, 5, 12, 0).timestamp(), None),
    ])
    sessions = fetch_export_data(db, since_str="2026-06-01")
    assert len(sessions) == 1
    assert sessions[0].start_time == int(datetime(2026, 6, 5, 12, 0).timestamp())



def test_parse_since_relative_uses_get_current_time(monkeypatch):
    # Relative expressions must honour TERMSTORY_DATE_OVERRIDE via get_current_time().
    monkeypatch.setenv("TERMSTORY_DATE_OVERRIDE", "2026-06-10 12:00:00")
    assert parse_since("7d") == int(datetime(2026, 6, 3, 0, 0).timestamp())


def test_cli_export_with_until(tmp_path, monkeypatch):
    db_file = tmp_path / "cli_until.db"
    monkeypatch.setattr("termstory.cli.get_db_path", lambda: str(db_file))
    monkeypatch.setattr("termstory.config.get_db_path", lambda: str(db_file))
    monkeypatch.setattr("termstory.cli.get_history_files", lambda: [])
    monkeypatch.setattr("termstory.cli.run_ingestion", lambda db: None)

    db = Database(str(db_file))
    db.init_db()
    p = Project(id=1, name="CLI Project", path="~/projects/cli",
                first_seen=2000, last_seen=2000, session_count=1, total_time=100)
    cmd = Command(id=60, timestamp=2000, command="echo hi", exit_code=0,
                  session_id=1, project_id=1)
    s = Session(id=1, start_time=2000, end_time=2000, duration_seconds=100,
                project_id=1, commands=[cmd])
    db.save_data([p], [s], [cmd])

    runner = CliRunner()

    # Both values reach the export layer: an early --until excludes the session
    # (timestamp ~1970-01-01).
    result = runner.invoke(app, ["export", "--format", "json",
                                "--since", "2026-01-01", "--until", "2026-01-31"])
    # result.output merges stdout/stderr, so it works whether or not the installed
    # Click separately captures stderr (e.g. older Click on Python 3.9 CI).
    assert "No sessions found matching filters" in result.output

    # A wide range that brackets the session includes it.
    result_all = runner.invoke(app, ["export", "--format", "json",
                                     "--since", "1969-01-01", "--until", "1971-01-01"])
    assert result_all.exit_code == 0
    data = json.loads(result_all.stdout)
    assert len(data) == 1


def test_cli_export_reversed_range_error(tmp_path, monkeypatch):
    db_file = tmp_path / "cli_reversed.db"
    monkeypatch.setattr("termstory.cli.get_db_path", lambda: str(db_file))
    monkeypatch.setattr("termstory.config.get_db_path", lambda: str(db_file))
    monkeypatch.setattr("termstory.cli.get_history_files", lambda: [])
    monkeypatch.setattr("termstory.cli.run_ingestion", lambda db: None)

    db = Database(str(db_file))
    db.init_db()
    runner = CliRunner()
    result = runner.invoke(app, ["export", "--format", "json",
                                 "--since", "2026-06-30", "--until", "2026-06-01"])
    assert result.exit_code == 1
    # result.output merges stdout/stderr, so it works across Click versions.
    assert "Invalid date range" in result.output


def test_cli_export_invalid_until_error(tmp_path, monkeypatch):
    db_file = tmp_path / "cli_invalid_until.db"
    monkeypatch.setattr("termstory.cli.get_db_path", lambda: str(db_file))
    monkeypatch.setattr("termstory.config.get_db_path", lambda: str(db_file))
    monkeypatch.setattr("termstory.cli.get_history_files", lambda: [])
    monkeypatch.setattr("termstory.cli.run_ingestion", lambda db: None)

    db = Database(str(db_file))
    db.init_db()
    runner = CliRunner()
    result = runner.invoke(app, ["export", "--format", "json", "--until", "invalid"])
    assert result.exit_code == 1
    # result.output merges stdout/stderr, so it works across Click versions.
    assert "Error" in result.output




def test_fetch_export_data_reversed_range(tmp_path):
    db = _build_session_db(tmp_path / "reversed.db", [
        (datetime(2026, 6, 5, 12, 0).timestamp(), None),
    ])
    with pytest.raises(ValueError):
        fetch_export_data(db, since_str="2026-06-30", until_str="2026-06-01")


def test_parse_until_preserves_explicit_time():
    # Regression: a timestamped --until (e.g. "2026-06-10T12:00:00") must
    # preserve the explicit time instead of snapping to 23:59:59. Only
    # date-only inputs get end-of-day normalization.
    assert parse_until("2026-06-10") == int(datetime(2026, 6, 10, 23, 59, 59).timestamp())
    assert parse_until("2026-06-10T12:00:00") == int(datetime(2026, 6, 10, 12, 0, 0).timestamp())
    assert parse_until("2026-06-10 14:30:00") == int(datetime(2026, 6, 10, 14, 30, 0).timestamp())
    # Explicit midnight must NOT be expanded to end-of-day — the user asked for 00:00:00.
    assert parse_until("2026-06-10T00:00:00") == int(datetime(2026, 6, 10, 0, 0, 0).timestamp())



def _make_attribution_db(path, projects, sessions, commands):
    """Create an initialised DB seeded with the given projects/sessions/commands.

    Projects are keyed by ``path`` in Database.save_data, so explicit ``id``
    values are remapped and every session/command ``project_id`` is kept
    consistent with the database-assigned IDs.
    """
    db = Database(str(path))
    db.init_db()
    db.save_data(projects, sessions, commands)
    return db


def _alpha_beta_projects():
    """Two projects: Alpha (name+path) and Beta (name+path)."""
    return [
        Project(id=1, name="Alpha", path="~/src/alpha",
                first_seen=1000, last_seen=5000, session_count=0, total_time=0),
        Project(id=2, name="Beta", path="~/src/beta",
                first_seen=1000, last_seen=5000, session_count=0, total_time=0),
    ]


def test_project_filter_final_project_matches(tmp_path):
    """#498 scenario 1: the session-level (final) project matches - the session
    is included (existing behaviour preserved)."""
    db = _make_attribution_db(
        tmp_path / "r1.db", _alpha_beta_projects(),
        [Session(id=1, start_time=1000, end_time=1050, duration_seconds=50,
                 project_id=1, commands=[Command(id=101, timestamp=1010,
                 command="git status", exit_code=0, session_id=1, project_id=1)])],
        [Command(id=101, timestamp=1010, command="git status", exit_code=0,
                 session_id=1, project_id=1)],
    )
    assert len(fetch_export_data(db, project_filter="alpha")) == 1
    # Filtering by the other project excludes it.
    assert len(fetch_export_data(db, project_filter="beta")) == 0


def test_project_filter_command_level_only_matches(tmp_path):
    """#498 scenario 2: only a command-level project matches. The session's
    final project is Beta, but a command ran in Alpha - the session must be
    included when filtering by Alpha."""
    cmd_alpha = Command(id=101, timestamp=1010, command="git status", exit_code=0,
                        session_id=1, project_id=1)
    cmd_beta = Command(id=102, timestamp=1030, command="git log", exit_code=0,
                       session_id=1, project_id=2)
    s1 = Session(id=1, start_time=1000, end_time=1050, duration_seconds=50,
                 project_id=2, commands=[cmd_alpha, cmd_beta])
    db = _make_attribution_db(tmp_path / "r2.db", _alpha_beta_projects(), [s1],
                              [cmd_alpha, cmd_beta])
    alpha_sessions = fetch_export_data(db, project_filter="alpha")
    assert len(alpha_sessions) == 1
    assert alpha_sessions[0].id == 1
    # Beta still matches too (session-level + a command).
    assert len(fetch_export_data(db, project_filter="beta")) == 1


def test_project_filter_command_level_does_not_match(tmp_path):
    """#498 scenario 3: neither the session nor any command is attributed to
    the requested project - the session is excluded."""
    projects = _alpha_beta_projects() + [
        Project(id=3, name="Gamma", path="~/src/gamma",
                first_seen=1000, last_seen=5000, session_count=0, total_time=0),
    ]
    cmd_beta = Command(id=101, timestamp=1010, command="git status", exit_code=0,
                       session_id=1, project_id=2)
    cmd_gamma = Command(id=102, timestamp=1030, command="npm test", exit_code=0,
                        session_id=1, project_id=3)
    s1 = Session(id=1, start_time=1000, end_time=1050, duration_seconds=50,
                 project_id=2, commands=[cmd_beta, cmd_gamma])
    db = _make_attribution_db(tmp_path / "r3.db", projects, [s1],
                              [cmd_beta, cmd_gamma])
    assert len(fetch_export_data(db, project_filter="alpha")) == 0
    # Sanity: the session is still reachable via its actual projects.
    assert len(fetch_export_data(db, project_filter="beta")) == 1
def test_project_filter_mixed_project_session(tmp_path):
    """#498 scenario 4: a session with commands attributed to both Alpha and
    Beta, final project Beta - filtering by either project includes it."""
    cmd_a1 = Command(id=101, timestamp=1010, command="cd ~/src/alpha", exit_code=0,
                     session_id=1, project_id=1)
    cmd_b1 = Command(id=102, timestamp=1030, command="cd ~/src/beta", exit_code=0,
                     session_id=1, project_id=2)
    cmd_a2 = Command(id=103, timestamp=1040, command="git commit", exit_code=0,
                     session_id=1, project_id=1)
    s1 = Session(id=1, start_time=1000, end_time=1050, duration_seconds=50,
                 project_id=2, commands=[cmd_a1, cmd_b1, cmd_a2])
    db = _make_attribution_db(tmp_path / "r4.db", _alpha_beta_projects(), [s1],
                              [cmd_a1, cmd_b1, cmd_a2])
    by_alpha = fetch_export_data(db, project_filter="alpha")
    by_beta = fetch_export_data(db, project_filter="beta")
    assert len(by_alpha) == 1 and by_alpha[0].id == 1
    assert len(by_beta) == 1 and by_beta[0].id == 1


def test_project_filter_single_project_unchanged(tmp_path):
    """#498 scenario 5: single-project sessions behave exactly as before
    (case-insensitive name AND path matching)."""
    cmd_a = Command(id=101, timestamp=1010, command="git status", exit_code=0,
                    session_id=1, project_id=1)
    cmd_b = Command(id=102, timestamp=1110, command="ls", exit_code=0,
                    session_id=2, project_id=2)
    s1 = Session(id=1, start_time=1000, end_time=1050, duration_seconds=50,
                 project_id=1, commands=[cmd_a])
    s2 = Session(id=2, start_time=2000, end_time=2050, duration_seconds=50,
                 project_id=2, commands=[cmd_b])
    db = _make_attribution_db(tmp_path / "r5.db", _alpha_beta_projects(), [s1, s2],
                              [cmd_a, cmd_b])
    assert len(fetch_export_data(db, project_filter="ALPHA")) == 1
    assert len(fetch_export_data(db, project_filter="src/alpha")) == 1
    assert len(fetch_export_data(db, project_filter="src/beta")) == 1
    assert len(fetch_export_data(db, project_filter="nonexistent")) == 0


def test_project_filter_no_project_attribution(tmp_path):
    """#498 scenario 6: a session with no project attribution (session-level
    and per-command) is handled by the existing 'other' bucket."""
    cmd = Command(id=101, timestamp=1010, command="ls -la", exit_code=0,
                  session_id=1, project_id=None)
    s1 = Session(id=1, start_time=1000, end_time=1050, duration_seconds=50,
                 project_id=None, commands=[cmd])
    db = _make_attribution_db(tmp_path / "r6.db", _alpha_beta_projects(), [s1], [cmd])
    assert len(fetch_export_data(db, project_filter="other")) == 1
    assert len(fetch_export_data(db, project_filter="general")) == 1
    assert len(fetch_export_data(db, project_filter="no project")) == 1
    assert len(fetch_export_data(db, project_filter="alpha")) == 0


def test_project_filter_command_path_match(tmp_path):
    """Command-level project can be matched by project path, not only name."""
    cmd_alpha = Command(id=101, timestamp=1010, command="git status", exit_code=0,
                        session_id=1, project_id=1)
    s1 = Session(id=1, start_time=1000, end_time=1050, duration_seconds=50,
                 project_id=2, commands=[cmd_alpha])
    db = _make_attribution_db(tmp_path / "r7.db", _alpha_beta_projects(), [s1], [cmd_alpha])
    sessions = fetch_export_data(db, project_filter="src/alpha")
    assert len(sessions) == 1
    assert sessions[0].id == 1


def test_project_filter_command_text_does_not_cause_match(tmp_path):
    """#498 Step 6: a session must NOT match merely because a command's text
    contains the project name - only persisted project_id attribution counts."""
    cmd = Command(id=101, timestamp=1010, command="git status in alpha repo",
                  exit_code=0, session_id=1, project_id=None)
    s1 = Session(id=1, start_time=1000, end_time=1050, duration_seconds=50,
                 project_id=None, commands=[cmd])
    db = _make_attribution_db(tmp_path / "r8.db", _alpha_beta_projects(), [s1], [cmd])
    assert len(fetch_export_data(db, project_filter="alpha")) == 0
    assert len(fetch_export_data(db, project_filter="other")) == 1


def test_project_filter_command_path_in_text_does_not_cause_match(tmp_path):
    """#498 Step 6: a command whose text contains a project path string must
    not match unless its project_id is actually attributed."""
    cmd = Command(id=101, timestamp=1010, command="cd ~/src/alpha", exit_code=0,
                  session_id=1, project_id=None)
    s1 = Session(id=1, start_time=1000, end_time=1050, duration_seconds=50,
                 project_id=None, commands=[cmd])
    db = _make_attribution_db(tmp_path / "r9.db", _alpha_beta_projects(), [s1], [cmd])
    assert len(fetch_export_data(db, project_filter="src/alpha")) == 0
    assert len(fetch_export_data(db, project_filter="other")) == 1


def test_project_filter_unknown_project_name_excludes(tmp_path):
    """#498: an unrelated project name with no attribution match excludes."""
    cmd_beta = Command(id=101, timestamp=1010, command="git log", exit_code=0,
                       session_id=1, project_id=2)
    s1 = Session(id=1, start_time=1000, end_time=1050, duration_seconds=50,
                 project_id=2, commands=[cmd_beta])
    db = _make_attribution_db(tmp_path / "r10.db", _alpha_beta_projects(), [s1], [cmd_beta])
    assert len(fetch_export_data(db, project_filter="alpha")) == 0
    assert len(fetch_export_data(db, project_filter="gamma")) == 0


def test_project_filter_other_includes_session_with_unattributed_command(tmp_path):
    """P1: session final project = Beta, one command in Alpha, one command
    unattributed (project_id=None) - the 'other' bucket must include it."""
    cmd_alpha = Command(id=101, timestamp=1010, command="git status", exit_code=0,
                        session_id=1, project_id=1)
    cmd_none = Command(id=102, timestamp=1020, command="ls -la", exit_code=0,
                       session_id=1, project_id=None)
    s1 = Session(id=1, start_time=1000, end_time=1050, duration_seconds=50,
                 project_id=2, commands=[cmd_alpha, cmd_none])
    db = _make_attribution_db(tmp_path / "p1a.db", _alpha_beta_projects(), [s1],
                              [cmd_alpha, cmd_none])
    assert len(fetch_export_data(db, project_filter="other")) == 1
    assert len(fetch_export_data(db, project_filter="general")) == 1
    assert len(fetch_export_data(db, project_filter="no project")) == 1


def test_project_filter_other_excludes_session_with_all_real_commands(tmp_path):
    """P1: session final project = Beta, all commands attributed to real
    projects - the 'other' bucket must exclude it."""
    cmd_alpha = Command(id=101, timestamp=1010, command="git status", exit_code=0,
                        session_id=1, project_id=1)
    cmd_beta = Command(id=102, timestamp=1030, command="git log", exit_code=0,
                       session_id=1, project_id=2)
    s1 = Session(id=1, start_time=1000, end_time=1050, duration_seconds=50,
                 project_id=2, commands=[cmd_alpha, cmd_beta])
    db = _make_attribution_db(tmp_path / "p1b.db", _alpha_beta_projects(), [s1],
                              [cmd_alpha, cmd_beta])
    assert len(fetch_export_data(db, project_filter="other")) == 0
    assert len(fetch_export_data(db, project_filter="general")) == 0
    # Sanity: it still matches its real projects.
    assert len(fetch_export_data(db, project_filter="alpha")) == 1
    assert len(fetch_export_data(db, project_filter="beta")) == 1


def test_project_filter_other_includes_session_with_none_project_id(tmp_path):
    """P1: a session whose own project_id is None is still in the 'other'
    bucket (existing behaviour preserved)."""
    cmd_none = Command(id=101, timestamp=1010, command="ls -la", exit_code=0,
                       session_id=1, project_id=None)
    s1 = Session(id=1, start_time=1000, end_time=1050, duration_seconds=50,
                 project_id=None, commands=[cmd_none])
    db = _make_attribution_db(tmp_path / "p1c.db", _alpha_beta_projects(), [s1], [cmd_none])
    assert len(fetch_export_data(db, project_filter="other")) == 1
    assert len(fetch_export_data(db, project_filter="general")) == 1
    assert len(fetch_export_data(db, project_filter="no project")) == 1


def test_project_filter_other_excludes_unknown_command_project_id(tmp_path):
    """P1: an unknown/nonexistent command project_id must NOT be treated as
    "no project" and make the session match 'other'.

    ``Database.save_data`` enforces a foreign key, so a command pointing at a
    project ID missing from the ``projects`` table cannot be persisted through
    the public API. Instead exercise :func:`_session_matches_project_filter`
    directly with a ``project_map`` that omits the command's (stale/unknown)
    ID -- exactly the guard the export path relies on.
    """
    cmd_ghost = Command(id=101, timestamp=1010, command="git status", exit_code=0,
                        session_id=1, project_id=9999)  # unknown ID
    s1 = Session(id=1, start_time=1000, end_time=1050, duration_seconds=50,
                 project_id=2, commands=[cmd_ghost])
    # project_map has only the session's project (Beta); 9999 is absent.
    project_map = {2: Project(id=2, name="Beta", path="~/src/beta",
                              first_seen=1000, last_seen=5000,
                              session_count=0, total_time=0)}
    assert _session_matches_project_filter(s1, project_map, "other") is False
    assert _session_matches_project_filter(s1, project_map, "general") is False
    assert _session_matches_project_filter(s1, project_map, "no project") is False
    # Nor does it match a real named project.
    assert _session_matches_project_filter(s1, project_map, "alpha") is False


def test_project_filter_other_excludes_unattributed_only_in_text(tmp_path):
    """P1: a command that merely *mentions* a project name in its text, but is
    attributed to a real project, does not count as 'other'."""
    cmd_beta = Command(id=101, timestamp=1010, command="git status other repo",
                       exit_code=0, session_id=1, project_id=2)
    s1 = Session(id=1, start_time=1000, end_time=1050, duration_seconds=50,
                 project_id=2, commands=[cmd_beta])
    db = _make_attribution_db(tmp_path / "p1e.db", _alpha_beta_projects(), [s1], [cmd_beta])
    assert len(fetch_export_data(db, project_filter="other")) == 0


def test_project_filter_mixed_session_matches_named_and_other(tmp_path):
    """P1: a mixed session with Alpha + Beta commands plus an unattributed
    command matches the correct named filters AND 'other'."""
    cmd_alpha = Command(id=101, timestamp=1010, command="git status", exit_code=0,
                        session_id=1, project_id=1)
    cmd_beta = Command(id=102, timestamp=1030, command="git log", exit_code=0,
                       session_id=1, project_id=2)
    cmd_none = Command(id=103, timestamp=1040, command="ls -la", exit_code=0,
                       session_id=1, project_id=None)
    s1 = Session(id=1, start_time=1000, end_time=1050, duration_seconds=50,
                 project_id=2, commands=[cmd_alpha, cmd_beta, cmd_none])
    db = _make_attribution_db(tmp_path / "p1f.db", _alpha_beta_projects(), [s1],
                              [cmd_alpha, cmd_beta, cmd_none])
    assert len(fetch_export_data(db, project_filter="alpha")) == 1
    assert len(fetch_export_data(db, project_filter="beta")) == 1
    assert len(fetch_export_data(db, project_filter="other")) == 1
    assert len(fetch_export_data(db, project_filter="general")) == 1
    assert len(fetch_export_data(db, project_filter="no project")) == 1
    # Unrelated project excluded.
    assert len(fetch_export_data(db, project_filter="gamma")) == 0
