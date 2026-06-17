from termstory.models import Command, Session
from termstory.session import create_sessions

def test_create_sessions_empty():
    assert create_sessions([]) == []

def test_create_sessions_single_session():
    # Commands executed close together (within 30 mins)
    commands = [
        Command(timestamp=1000, command="git status"),
        Command(timestamp=1050, command="git diff"),
        Command(timestamp=1100, command="git commit"),
    ]
    
    sessions = create_sessions(commands)
    assert len(sessions) == 1
    session = sessions[0]
    assert session.id == 1
    assert session.start_time == 1000
    assert session.end_time == 1100
    assert session.duration_seconds == 100
    assert len(session.commands) == 3
    assert all(c.session_id == 1 for c in session.commands)

def test_create_sessions_multiple():
    # Commands with gaps > 30 mins (1800 seconds)
    commands = [
        Command(timestamp=1000, command="git status"),
        # Gap of 1801 seconds
        Command(timestamp=2801, command="docker ps"),
        Command(timestamp=2900, command="docker compose up"),
        # Gap of 2000 seconds
        Command(timestamp=4900, command="mvn clean test"),
    ]
    
    sessions = create_sessions(commands)
    assert len(sessions) == 3
    
    # Session 1
    assert sessions[0].id == 1
    assert sessions[0].start_time == 1000
    assert sessions[0].end_time == 1000
    assert sessions[0].duration_seconds == 0
    assert len(sessions[0].commands) == 1
    
    # Session 2
    assert sessions[1].id == 2
    assert sessions[1].start_time == 2801
    assert sessions[1].end_time == 2900
    assert sessions[1].duration_seconds == 99
    assert len(sessions[1].commands) == 2
    
    # Session 3
    assert sessions[2].id == 3
    assert sessions[2].start_time == 4900
    assert sessions[2].end_time == 4900
    assert sessions[2].duration_seconds == 0
    assert len(sessions[2].commands) == 1

def test_create_sessions_no_cwd_fragmentation():
    commands = [
        # In project A
        Command(timestamp=1000, command="cd ~/projects/projectA"),
        Command(timestamp=1010, command="git status"),
        # Go to home
        Command(timestamp=1020, command="cd ~"),
        Command(timestamp=1030, command="ls"),
        # Go to project B
        Command(timestamp=1040, command="cd ~/projects/projectB"),
        Command(timestamp=1050, command="git log"),
    ]
    
    sessions = create_sessions(commands)
    
    # We expect 1 session because there are no time gaps > 1800s
    assert len(sessions) == 1
    
    assert sessions[0].id == 1
    assert len(sessions[0].commands) == 6
