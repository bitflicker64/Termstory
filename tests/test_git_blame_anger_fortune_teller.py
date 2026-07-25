import json
import urllib.request
from datetime import datetime
from typer.testing import CliRunner
import pytest

from termstory.cli import app, get_ai_provider_settings
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


def test_get_ai_provider_settings_reads_nested_ollama_config():
    config = {
        "active_provider": "ollama",
        "providers": {
            "ollama": {
                "api_key": "",
                "api_base_url": "http://ollama.internal:11434/v1",
                "model_name": "llama3.2",
            }
        },
    }

    provider, api_key, api_base_url, model_name = get_ai_provider_settings(config)

    assert provider == "ollama"
    assert api_key == ""
    assert api_base_url == "http://ollama.internal:11434/v1"
    assert model_name == "llama3.2"


def test_get_ai_provider_settings_keeps_legacy_fallbacks():
    config = {
        "ai_provider": "openai",
        "openai_api_key": "legacy-key",
        "openai_api_base_url": "https://legacy.example/v1",
        "openai_model_name": "legacy-model",
    }

    provider, api_key, api_base_url, model_name = get_ai_provider_settings(config)

    assert provider == "openai"
    assert api_key == "legacy-key"
    assert api_base_url == "https://legacy.example/v1"
    assert model_name == "legacy-model"


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

def test_format_bug_predictions_heuristics_force_push_branch():
    """Force-push / amend / reset / revert sessions predict git desync bugs."""
    sessions = [
        {
            "session_id": 42,
            "hour": 2,
            "project_name": "termstory",
            "failed_commands": [],
            "commands": ["git push --force origin main", "git commit --amend"],
            "commits": [],
        }
    ]
    out = format_bug_predictions_heuristics(sessions)
    assert "Detached HEAD or Git Desynchronization" in out
    assert "history corruption" in out


def test_format_bug_predictions_heuristics_git_add_all_branch():
    """Frantic `git add .` predicts committed secrets / artifacts."""
    sessions = [
        {
            "session_id": 7,
            "hour": 3,
            "project_name": "side-project",
            "failed_commands": [],
            "commands": ["git add .", "git commit -m wip"],
            "commits": [],
        }
    ]
    out = format_bug_predictions_heuristics(sessions)
    assert "Accidentally Committed Secrets" in out
    assert "git filter-repo" in out


def test_format_bug_predictions_heuristics_test_bypass_branch():
    """`pytest --deselect` / `-k not` / `--skip` predict silently bypassed tests."""
    sessions = [
        {
            "session_id": 9,
            "hour": 1,
            "project_name": "termstory",
            "failed_commands": [],
            "commands": ["pytest --deselect tests/test_hard.py", "pytest -k 'not slow'"],
            "commits": [],
        }
    ]
    out = format_bug_predictions_heuristics(sessions)
    assert "Silently Bypassed Test" in out


def test_format_bug_predictions_heuristics_docker_branch():
    """Docker churn predicts zombie containers."""
    sessions = [
        {
            "session_id": 11,
            "hour": 2,
            "project_name": "infra",
            "failed_commands": [],
            "commands": ["docker compose up -d", "docker ps"],
            "commits": [],
        }
    ]
    out = format_bug_predictions_heuristics(sessions)
    assert "Docker Port Bind Collision" in out


def test_format_bug_predictions_heuristics_off_by_one_fallback():
    """No matching chaos signal falls through to the sleep-deprived off-by-one branch."""
    sessions = [
        {
            "session_id": 13,
            "hour": 4,
            "project_name": "misc",
            "failed_commands": [],
            "commands": ["ls -la", "cat README.md", "echo hi"] * 4,
            "commits": [],
        }
    ]
    out = format_bug_predictions_heuristics(sessions)
    assert "Sleep-Deprived Off-by-One" in out


def test_detect_late_night_chaotic_sessions_excludes_legacy(tmp_path):
    """Legacy/synthetic sessions must be excluded from chaotic detection (issue #39 pitfall)."""
    db_file = tmp_path / "test_legacy.db"
    db = Database(str(db_file))
    db.init_db()

    late_night_start = int(datetime(2026, 6, 16, 2, 0, 0).timestamp())
    p = Project(
        id=1, name="Project Alpha", path="~/alpha",
        first_seen=late_night_start, last_seen=late_night_start,
        session_count=1, total_time=1,
    )

    # 10 legacy commands — every command is_legacy=True, so the whole
    # session is legacy and must be filtered out by the detector.
    legacy_cmds = [
        Command(
            id=i, timestamp=late_night_start + i,
            command=f"git push --force {i}",  # desperate pattern, but legacy
            exit_code=1, session_id=1, project_id=1, is_legacy=True,
        )
        for i in range(10)
    ]
    s = Session(
        id=1, start_time=late_night_start, end_time=late_night_start + 100,
        duration_seconds=100, project_id=1, commands=legacy_cmds,
    )
    db.save_data([p], [s], legacy_cmds)

    sessions = detect_late_night_chaotic_sessions(db)
    assert sessions == [], "legacy/synthetic late-night session should be excluded"


def test_detect_late_night_chaotic_sessions_frantic_git_add(tmp_path):
    """A single frantic `git add .` in a late-night session marks it chaotic."""
    db_file = tmp_path / "test_git_add.db"
    db = Database(str(db_file))
    db.init_db()

    late_night_start = int(datetime(2026, 6, 16, 3, 0, 0).timestamp())
    p = Project(
        id=1, name="Project Beta", path="~/beta",
        first_seen=late_night_start, last_seen=late_night_start,
        session_count=1, total_time=1,
    )
    # Only 1 command — below the total_count>=10 threshold — but it's a
    # desperate `git add .`, so has_desperate_command should flag it.
    cmds = [
        Command(
            id=0, timestamp=late_night_start,
            command="git add .", exit_code=0,
            session_id=1, project_id=1, is_legacy=False,
        )
    ]
    s = Session(
        id=1, start_time=late_night_start, end_time=late_night_start + 30,
        duration_seconds=30, project_id=1, commands=cmds,
    )
    db.save_data([p], [s], cmds)

    sessions = detect_late_night_chaotic_sessions(db)
    assert len(sessions) == 1
    assert sessions[0]["session_id"] == 1
    assert "git add ." in sessions[0]["commands"]


def test_detect_late_night_chaotic_sessions_test_bypass(tmp_path):
    """A `pytest --deselect` in a late-night session marks it chaotic."""
    db_file = tmp_path / "test_bypass.db"
    db = Database(str(db_file))
    db.init_db()

    late_night_start = int(datetime(2026, 6, 16, 1, 0, 0).timestamp())
    p = Project(
        id=1, name="Project Gamma", path="~/gamma",
        first_seen=late_night_start, last_seen=late_night_start,
        session_count=1, total_time=1,
    )
    cmds = [
        Command(
            id=0, timestamp=late_night_start,
            command="pytest --deselect tests/test_flaky.py",
            exit_code=0, session_id=1, project_id=1, is_legacy=False,
        )
    ]
    s = Session(
        id=1, start_time=late_night_start, end_time=late_night_start + 30,
        duration_seconds=30, project_id=1, commands=cmds,
    )
    db.save_data([p], [s], cmds)

    sessions = detect_late_night_chaotic_sessions(db)
    assert len(sessions) == 1
    assert "deselect" in sessions[0]["commands"][0].lower()
    
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

