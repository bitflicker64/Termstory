import json
import urllib.request
from datetime import datetime
from typer.testing import CliRunner
import pytest

from termstory.cli import app
from termstory.database import Database
from termstory.models import Project, Session, Command
from termstory.ai import translate_git_anger, predict_bugs_from_sessions
from termstory.insights import detect_late_night_chaotic_sessions
from termstory.formatter import (
    format_anger_translation,
    format_anger_translation_heuristics,
    format_bug_predictions,
    format_bug_predictions_heuristics
)

class MockResponse:
    def __init__(self, data, status_code=200):
        self.data = data
        self.status = status_code
        
    def read(self):
        return self.data
        
    def __enter__(self):
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


def test_ai_translate_git_anger(monkeypatch):
    called = []
    
    def mock_urlopen(req, timeout=None):
        called.append(req)
        resp_payload = {
            "choices": [
                {
                    "message": {
                        "content": "😡 RAGE: You failed tests multiple times before checking in this code!"
                    }
                }
            ]
        }
        return MockResponse(json.dumps(resp_payload).encode("utf-8"))
        
    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    
    commit_data = [
        {
            "hash": "abcdef12345",
            "message": "feat: finish task",
            "preceding_errors": ["pytest tests/", "python run.py"]
        }
    ]
    
    res = translate_git_anger(
        commit_data,
        api_key="test-key",
        api_base_url="https://api.openai.com/v1",
        model_name="gpt-4o",
        provider="openai"
    )
    
    assert res == "😡 RAGE: You failed tests multiple times before checking in this code!"
    assert len(called) == 1


def test_ai_predict_bugs_from_sessions(monkeypatch):
    called = []
    
    def mock_urlopen(req, timeout=None):
        called.append(req)
        resp_payload = {
            "choices": [
                {
                    "message": {
                        "content": "🔮 Predicted Bug: Missing exception handler in test script."
                    }
                }
            ]
        }
        return MockResponse(json.dumps(resp_payload).encode("utf-8"))
        
    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    
    sessions_data = [
        {
            "session_id": 1,
            "project_name": "Project X",
            "hour": 2,
            "failed_commands": ["npm run build"],
            "commands": ["npm run build", "git commit -m 'fix'"],
            "commits": ["fix stuff"]
        }
    ]
    
    res = predict_bugs_from_sessions(
        sessions_data,
        api_key="test-key",
        api_base_url="https://api.openai.com/v1",
        model_name="gpt-4o",
        provider="openai"
    )
    
    assert res == "🔮 Predicted Bug: Missing exception handler in test script."
    assert len(called) == 1


def test_detect_late_night_chaotic_sessions(tmp_path):
    db_file = tmp_path / "test_insights.db"
    db = Database(str(db_file))
    db.init_db()
    
    # Session at 2 AM (late night)
    late_night_start = int(datetime(2026, 6, 16, 2, 0, 0).timestamp())
    p = Project(id=1, name="Project Alpha", path="~/alpha", first_seen=late_night_start, last_seen=late_night_start, session_count=1, total_time=1)
    
    # 10 commands (chaotic)
    cmds = []
    for i in range(10):
        cmds.append(Command(id=i, timestamp=late_night_start + i, command=f"echo command_{i}", exit_code=1 if i < 3 else 0, session_id=1, project_id=1))
        
    s = Session(id=1, start_time=late_night_start, end_time=late_night_start + 100, duration_seconds=100, project_id=1, commands=cmds)
    
    db.save_data([p], [s], cmds)
    
    sessions = detect_late_night_chaotic_sessions(db)
    
    assert len(sessions) == 1
    assert sessions[0]["session_id"] == 1
    assert sessions[0]["project_name"] == "Project Alpha"
    assert sessions[0]["hour"] == 2
    assert len(sessions[0]["failed_commands"]) == 3
    assert len(sessions[0]["commands"]) == 10


def test_formatters():
    # Test anger translation
    output_anger = format_anger_translation("Raging developer logs")
    assert "Git-Blame Anger Translator" in output_anger
    assert "Raging developer logs" in output_anger
    
    # Test anger heuristics
    commit_data = [
        {
            "hash": "abcdef12345",
            "message": "fix: resolve crash",
            "preceding_errors": ["python run.py", "pytest"]
        }
    ]
    output_anger_h = format_anger_translation_heuristics(commit_data)
    assert "Heuristic Fallback Mode" in output_anger_h
    assert "resolve crash" in output_anger_h
    
    # Test bug predictions
    output_bugs = format_bug_predictions("Mock leak predicted")
    assert "Predictive Bug Fortune Teller" in output_bugs
    assert "Mock leak predicted" in output_bugs
    
    # Test bug heuristics
    sessions = [
        {
            "session_id": 1,
            "hour": 3,
            "project_name": "termstory",
            "failed_commands": ["pytest"],
            "commands": ["pytest", "git commit --amend"],
            "commits": []
        }
    ]
    output_bugs_h = format_bug_predictions_heuristics(sessions)
    assert "Heuristic Fallback Mode" in output_bugs_h
    assert "Predicted Bug:" in output_bugs_h


