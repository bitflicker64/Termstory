import time
from termstory.models import Session, Project, Command
from termstory.insights import (
    calculate_time_distribution,
    calculate_time_of_day_distribution,
    calculate_day_distribution,
    calculate_focus_score,
    detect_patterns_and_anomalies
)

def test_insights_calculations():
    # Use a fixed noon epoch timestamp to ensure adding 2 hours doesn't cross midnight in any timezone
    now = 1748870400
    
    # Create projects
    p1 = Project(id=1, name="Project Alpha", path="~/alpha", first_seen=0, last_seen=0, session_count=1, total_time=1)
    p2 = Project(id=2, name="Project Beta", path="~/beta", first_seen=0, last_seen=0, session_count=1, total_time=1)
    
    # Create sessions
    # Monday starts
    s1 = Session(id=1, start_time=now, end_time=now + 3600, duration_seconds=3600, project_id=1, commands=[
        Command(timestamp=now, command="git commit -m 'feat: first commit'")
    ]) # 1 hour
    
    s2 = Session(id=2, start_time=now + 7200, end_time=now + 9000, duration_seconds=1800, project_id=2, commands=[
        Command(timestamp=now+7200, command="docker run nginx")
    ]) # 30 mins
    
    # Test Time Distribution
    dist = calculate_time_distribution([s1, s2], [p1, p2])
    assert len(dist) == 2
    assert dist[0][0] == "Project Alpha"
    assert dist[0][1] == 66.66666666666666  # 3600 / 5400 * 100
    assert dist[0][2] == 3600
    
    # Test Time of Day (depends on local timezone, so we can mock/assert categorization)
    # Check that it returns counts matching total time
    tod = calculate_time_of_day_distribution([s1, s2])
    assert sum(tod.values()) == 5400
    
    # Test Day of Week
    day_dist = calculate_day_distribution([s1, s2])
    assert sum(day_dist.values()) == 5400
    
    # Test Focus Score
    # 2 sessions, 2 unique projects on 1 day. 
    # Mins active = 90 mins. Mins per session = 45 mins.
    # Switches = 2 unique projects - 1 = 1 switch.
    # Penalty = 1 * 1.5 = 1.5.
    # Bonus = 45 / 20 = 2.25.
    # Score = 6.0 - 1.5 + 2.25 = 6.75 -> 6.8
    score = calculate_focus_score([s1, s2])
    assert score == 6.8
    
    # Test Patterns and Anomalies
    patterns = detect_patterns_and_anomalies([s1, s2], [p1, p2])
    assert len(patterns) > 0
    assert any("Project Alpha" in p for p in patterns)
    assert any("git" in p.lower() for p in patterns)


def test_analyze_all(tmp_path, monkeypatch):
    monkeypatch.setenv("TERMSTORY_DATE_OVERRIDE", "2026-06-01 12:00:00")
    
    db_file = tmp_path / "test_insights.db"
    from termstory.database import Database
    db = Database(str(db_file))
    db.init_db()
    
    from termstory.models import Project, Session, Command
    from datetime import datetime
    
    # 2026-06-01 12:00:00 is Monday
    now = int(datetime(2026, 6, 1, 12, 0, 0).timestamp())
    
    p1 = Project(id=1, name="Project Alpha", path="~/alpha", first_seen=now, last_seen=now, session_count=1, total_time=3600)
    p2 = Project(id=2, name="Project Beta", path="~/beta", first_seen=now, last_seen=now, session_count=1, total_time=1800)
    
    c1 = Command(timestamp=now, command="git commit -m 'feat: first commit'", session_id=1, project_id=1, is_legacy=False)
    s1 = Session(id=1, start_time=now, end_time=now + 3600, duration_seconds=3600, project_id=1, commands=[c1], is_legacy=False)
    
    c2 = Command(timestamp=now + 7200, command="docker run nginx", session_id=2, project_id=2, is_legacy=False)
    s2 = Session(id=2, start_time=now + 7200, end_time=now + 9000, duration_seconds=1800, project_id=2, commands=[c2], is_legacy=False)
    
    db.save_data([p1, p2], [s1, s2], [c1, c2])
    
    from termstory.insights import analyze_all
    stats = analyze_all(db)
    
    assert stats["total_sessions"] == 2
    assert stats["total_commands"] == 2
    assert stats["total_projects"] == 2
    assert stats["streak"] == 1
    assert stats["most_active_day"] == "Monday"
    assert stats["most_active_time"] == "afternoon"
    assert len(stats["most_used_projects"]) == 2
    assert stats["most_used_projects"][0][0] == "Project Alpha"
    assert stats["most_used_projects"][0][1] == 3600
    assert stats["most_used_projects"][1][0] == "Project Beta"
    assert stats["most_used_projects"][1][1] == 1800
    assert stats["vampire_index"] == 0.0
    assert stats["rpg_class"] == "Docker Demolitionist"


def test_vampire_coder_index():
    from termstory.insights import calculate_vampire_coder_index, get_vampire_metrics
    from datetime import datetime
    
    # 2 AM timestamp (vampire time)
    ts_vampire = int(datetime(2026, 6, 1, 2, 30, 0).timestamp())
    # 2 PM timestamp (non-vampire time)
    ts_day = int(datetime(2026, 6, 1, 14, 30, 0).timestamp())
    
    s = Session(
        id=1,
        start_time=ts_vampire,
        end_time=ts_vampire + 100,
        duration_seconds=100,
        project_id=1,
        commands=[
            Command(timestamp=ts_vampire, command="git status"),
            Command(timestamp=ts_day, command="git push")
        ],
        commits=[
            {"hash": "h1", "timestamp": ts_vampire, "message": "fix: bug"}
        ]
    )
    
    # 2 vampire events (status, commit), 1 day event (push). Total events = 3.
    # Vampire Index = 2 / 3 * 100 = 66.666... -> 66.7%
    index = calculate_vampire_coder_index([s])
    assert index == 66.7
    
    metrics = get_vampire_metrics([s])
    assert metrics["vampire_index"] == 66.7
    assert metrics["vampire_commands"] == 1
    assert metrics["total_commands"] == 2
    assert metrics["vampire_commits"] == 1
    assert metrics["total_commits"] == 1


def test_assign_rpg_class():
    from termstory.insights import assign_rpg_class
    
    # Test Regex Sorcerer
    s1 = Session(
        id=1, start_time=0, end_time=100, duration_seconds=100, project_id=1,
        commands=[
            Command(timestamp=0, command="cat file.txt | grep pattern"),
            Command(timestamp=10, command="awk '{print $1}'"),
            Command(timestamp=20, command="git status") # Git Paladin
        ]
    )
    r1 = assign_rpg_class([s1])
    assert r1["class_name"] == "Regex Sorcerer"
    assert r1["counts"]["Regex Sorcerer"] == 2
    
    # Test Docker Demolitionist
    s2 = Session(
        id=2, start_time=0, end_time=100, duration_seconds=100, project_id=1,
        commands=[
            Command(timestamp=0, command="docker ps"),
            Command(timestamp=10, command="docker-compose up -d")
        ]
    )
    r2 = assign_rpg_class([s2])
    assert r2["class_name"] == "Docker Demolitionist"



