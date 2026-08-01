import json
import urllib.request
from datetime import datetime
from typer.testing import CliRunner
import pytest

from termstory.cli import app, get_ai_provider_settings, _is_recompile_command, _count_rapid_recompile_clusters
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


# ─── Issue #37: signal detection (kill -9 + rapid recompile) ──────────────────


def test_is_recompile_command_positive():
    """Issue #37: build/compile invocations are detected as recompile candidates."""
    for cmd in [
        "make", "make -j8", "cargo build --release", "go build ./...",
        "tsc --noEmit", "ninja -C build", "webpack --mode production",
        "g++ main.cpp", "swift build", "mvn compile", "dotnet build",
    ]:
        assert _is_recompile_command(cmd), f"expected {cmd!r} to be a recompile"


def test_is_recompile_command_no_false_positives():
    """Unrelated commands must not be flagged as recompile invocations."""
    for cmd in [
        "makeup", "cmake-format", "ls", "echo hello", "git commit",
        "made-up-command", "tail -f log", "kubectl get pods",
        "pytest tests/", "rm -rf build", "makedepend",  # makedepend contains 'make' but is a different tool
    ]:
        assert not _is_recompile_command(cmd), f"expected {cmd!r} NOT to be a recompile"


def test_count_rapid_recompile_clusters_single_cluster():
    """3 build invocations within 60s = 1 cluster."""
    ts = [0, 10, 20]
    cmds = ["make", "make", "make"]
    assert _count_rapid_recompile_clusters(cmds, ts) == 1


def test_count_rapid_recompile_clusters_no_cluster_when_below_threshold():
    """Only 2 build invocations in the window = 0 clusters."""
    ts = [0, 10]
    cmds = ["make", "make"]
    assert _count_rapid_recompile_clusters(cmds, ts) == 0


def test_count_rapid_recompile_clusters_two_clusters():
    """6 builds split into two 60s windows = 2 clusters."""
    ts = [0, 10, 20, 100, 110, 120]
    cmds = ["make"] * 6
    assert _count_rapid_recompile_clusters(cmds, ts) == 2


def test_count_rapid_recompile_clusters_mixed_builders():
    """Mixed build tools (make + cargo + tsc) within 60s count as one cluster."""
    ts = [0, 5, 15]
    cmds = ["make", "cargo build", "tsc"]
    assert _count_rapid_recompile_clusters(cmds, ts) == 1


def test_count_rapid_recompile_clusters_inclusive_60s_boundary():
    """Exactly 60s apart still counts as a cluster (<=); 61s does not."""
    assert _count_rapid_recompile_clusters(["make"] * 3, [0, 30, 60]) == 1
    assert _count_rapid_recompile_clusters(["make"] * 3, [0, 30, 61]) == 0


def test_count_rapid_recompile_clusters_handles_mismatched_lengths():
    """Defensive: mismatched command/timestamp lengths return 0 (no crash)."""
    assert _count_rapid_recompile_clusters(["make", "make"], [0, 1, 2]) == 0
    assert _count_rapid_recompile_clusters([], []) == 0


def test_ai_translate_git_anger_includes_kill_signal_in_prompt(monkeypatch):
    """The LLM prompt must include the kill -9 signal count when present."""
    captured = []

    def mock_urlopen(req, timeout=None):
        # The Request object's data attribute holds the JSON body sent to the LLM.
        body = req.data.decode("utf-8") if isinstance(req.data, bytes) else req.data
        captured.append(body)
        return _mock_response("💀 roasted")

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

    commit_data = [
        {
            "hash": "deadbeef12",
            "message": "feat: stop runaway process",
            "preceding_errors": ["kill -9 12345", "kill -9 67890"],
            "kill_count": 2,
            "recompile_clusters": 0,
        }
    ]
    translate_git_anger(
        commit_data,
        api_key="test-key",
        api_base_url="https://api.openai.com/v1",
        model_name="gpt-4o",
        provider="openai",
    )
    assert len(captured) == 1
    payload = captured[0]
    # The kill signal summary should be in the rendered prompt body
    assert "kill -9" in payload or "SIGKILL" in payload
    assert "2x" in payload
    assert "Aggression Signals" in payload


