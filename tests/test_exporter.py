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
from termstory.exporter import parse_since, fetch_export_data, serialize_sessions_to_dict, export_json, export_csv, export_markdown

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