def test_cli_commands(tmp_path, monkeypatch):
    monkeypatch.setenv("TERMSTORY_DATE_OVERRIDE", "2026-06-16 12:00:00")
    db_file = tmp_path / "test_cli.db"
    monkeypatch.setattr("termstory.cli.get_db_path", lambda: str(db_file))
    monkeypatch.setattr("termstory.config.get_db_path", lambda: str(db_file))
    monkeypatch.setattr("termstory.cli.get_history_files", lambda: [])
    
    db = Database(str(db_file))
    db.init_db()
    
    # Late night session (1 AM)
    late_night_start = int(datetime(2026, 6, 16, 1, 0, 0).timestamp())
    p = Project(id=1, name="Project Alpha", path="~/alpha", first_seen=late_night_start, last_seen=late_night_start, session_count=1, total_time=1)
    
    # 10 commands (chaotic)
    cmds = []
    for i in range(10):
        cmds.append(Command(id=i, timestamp=late_night_start + i, command=f"echo command_{i}", exit_code=1 if i < 3 else 0, session_id=1, project_id=1))
        
    s = Session(id=1, start_time=late_night_start, end_time=late_night_start + 100, duration_seconds=100, project_id=1, commands=cmds)
    
    db.save_data([p], [s], cmds)
    
    # Save a commit
    commits = [
        {"hash": "abcdef1234567890", "timestamp": late_night_start + 50, "message": "fix: crash in parser", "cleaned_message": "crash in parser"}
    ]
    db.save_commits(p.id, commits)
    
    runner = CliRunner()
    
    # Test anger-translator
    result_anger = runner.invoke(app, ["anger-translator"])
    assert result_anger.exit_code == 0
    assert "Git-Blame Anger Translator" in result_anger.stdout
    assert "crash in parser" in result_anger.stdout
    
    # Test fortune-teller
    result_fortune = runner.invoke(app, ["fortune-teller"])
    assert result_fortune.exit_code == 0
    assert "Predictive Bug Fortune Teller" in result_fortune.stdout
    assert "Project Alpha" in result_fortune.stdout


def test_rpg_class_vampire_index_cli_and_formatters(tmp_path, monkeypatch):
    monkeypatch.setenv("TERMSTORY_DATE_OVERRIDE", "2026-06-16 12:00:00")
    db_file = tmp_path / "test_cli_rpg.db"
    monkeypatch.setattr("termstory.cli.get_db_path", lambda: str(db_file))
    monkeypatch.setattr("termstory.config.get_db_path", lambda: str(db_file))
    monkeypatch.setattr("termstory.cli.get_history_files", lambda: [])
    
    db = Database(str(db_file))
    db.init_db()
    
    # Session
    now = int(datetime(2026, 6, 16, 12, 0, 0).timestamp())
    p = Project(id=1, name="Project Alpha", path="~/alpha", first_seen=now, last_seen=now, session_count=1, total_time=1)
    
    cmds = [
        Command(id=1, timestamp=now, command="git commit -m 'feat: main'", exit_code=0, session_id=1, project_id=1),
        Command(id=2, timestamp=now + 5, command="git push", exit_code=0, session_id=1, project_id=1),
    ]
    s = Session(id=1, start_time=now, end_time=now + 100, duration_seconds=100, project_id=1, commands=cmds)
    db.save_data([p], [s], cmds)
    
    runner = CliRunner()
    
    # Test rpg-class subcommand
    result_rpg = runner.invoke(app, ["rpg-class"])
    assert result_rpg.exit_code == 0
    assert "Daily RPG Class Assigner" in result_rpg.stdout
    assert "Git Paladin" in result_rpg.stdout
    
    # Test vampire-index subcommand
    result_vamp = runner.invoke(app, ["vampire-index"])
    assert result_vamp.exit_code == 0
    assert "The Vampire Coder Index" in result_vamp.stdout
    assert "Vampire Index : 0.0%" in result_vamp.stdout

