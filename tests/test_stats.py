import os
import time
from datetime import datetime, timedelta
import pytest
from unittest.mock import patch

from termstory.database import Database
from termstory.models import Command, Session, Project
from termstory.stats import daily_activity_heatmap, project_breakdown, language_detection, peak_hours, detect_project_language_from_files, _LANG_CACHE
from termstory.formatter import format_stats_output

@pytest.fixture
def temp_db(tmp_path):
    db_file = tmp_path / "test_stats.db"
    db = Database(str(db_file))
    db.init_db()
    return db

def test_daily_activity_heatmap(temp_db):
    # Setup commands at different days
    # Let's override the current time to 2026-06-14 for deterministic testing
    now_ts = int(datetime(2026, 6, 14, 12, 0, 0).timestamp())
    
    # Yesterday: 1 command (should show '▄')
    yesterday_ts = now_ts - 24 * 3600
    cmd1 = Command(timestamp=yesterday_ts, command="git commit", exit_code=0, session_id=1, project_id=1)
    
    # Today: 25 commands (should show '█')
    today_cmds = []
    for i in range(25):
        today_cmds.append(Command(timestamp=now_ts + i, command=f"python test.py {i}", exit_code=0, session_id=2, project_id=1))
        
    project = Project(id=1, name="ProjA", path="~/proj-a", first_seen=yesterday_ts, last_seen=now_ts, session_count=2, total_time=110)
    session1 = Session(id=1, start_time=yesterday_ts, end_time=yesterday_ts + 10, duration_seconds=10, project_id=1, commands=[cmd1])
    session2 = Session(id=2, start_time=now_ts, end_time=now_ts + 100, duration_seconds=100, project_id=1, commands=today_cmds)
    
    all_cmds = [cmd1] + today_cmds
    temp_db.save_data([project], [session1, session2], all_cmds)
    
    with patch("termstory.stats.get_current_time", return_value=datetime(2026, 6, 14, 12, 0, 0)):
        # Test colored heatmap
        colored_heatmap = daily_activity_heatmap(temp_db, days_limit=3, colored=True)
        # 3 days limit: day before yesterday (0 cmds -> ░), yesterday (1 cmd -> ▄), today (25 cmds -> █)
        assert "[grey37]░[/]" in colored_heatmap
        assert "[green]▄[/]" in colored_heatmap
        assert "[bold reverse green]█[/]" in colored_heatmap
        
        # Test uncolored heatmap
        uncolored_heatmap = daily_activity_heatmap(temp_db, days_limit=3, colored=False)
        assert uncolored_heatmap == "░ ▄ █"

def test_project_breakdown(temp_db):
    now_ts = int(time.time())
    
    # Project 1: Named "General / No Project" -> should map to "Other"
    p1 = Project(id=1, name="General / No Project", path=None, first_seen=now_ts, last_seen=now_ts, session_count=1, total_time=60)
    cmd1 = Command(timestamp=now_ts, command="ls", exit_code=0, session_id=1, project_id=1)
    s1 = Session(id=1, start_time=now_ts, end_time=now_ts + 60, duration_seconds=60, project_id=1, commands=[cmd1])
    
    # Project 2: Named "TermStory"
    p2 = Project(id=2, name="TermStory", path="~/termstory", first_seen=now_ts + 100, last_seen=now_ts + 200, session_count=1, total_time=100)
    cmd2 = Command(timestamp=now_ts + 100, command="git diff", exit_code=0, session_id=2, project_id=2)
    s2 = Session(id=2, start_time=now_ts + 100, end_time=now_ts + 200, duration_seconds=100, project_id=2, commands=[cmd2])
    
    temp_db.save_data([p1, p2], [s1, s2], [cmd1, cmd2])
    
    breakdown = project_breakdown(temp_db)
    
    assert "General / No Project" not in breakdown
    assert "Other" in breakdown
    assert "TermStory" in breakdown
    
    # Check Other stats
    assert breakdown["Other"]["commands_count"] == 1
    assert breakdown["Other"]["total_duration"] == 60
    assert breakdown["Other"]["sessions_count"] == 1
    
    # Check TermStory stats
    assert breakdown["TermStory"]["commands_count"] == 1
    assert breakdown["TermStory"]["total_duration"] == 100
    assert breakdown["TermStory"]["sessions_count"] == 1
    assert breakdown["TermStory"]["path"] == "~/termstory"

