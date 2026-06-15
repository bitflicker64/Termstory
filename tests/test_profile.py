import os
import json
from datetime import datetime
from typer.testing import CliRunner
import pytest

from termstory.cli import app
from termstory.database import Database
from termstory.models import Project, Session, Command

@pytest.fixture
def temp_profile_db(tmp_path, monkeypatch):
    db_file = tmp_path / "test_profile.db"
    monkeypatch.setattr("termstory.cli.get_db_path", lambda: str(db_file))
    monkeypatch.setattr("termstory.config.get_db_path", lambda: str(db_file))
    monkeypatch.setattr("termstory.cli.get_history_files", lambda: [])
    monkeypatch.setattr("termstory.cli.run_ingestion", lambda db: None)

    db = Database(str(db_file))
    db.init_db()
    
    # Insert mock data
    p = Project(id=1, name="Profile Project", path="~/profile", first_seen=2000, last_seen=2000, session_count=1, total_time=100)
    cmd1 = Command(id=1, timestamp=2000, command="echo 'first'", exit_code=0, session_id=1, project_id=1)
    cmd2 = Command(id=2, timestamp=2010, command="echo 'second'", exit_code=0, session_id=1, project_id=1)
    s = Session(id=1, start_time=2000, end_time=2010, duration_seconds=10, project_id=1, commands=[cmd1, cmd2])
    db.save_data([p], [s], [cmd1, cmd2])
    
    return db

def test_db_profiling_instrumentation(temp_profile_db):
    # Running query via Database connection should write to query_profile table
    conn = temp_profile_db.get_connection()
    conn.execute("SELECT name FROM projects")
    conn.close()
    
    queries = temp_profile_db.get_slowest_queries(limit=5)
    assert len(queries) > 0
    # The SELECT query we ran should be profiled
    assert any("SELECT name FROM projects" in q["sql"] for q in queries)

def test_db_session_profiling(temp_profile_db):
    profile_data = temp_profile_db.get_profile_sessions(limit=5)
    assert len(profile_data["longest_sessions"]) == 1
    assert profile_data["longest_sessions"][0]["project_name"] == "Profile Project"
    assert profile_data["longest_sessions"][0]["command_count"] == 2

    assert len(profile_data["highest_count_sessions"]) == 1
    assert profile_data["highest_count_sessions"][0]["command_count"] == 2

def test_cli_profile_queries(temp_profile_db):
    # Ensure there's some query profiled
    conn = temp_profile_db.get_connection()
    conn.execute("SELECT * FROM projects")
    conn.close()

    runner = CliRunner()
    result = runner.invoke(app, ["profile", "queries"])
    assert result.exit_code == 0
    assert "Top 10 Slowest DB Queries" in result.stdout
    assert "SELECT * FROM projects" in result.stdout

def test_cli_profile_sessions(temp_profile_db):
    runner = CliRunner()
    result = runner.invoke(app, ["profile", "sessions"])
    assert result.exit_code == 0
    assert "Top 10 Longest Sessions" in result.stdout
    assert "Top 10 Sessions by Command Count" in result.stdout
    assert "Profile Project" in result.stdout