def test_ai_translate_git_anger_includes_recompile_signal_in_prompt(monkeypatch):
    """The LLM prompt must include the rapid-recompile cluster count when present."""
    captured = []

    def mock_urlopen(req, timeout=None):
        body = req.data.decode("utf-8") if isinstance(req.data, bytes) else req.data
        captured.append(body)
        return _mock_response("recompile")

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

    commit_data = [
        {
            "hash": "feedface12",
            "message": "feat: build keeps breaking",
            "preceding_errors": [],
            "kill_count": 0,
            "recompile_clusters": 2,
        }
    ]
    translate_git_anger(
        commit_data,
        api_key="test-key",
        api_base_url="https://api.openai.com/v1",
        model_name="gpt-4o",
        provider="openai",
    )
    assert len(captured) == 1
    payload = captured[0]
    assert "rapid-recompile" in payload
    assert "2" in payload


def _mock_response(content: str):
    """Helper to build a MockResponse with the given LLM content."""
    payload = json.dumps({"choices": [{"message": {"content": content}}]}).encode("utf-8")
    return MockResponse(payload)


def test_format_anger_heuristics_kill9_dominant():
    """Issue #37: 2+ kill -9 commands in window → FURY / PROCESS WHACK-A-MOLE branch.

    Note: the formatter's commit-message override (matches 'fix'/'bug'/'crash'/'issue')
    always wins, so we use a non-fix commit message to keep the kill branch visible.
    """
    commit_data = [
        {
            "hash": "killed123",
            "message": "feat: stop runaway process",
            "preceding_errors": ["kill -9 111", "kill -9 222", "kill -9 333"],
            "kill_count": 3,
            "recompile_clusters": 0,
        }
    ]
    out = format_anger_translation_heuristics(commit_data)
    assert "FURY" in out
    assert "PROCESS WHACK-A-MOLE" in out
    assert "kill -9 x3" in out


def test_format_anger_heuristics_recompile_only():
    """Issue #37: rapid-recompile cluster with no kill → RECOMPILING IN A PANIC branch."""
    commit_data = [
        {
            "hash": "recomp456",
            "message": "feat: add widget",
            "preceding_errors": [],
            "kill_count": 0,
            "recompile_clusters": 2,
        }
    ]
    out = format_anger_translation_heuristics(commit_data)
    assert "RECOMPILING IN A PANIC" in out
    assert "recompile-clusters x2" in out


def test_format_anger_heuristics_both_signals():
    """Issue #37: 1+ kill AND 1+ recompile cluster → BUILD BROKE & PROCESSES DIED branch.

    Note: the formatter's commit-message override (matches 'fix'/'bug'/'crash'/'issue')
    always wins, so we use a non-fix commit message to keep the both-signals branch visible.
    """
    commit_data = [
        {
            "hash": "both789",
            "message": "wip: chaos in build pipeline",
            "preceding_errors": ["kill -9 1"],
            "kill_count": 1,
            "recompile_clusters": 1,
        }
    ]
    out = format_anger_translation_heuristics(commit_data)
    assert "BUILD BROKE" in out
    assert "PROCESSES DIED" in out
    assert "kill -9 x1" in out
    assert "recompile-clusters x1" in out


def test_format_anger_heuristics_falls_back_to_error_count():
    """When no aggression signals fire, the existing error-count behavior is preserved.

    Note: the formatter's commit-message override (matches 'fix'/'bug'/'crash'/'issue')
    always wins, so we use a non-fix commit message to keep the error-count branch visible.
    """
    commit_data = [
        {
            "hash": "plain123",
            "message": "chore: refactor utils",
            "preceding_errors": ["pytest", "python run.py", "pytest", "python run.py"],
            "kill_count": 0,
            "recompile_clusters": 0,
        }
    ]
    out = format_anger_translation_heuristics(commit_data)
    # 4 fails with no kill / recompile should still hit the original RAGE & DESPAIR branch
    assert "RAGE" in out or "FRUSTRATION" in out
    # The aggression-signal badge should be empty in this case
    assert "kill -9" not in out
    assert "recompile-clusters" not in out


def test_format_anger_heuristics_missing_signal_keys_default_to_zero():
    """Backwards-compat: older callers passing only preceding_errors still work."""
    commit_data = [
        {"hash": "old1", "message": "feat: x", "preceding_errors": []},
    ]
    out = format_anger_translation_heuristics(commit_data)
    assert "TRIUMPH" in out or "SMOOTH" in out