def test_language_detection(temp_db, tmp_path):
    # Create temp project path on disk with Cargo.toml
    proj_path = tmp_path / "my-rust-project"
    proj_path.mkdir()
    (proj_path / "Cargo.toml").write_text("[package]")
    
    now_ts = int(time.time())
    p1 = Project(id=1, name="RustProj", path=str(proj_path), first_seen=now_ts, last_seen=now_ts, session_count=1, total_time=0)
    cmd1 = Command(timestamp=now_ts, command="cargo build", exit_code=0, session_id=1, project_id=1)
    s1 = Session(id=1, start_time=now_ts, end_time=now_ts, duration_seconds=0, project_id=1, commands=[cmd1])
    
    # Project 2: Fallback to command-based classification
    p2 = Project(id=2, name="PythonProj", path=None, first_seen=now_ts, last_seen=now_ts, session_count=1, total_time=0)
    cmd2 = Command(timestamp=now_ts, command="python manage.py runserver", exit_code=0, session_id=2, project_id=2)
    s2 = Session(id=2, start_time=now_ts, end_time=now_ts, duration_seconds=0, project_id=2, commands=[cmd2])
    
    temp_db.save_data([p1, p2], [s1, s2], [cmd1, cmd2])
    
    langs = language_detection(temp_db)
    
    assert langs["Rust"] == 50.0
    assert langs["Python"] == 50.0

def test_peak_hours(temp_db):
    # Insert commands at specific hours
    # Hour 14:00 (2 PM) local time
    # Hour 9:00 (9 AM) local time
    dt1 = datetime(2026, 6, 14, 14, 30, 0)
    dt2 = datetime(2026, 6, 14, 9, 15, 0)
    dt3 = datetime(2026, 6, 14, 14, 45, 0)
    
    cmd1 = Command(timestamp=int(dt1.timestamp()), command="git diff", exit_code=0, session_id=1, project_id=1)
    cmd2 = Command(timestamp=int(dt2.timestamp()), command="pytest", exit_code=0, session_id=1, project_id=1)
    cmd3 = Command(timestamp=int(dt3.timestamp()), command="git commit", exit_code=0, session_id=1, project_id=1)
    
    p = Project(id=1, name="Proj", path="~/proj", first_seen=int(dt2.timestamp()), last_seen=int(dt1.timestamp()), session_count=1, total_time=100)
    s = Session(id=1, start_time=int(dt2.timestamp()), end_time=int(dt1.timestamp()), duration_seconds=100, project_id=1, commands=[cmd1, cmd2, cmd3])
    
    temp_db.save_data([p], [s], [cmd1, cmd2, cmd3])
    
    hourly = peak_hours(temp_db)
    
    assert hourly[14] == 2
    assert hourly[9] == 1
    assert hourly[0] == 0

def test_format_stats_output(temp_db):
    now_ts = int(time.time())
    p = Project(id=1, name="TermStory", path="~/termstory", first_seen=now_ts, last_seen=now_ts, session_count=1, total_time=60)
    cmd = Command(timestamp=now_ts, command="python -m pytest", exit_code=0, session_id=1, project_id=1)
    s = Session(id=1, start_time=now_ts, end_time=now_ts + 60, duration_seconds=60, project_id=1, commands=[cmd])
    
    temp_db.save_data([p], [s], [cmd])
    
    output = format_stats_output(temp_db)
    
    assert "Deep History Statistics & Telemetry" in output
    assert "Activity Heatmap" in output
    assert "Peak Hours" in output
    assert "Language Distribution" in output
    assert "Project Breakdown" in output
    assert "TermStory" in output
    assert "Python" in output


