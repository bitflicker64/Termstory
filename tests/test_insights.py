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


def test_calculate_project_necromancer_score():
    from termstory.insights import calculate_project_necromancer_score
    from termstory.formatter import format_necromancer_score
    
    p1 = Project(id=1, name="Project Alpha", path="~/alpha", first_seen=0, last_seen=0, session_count=1, total_time=1)
    
    # Gap of exactly 180 days: 180 * 24 * 3600 = 15552000 seconds
    t1 = 1748870400
    t2 = t1 + 180 * 24 * 3600 + 1000
    
    s1 = Session(id=1, start_time=t1, end_time=t1 + 1000, duration_seconds=1000, project_id=1)
    s2 = Session(id=2, start_time=t2, end_time=t2 + 1000, duration_seconds=1000, project_id=1)
    
    # 180-day gap: should count as resurrection
    info = calculate_project_necromancer_score([s1, s2], [p1])
    assert info["score"] == 1
    assert len(info["resurrections"]) == 1
    assert info["resurrections"][0]["project_name"] == "Project Alpha"
    assert info["resurrections"][0]["gap_days"] == 180
    assert info["resurrections"][0]["last_active"] == s1.end_time
    
    # Verify legacy sessions are ignored
    s1_legacy = Session(id=1, start_time=t1, end_time=t1 + 1000, duration_seconds=1000, project_id=1, is_legacy=True)
    info_legacy = calculate_project_necromancer_score([s1_legacy, s2], [p1])
    assert info_legacy["score"] == 0
    
    # Test formatter
    formatted = format_necromancer_score(info)
    assert "Project Necromancer Score" in formatted
    assert "Project Alpha" in formatted
    assert "180 days" in formatted
    
    # Gap of 179 days: should not count
    t3 = t1 + 179 * 24 * 3600
    s3 = Session(id=3, start_time=t3, end_time=t3 + 1000, duration_seconds=1000, project_id=1)
    info_short = calculate_project_necromancer_score([s1, s3], [p1])
    assert info_short["score"] == 0
    assert len(info_short["resurrections"]) == 0
    
    # Test formatter empty state
    formatted_empty = format_necromancer_score(info_short)
    assert "No projects have been resurrected" in formatted_empty


def test_calculate_rage_quit_signatures():
    from termstory.insights import calculate_rage_quit_signatures
    from termstory.formatter import format_rage_quit_signatures
    
    # Gap of exactly 12 hours: 12 * 3600 = 43200 seconds
    t1 = 1748870400
    t2 = t1 + 12 * 3600 + 1000
    
    s1 = Session(id=1, start_time=t1, end_time=t1 + 1000, duration_seconds=1000, project_id=1, commands=[
        Command(timestamp=t1 + 500, command="git commit -m 'wip'"),
        Command(timestamp=t1 + 1000, command="make build", exit_code=1)
    ])
    s2 = Session(id=2, start_time=t2, end_time=t2 + 1000, duration_seconds=1000, project_id=1)
    
    info = calculate_rage_quit_signatures([s1, s2])
    assert info["total_events"] == 1
    assert len(info["signatures"]) == 1
    assert info["signatures"][0]["command"] == "make build"
    assert info["signatures"][0]["count"] == 1
    
    # Verify legacy sessions are ignored
    s1_legacy = Session(id=1, start_time=t1, end_time=t1 + 1000, duration_seconds=1000, project_id=1, commands=[
        Command(timestamp=t1 + 500, command="git commit -m 'wip'"),
        Command(timestamp=t1 + 1000, command="make build", exit_code=1)
    ], is_legacy=True)
    info_legacy = calculate_rage_quit_signatures([s1_legacy, s2])
    assert info_legacy["total_events"] == 0
    
    # Test formatter
    formatted = format_rage_quit_signatures(info)
    assert "Rage-Quit Signatures" in formatted
    assert "make build" in formatted
    assert "FAIL (1)" in formatted
    
    # Gap of 11 hours: should not count
    t3 = t1 + 11 * 3600
    s2_short = Session(id=2, start_time=t3, end_time=t3 + 1000, duration_seconds=1000, project_id=1)
    info_short = calculate_rage_quit_signatures([s1, s2_short])
    assert info_short["total_events"] == 0
    assert len(info_short["signatures"]) == 0
    
    # Test formatter empty state
    formatted_empty = format_rage_quit_signatures(info_short)
    assert "No rage-quit events detected." in formatted_empty




