import os
import json
import time
import pytest
from typer.testing import CliRunner

from termstory.cli import app
from termstory.database import Database
from termstory.models import Project, Session, Command
from termstory.reminder import (
    cluster_commands,
    generate_cluster_summary,
    consolidate_sleep_contexts
)

def test_cluster_commands_fallback(monkeypatch):
    # Try importing termstory.rag and monkeypatching SENTENCE_TRANSFORMERS_AVAILABLE to False
    import termstory.rag
    monkeypatch.setattr(termstory.rag, "SENTENCE_TRANSFORMERS_AVAILABLE", False)
    
    cmds = [
        "git add .",
        "git commit -m 'feat'",
        "docker build -t app .",
        "docker run app",
        "git status"
    ]
    
    clusters = cluster_commands(cmds)
    # Fallback should group by first word
    # verbs: git (3), docker (2)
    assert len(clusters) == 2
    git_cluster = [c for c in clusters if c[0].startswith("git")]
    docker_cluster = [c for c in clusters if c[0].startswith("docker")]
    assert len(git_cluster[0]) == 3
    assert len(docker_cluster[0]) == 2


def test_cluster_commands_with_embeddings(monkeypatch):
    import termstory.rag
    monkeypatch.setattr(termstory.rag, "SENTENCE_TRANSFORMERS_AVAILABLE", True)
    
    # Mock get_embeddings to return custom vectors where similar commands have same vector
    def mock_get_embeddings(texts, **kwargs):
        embs = []
        for text in texts:
            if "docker" in text:
                embs.append([1.0, 0.0, 0.0])
            elif "git" in text:
                embs.append([0.0, 1.0, 0.0])
            else:
                embs.append([0.0, 0.0, 1.0])
        return embs
        
    monkeypatch.setattr(termstory.rag, "get_embeddings", mock_get_embeddings)
    
    cmds = [
        "docker build",
        "git commit",
        "docker run",
        "git push"
    ]
    
    clusters = cluster_commands(cmds)
    assert len(clusters) == 2
    # Verify similar commands grouped together
    for c in clusters:
        if "docker" in c[0]:
            assert "docker build" in c and "docker run" in c
        if "git" in c[0]:
            assert "git commit" in c and "git push" in c


def test_consolidate_sleep_contexts(tmp_path, monkeypatch):
    db_file = tmp_path / "test_sleep.db"
    db = Database(str(db_file))
    db.init_db()
    
    # Setup some commands with gaps
    now = int(time.time())
    
    # Chunk 1: completed and followed by 30+ min gap
    # Commands at: now - 5000, now - 4900, now - 4800
    # Gap from now - 4800 to Chunk 2 at now - 1000 is 3800s (>=1800s)
    cmds = [
        Command(timestamp=now - 5000, command="git diff", session_id=1, project_id=1),
        Command(timestamp=now - 4900, command="git commit", session_id=1, project_id=1),
        Command(timestamp=now - 4800, command="git push", session_id=1, project_id=1),
        
        # Chunk 2: current active chunk (ends at now - 100s, gap to now is 100s, i.e. <1800s)
        Command(timestamp=now - 1000, command="docker ps", session_id=2, project_id=1),
        Command(timestamp=now - 900, command="docker logs", session_id=2, project_id=1),
    ]
    
    # Save projects & sessions & commands
    p = Project(id=1, name="test_project", path="~/test", first_seen=now-5000, last_seen=now, session_count=2, total_time=500)
    s1 = Session(id=1, start_time=now-5000, end_time=now-4800, duration_seconds=200, project_id=1, commands=cmds[:3])
    s2 = Session(id=2, start_time=now-1000, end_time=now-900, duration_seconds=100, project_id=1, commands=cmds[3:])
    db.save_data([p], [s1, s2], cmds)
    
    # Mock AI provider as disabled so it uses fallback summary
    monkeypatch.setattr("termstory.config.load_config", lambda: {"provider": "disabled"})
    
    # 1. Test consolidation with force=False (normal daemon run)
    # Should only consolidate Chunk 1 because Chunk 2 is too recent
    count = consolidate_sleep_contexts(db, force=False)
    assert count == 1
    
    contexts = db.get_consolidated_contexts()
    assert len(contexts) == 1
    assert contexts[0]["start_time"] == now - 5000
    assert contexts[0]["end_time"] == now - 4800
    assert "git" in contexts[0]["summary"]
    
    # 2. Test consolidation again with force=False. Should create 0 new contexts
    count = consolidate_sleep_contexts(db, force=False)
    assert count == 0
    
    # 3. Test consolidation with force=True (manual run). Should consolidate Chunk 2
    count = consolidate_sleep_contexts(db, force=True)
    assert count == 1
    
    contexts = db.get_consolidated_contexts()
    assert len(contexts) == 2
    # The newest consolidated context (ordered by start_time DESC) should be Chunk 2
    assert contexts[0]["start_time"] == now - 1000
    assert contexts[0]["end_time"] == now - 900
    assert "docker" in contexts[0]["summary"]


def test_cli_sleep_command(tmp_path, monkeypatch):
    db_file = tmp_path / "test_sleep_cli.db"
    monkeypatch.setattr("termstory.cli.get_db_path", lambda: str(db_file))
    monkeypatch.setattr("termstory.cli.get_history_files", lambda: [])
    monkeypatch.setattr("termstory.config.load_config", lambda: {"provider": "disabled"})
    
    # Mock start_sleep_daemon to prevent spawning background process in tests
    monkeypatch.setattr("termstory.reminder.start_sleep_daemon", lambda *args, **kwargs: None)
    
    db = Database(str(db_file))
    db.init_db()
    
    # Add dummy command in DB
    now = int(time.time())
    p = Project(id=1, name="test_proj", path="~/test", first_seen=now-100, last_seen=now, session_count=1, total_time=100)
    cmd = Command(timestamp=now - 50, command="git diff", session_id=1, project_id=1)
    s = Session(id=1, start_time=now-100, end_time=now, duration_seconds=100, project_id=1, commands=[cmd])
    db.save_data([p], [s], [cmd])
    
    runner = CliRunner()
    
    # 1. Test empty show
    result = runner.invoke(app, ["sleep", "--show"])
    assert result.exit_code == 0
    assert "No consolidated contexts found" in result.stdout
    
    # 2. Test consolidate manual trigger
    result = runner.invoke(app, ["sleep", "--consolidate"])
    assert result.exit_code == 0
    assert "Consolidation complete" in result.stdout
    assert "Created 1 new consolidated contexts" in result.stdout
    
    # 3. Test show after consolidation
    result = runner.invoke(app, ["sleep", "--show"])
    assert result.exit_code == 0
    assert "REM Sleep Consolidated Contexts" in result.stdout
    assert "git" in result.stdout