@pytest.fixture(autouse=True)
def clear_cache():
    _LANG_CACHE.clear()


def test_detect_language_with_tilde_expansion(tmp_path, monkeypatch):
    monkeypatch.setattr("os.path.expanduser", lambda path: str(tmp_path / path[2:]) if path.startswith("~/") else path)
    proj_dir = tmp_path / "Projects" / "my-python-project"
    proj_dir.mkdir(parents=True)
    (proj_dir / "pyproject.toml").touch()
    result = detect_project_language_from_files("~/Projects/my-python-project")
    assert result == "Python"
    assert str(proj_dir) in _LANG_CACHE


def test_detect_language_absolute_path(tmp_path):
    proj_dir = tmp_path / "node-project"
    proj_dir.mkdir(parents=True)
    (proj_dir / "package.json").touch()
    result = detect_project_language_from_files(str(proj_dir))
    assert result == "JavaScript/TypeScript"


def test_detect_language_nonexistent_path():
    assert detect_project_language_from_files("/nonexistent/path/xyz") is None


def test_detect_language_cache_hit(tmp_path):
    proj_dir = tmp_path / "rust-project"
    proj_dir.mkdir(parents=True)
    (proj_dir / "Cargo.toml").touch()
    assert detect_project_language_from_files(str(proj_dir)) == "Rust"
    (proj_dir / "Cargo.toml").unlink()
    assert detect_project_language_from_files(str(proj_dir)) == "Rust"


def test_detect_language_network_mount_blacklist(monkeypatch):
    monkeypatch.setattr("os.path.isdir", lambda path: True)
    monkeypatch.setattr("os.path.expanduser", lambda path: path)
    assert detect_project_language_from_files("/mnt/stale_nfs/project") is None
    assert detect_project_language_from_files("/Volumes/smb/share") is None
    assert detect_project_language_from_files("\\\\Server\\Share\\project") is None


def test_detect_language_csharp_proj(tmp_path):
    proj_dir = tmp_path / "csharp-project"
    proj_dir.mkdir(parents=True)
    (proj_dir / "myapp.csproj").touch()
    assert detect_project_language_from_files(str(proj_dir)) == "C#"


def test_detect_language_csharp_sln(tmp_path):
    proj_dir = tmp_path / "csharp-sln"
    proj_dir.mkdir(parents=True)
    (proj_dir / "myapp.sln").touch()
    assert detect_project_language_from_files(str(proj_dir)) == "C#"


def test_detect_language_makefile(tmp_path):
    proj_dir = tmp_path / "c-project"
    proj_dir.mkdir(parents=True)
    (proj_dir / "Makefile").touch()
    assert detect_project_language_from_files(str(proj_dir)) == "C/C++"


def test_detect_language_empty_path():
    assert detect_project_language_from_files("") is None
    assert detect_project_language_from_files(None) is None


def test_detect_language_multiple_config_files(tmp_path):
    proj_dir = tmp_path / "multi-project"
    proj_dir.mkdir(parents=True)
    (proj_dir / "Cargo.toml").touch()
    (proj_dir / "package.json").touch()
    assert detect_project_language_from_files(str(proj_dir)) == "Rust"


def test_detect_language_java_gradle(tmp_path):
    proj_dir = tmp_path / "java-project"
    proj_dir.mkdir(parents=True)
    (proj_dir / "build.gradle").touch()
    assert detect_project_language_from_files(str(proj_dir)) == "Java/Kotlin"


def test_detect_language_php_composer(tmp_path):
    proj_dir = tmp_path / "php-project"
    proj_dir.mkdir(parents=True)
    (proj_dir / "composer.json").touch()
    assert detect_project_language_from_files(str(proj_dir)) == "PHP"


def test_detect_language_ruby_gemfile(tmp_path):
    proj_dir = tmp_path / "ruby-project"
    proj_dir.mkdir(parents=True)
    (proj_dir / "Gemfile").touch()
    assert detect_project_language_from_files(str(proj_dir)) == "Ruby"
