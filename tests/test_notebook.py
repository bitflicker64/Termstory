import os
from datetime import datetime, timedelta
import pytest
from typer.testing import CliRunner

from termstory.cli import app
from termstory.database import Database
from termstory.models import Project, Session, Command
from termstory.notebook import generate_notebook

@pytest.fixture
def temp_db(tmp_path):
    db_file = tmp_path / "test_notebook.db"
    db = Database(str(db_file))
    db.init_db()

    # Create two projects
    p1 = Project(id=1, name="Project Alpha", path="~/src/alpha", first_seen=1000, last_seen=2000, session_count=1, total_time=100)
    p2 = Project(id=2, name="Project Beta", path="~/src/beta", first_seen=2000, last_seen=3000, session_count=1, total_time=200)

    # Choose two distinct days
    # Day 1: 2026-06-03
    t1 = int(datetime(2026, 6, 3, 10, 0, 0).timestamp())
    t2 = int(datetime(2026, 6, 3, 10, 30, 0).timestamp())
    # Day 2: 2026-06-04
    t3 = int(datetime(2026, 6, 4, 15, 0, 0).timestamp())

    # Mock commands
    c1 = Command(id=101, timestamp=t1, command="git status", exit_code=0, session_id=1, project_id=1)
    c2 = Command(id=102, timestamp=t1 + 60, command="make build", exit_code=0, session_id=1, project_id=1)
    c3 = Command(id=103, timestamp=t2, command="cd ~/src/beta", exit_code=0, session_id=2, project_id=2)
    c4 = Command(id=104, timestamp=t2 + 60, command="python run.py", exit_code=0, session_id=2, project_id=2)
    c5 = Command(id=105, timestamp=t3, command="git commit -m 'wip'", exit_code=0, session_id=3, project_id=1)

    # Mock sessions
    s1 = Session(id=1, start_time=t1, end_time=t1 + 60, duration_seconds=60, project_id=1, commands=[c1, c2], ai_summary="Built project alpha.")
    s2 = Session(id=2, start_time=t2, end_time=t2 + 60, duration_seconds=60, project_id=2, commands=[c3, c4], ai_summary="Switched to project beta and executed python runner.")
    s3 = Session(id=3, start_time=t3, end_time=t3, duration_seconds=0, project_id=1, commands=[c5], ai_summary="Committed progress.")

    db.save_data([p1, p2], [s1, s2, s3], [c1, c2, c3, c4, c5])
    db.save_session_ai_summary(s1.id, "Built project alpha.")
    db.save_session_ai_summary(s2.id, "Switched to project beta and executed python runner.")
    db.save_session_ai_summary(s3.id, "Committed progress.")

    # Save a commit
    db.save_commits(1, [{"hash": "a1b2c3d4e5f6", "timestamp": t1 + 30, "message": "feat: build alpha", "cleaned_message": "build alpha"}])

    return db

def test_generate_notebook_basic(temp_db):
    sessions = temp_db.get_range_sessions(0, 9999999999)
    # Default is chronological: 2026-06-03 first, then 2026-06-04
    markdown = generate_notebook(sessions, temp_db, all_commands=True)

    assert "# 2026-06-03" in markdown
    assert "# 2026-06-04" in markdown
    assert "## Projects" in markdown
    assert "## Timeline & AI Summaries" in markdown

    # Check project details on 2026-06-03
    assert "### Project Alpha" in markdown
    assert "### Project Beta" in markdown
    assert "Built project alpha." in markdown
    assert "Switched to project beta" in markdown

    # Check commits
    assert "a1b2c3d" in markdown
    assert "build alpha" in markdown

    # Check commands
    assert "make build" in markdown
    assert "python run.py" in markdown

def test_generate_notebook_noise_filtering(temp_db):
    sessions = temp_db.get_range_sessions(0, 9999999999)
    
    # 1. Without all_commands (noise commands filtered out: 'git status', 'cd ~/src/beta')
    markdown_filtered = generate_notebook(sessions, temp_db, all_commands=False)
    assert "git status" not in markdown_filtered
    assert "cd ~/src/beta" not in markdown_filtered
    assert "make build" in markdown_filtered
    assert "python run.py" in markdown_filtered

    # 2. With all_commands (noise commands included)
    markdown_all = generate_notebook(sessions, temp_db, all_commands=True)
    assert "git status" in markdown_all
    assert "cd ~/src/beta" in markdown_all
    assert "make build" in markdown_all
    assert "python run.py" in markdown_all

def test_generate_notebook_reverse_sorting(temp_db):
    sessions = temp_db.get_range_sessions(0, 9999999999)

    # 1. Chronological (default)
    markdown_chrono = generate_notebook(sessions, temp_db, reverse=False)
    idx_03 = markdown_chrono.index("# 2026-06-03")
    idx_04 = markdown_chrono.index("# 2026-06-04")
    assert idx_03 < idx_04

    # 2. Reverse-chronological
    markdown_reverse = generate_notebook(sessions, temp_db, reverse=True)
    idx_03_rev = markdown_reverse.index("# 2026-06-03")
    idx_04_rev = markdown_reverse.index("# 2026-06-04")
    assert idx_04_rev < idx_03_rev

def test_cli_notebook_command(temp_db, tmp_path, monkeypatch):
    monkeypatch.setenv("TERMSTORY_DATE_OVERRIDE", "2026-06-04")
    monkeypatch.setattr("termstory.cli.get_db_path", lambda: temp_db.db_path)
    monkeypatch.setattr("termstory.config.get_db_path", lambda: temp_db.db_path)
    monkeypatch.setattr("termstory.cli.get_history_files", lambda: [])

    runner = CliRunner()

    # 1. Stdout output
    result = runner.invoke(app, ["notebook"])
    assert result.exit_code == 0
    assert "# 2026-06-03" in result.stdout
    assert "# 2026-06-04" in result.stdout

    # 2. Project filter
    result_filter = runner.invoke(app, ["notebook", "--project", "Beta"])
    assert result_filter.exit_code == 0
    assert "Project Beta" in result_filter.stdout
    assert "Project Alpha" not in result_filter.stdout

    # 3. Output to file
    out_file = tmp_path / "notebook.md"
    result_file = runner.invoke(app, ["notebook", "--output", str(out_file)])
    assert result_file.exit_code == 0
    assert "Notebook successfully exported to" in result_file.stdout
    assert out_file.exists()
    
    file_content = out_file.read_text(encoding="utf-8")
    assert "# 2026-06-03" in file_content
    assert "# 2026-06-04" in file_content

def test_generate_notebook_multiline_commit(temp_db):
    sessions = temp_db.get_range_sessions(0, 9999999999)
    s = sessions[0]
    s.commits = [{
        "hash": "b2c3d4e5f6g7",
        "timestamp": s.start_time,
        "message": "feat: add first feature\n\nHere is a detailed explanation of the feature.",
        "cleaned_message": "feat: add first feature\n\nHere is a detailed explanation of the feature."
    }]
    markdown = generate_notebook([s], temp_db)
    assert "feat: add first feature" in markdown
    assert "Here is a detailed explanation" not in markdown


AWS_SAMPLE_SECRET = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"


def test_generate_notebook_redacts_secret_command(temp_db):
    cmd = Command(id=501, timestamp=1000,
                  command=f"export AWS_SECRET_ACCESS_KEY={AWS_SAMPLE_SECRET}",
                  exit_code=0, session_id=1, project_id=1)
    s = Session(id=1, start_time=1000, end_time=2000, duration_seconds=1000,
                project_id=1, commands=[cmd])
    markdown = generate_notebook([s], temp_db, all_commands=True)
    assert AWS_SAMPLE_SECRET not in markdown
    assert "export AWS_SECRET_ACCESS_KEY=[REDACTED]" in markdown


def test_generate_notebook_blacklisted_session_redacted(temp_db):
    cmd = Command(id=502, timestamp=1000, command="vault read secret/data",
                  exit_code=0, session_id=1, project_id=1)
    s = Session(id=1, start_time=1000, end_time=2000, duration_seconds=1000,
                project_id=1, commands=[cmd])
    markdown = generate_notebook([s], temp_db, all_commands=True)
    assert "vault read secret" not in markdown
    assert "Security/Authentication Operations" in markdown


def test_generate_notebook_redacts_commit_message(temp_db):
    s = Session(
        id=1, start_time=1000, end_time=2000, duration_seconds=1000, project_id=1,
        commands=[Command(id=503, timestamp=1000, command="git commit", exit_code=0,
                          session_id=1, project_id=1)],
        commits=[{"hash": "abc123def456", "timestamp": 1500,
                  "message": "feat: login password: hunter2",
                  "cleaned_message": "login password: hunter2"}],
    )
    markdown = generate_notebook([s], temp_db)
    assert "hunter2" not in markdown
