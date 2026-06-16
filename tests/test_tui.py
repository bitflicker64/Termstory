import os
import time
import tempfile
import pytest
from datetime import datetime, timedelta

from termstory.database import Database
from termstory.models import Session, Project, Command
from termstory.tui import (
    TermStoryWorkspace,
    calculate_streak,
    generate_heatmap,
    calculate_dashboard_stats,
    get_session_memory_str,
    deduplicate_sessions,
    clean_command_to_memory,
    strip_ansi,
    OnboardingScreen,
)

def test_calculate_streak(monkeypatch):
    now = datetime(2026, 6, 2, 12, 0)
    monkeypatch.setattr("termstory.tui.get_current_time", lambda: now)
    now_ts = int(now.timestamp())
    
    # 1. Empty sessions
    assert calculate_streak([]) == 0
    
    # 2. Single session today
    s1 = Session(id=1, start_time=now_ts, end_time=now_ts + 600, duration_seconds=600, project_id=1)
    assert calculate_streak([s1]) == 1
    
    # 3. Gap of 3 days (streak broken)
    s2 = Session(id=2, start_time=now_ts - 3 * 86400, end_time=now_ts - 3 * 86400 + 600, duration_seconds=600, project_id=1)
    assert calculate_streak([s1, s2]) == 1
    
    # 4. Continuous streak (today, yesterday, day before)
    s_yesterday = Session(id=3, start_time=now_ts - 86400, end_time=now_ts - 86400 + 600, duration_seconds=600, project_id=1)
    s_prev = Session(id=4, start_time=now_ts - 2 * 86400, end_time=now_ts - 2 * 86400 + 600, duration_seconds=600, project_id=1)
    assert calculate_streak([s1, s_yesterday, s_prev]) == 3

def test_generate_heatmap():
    now = int(datetime.now().timestamp())
    sessions = [
        Session(id=1, start_time=now, end_time=now + 600, duration_seconds=600, project_id=1, commands=[
            Command(timestamp=now, command="git status")
        ])
    ]
    heatmap = generate_heatmap(sessions, days_limit=30)
    assert "█" in heatmap or "■" in heatmap or "▄" in heatmap
    assert "░" in heatmap

def test_get_session_memory_str():
    # 1. Commit priority
    s1 = Session(id=1, start_time=1000, end_time=1600, duration_seconds=600, project_id=1, commits=[
        {"hash": "abc", "message": "feat: commit message", "cleaned_message": "Clean message"}
    ])
    assert get_session_memory_str(s1) == "Clean message"
    
    # 2. Command length fallback
    s2 = Session(id=2, start_time=1000, end_time=1600, duration_seconds=600, project_id=1, commands=[
        Command(timestamp=1000, command="git status"),
        Command(timestamp=1100, command="test")
    ])
    assert get_session_memory_str(s2) == "test"

    # 3. AI summary parsing fallback for Option B
    s3 = Session(id=3, start_time=1000, end_time=1600, duration_seconds=600, project_id=1)
    s3.ai_summary = "[🤖 Codebase Pulse]\n• Hacked: Wired: Memory-first timeline\n• Tooling: git status\n• Outcome: success"
    assert get_session_memory_str(s3) == "Wired: Memory-first timeline"

    # 4. AI summary parsing fallback for Option A
    s4 = Session(id=4, start_time=1000, end_time=1600, duration_seconds=600, project_id=1)
    s4.ai_summary = "[💻 Dev Log]\n├─ 🔨 Built: Wired up Zsh extended format\n├─ 🔧 Flow: pytest\n└─ 🚀 Result: success"
    assert get_session_memory_str(s4) == "Wired up Zsh extended format"

def test_tui_workspace_init():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "test.db")
        db = Database(db_path)
        db.init_db()
        
        app = TermStoryWorkspace(db, days_limit=30, config_override={"has_seen_onboarding": True, "ai_enabled": False})
        assert app.db == db
        assert app.days_limit == 30

@pytest.mark.asyncio
async def test_tui_workspace_mount():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "test.db")
        db = Database(db_path)
        db.init_db()
        
        # Insert a mock project and session
        now = int(datetime.now().timestamp())
        p = Project(id=1, name="Project Alpha", path="~/alpha", first_seen=now, last_seen=now, session_count=1, total_time=600)
        cmd = Command(timestamp=now, command="git diff", session_id=1, project_id=1)
        s = Session(id=1, start_time=now, end_time=now + 600, duration_seconds=600, project_id=1, commands=[cmd], commits=[
            {"hash": "abcdefabcdef", "timestamp": now, "message": "feat: init", "cleaned_message": "Init"}
        ])
        db.save_data([p], [s], [cmd])
        db.save_commits(1, [{"hash": "abcdefabcdef", "timestamp": now, "message": "feat: init", "cleaned_message": "Init"}])
        
        app = TermStoryWorkspace(db, days_limit=30, config_override={"has_seen_onboarding": True, "ai_enabled": False})
        async with app.run_test() as pilot:
            # Verify widgets are instantiated and layout works
            assert app.query_one("#stats-panel") is not None
            tree = app.query_one("#history-navigator")
            assert tree is not None
            assert app.query_one("#details-canvas") is not None
            assert app.query_one("#search-box") is not None
            
            # Verify the 4-level hierarchy structure
            # Root node has children (Level 1: Categories)
            assert len(tree.root.children) == 3
            timeline_root = tree.root.children[0]
            assert timeline_root.data["category"] == "timeline"
            
            # Timeline node has children (Level 2: Month nodes)
            assert len(timeline_root.children) > 0
            month_node = timeline_root.children[0]
            assert month_node.data["type"] == "month"
            
            # Month node has children (Level 3: Date nodes)
            assert len(month_node.children) > 0
            date_node = month_node.children[0]
            assert date_node.data["type"] == "date"
            
            # Date node has children (Level 4: Project nodes)
            assert len(date_node.children) > 0
            project_node = date_node.children[0]
            assert project_node.data["type"] == "project"
            
            # Project node has children (Level 5: Session nodes)
            assert len(project_node.children) > 0
            session_node = project_node.children[0]
            assert session_node.data["type"] == "session"
            assert session_node.data["session_id"] == 1
            assert session_node.data["project_id"] == 1


def test_strip_ansi():
    assert strip_ansi("\033[1;36mTermstory\033[0m") == "Termstory"
    assert strip_ansi("Simple text") == "Simple text"


def test_clean_command_to_memory():
    # 1. Quoted git commit extraction
    assert clean_command_to_memory("git commit -m 'docs: fix markdown'") == "docs: fix markdown"
    assert clean_command_to_memory('git commit -s -m "feat: user login"') == "feat: user login"
    
    # 2. Humanize checkout and push/pull
    assert clean_command_to_memory("git checkout -b feature/tui") == "Create branch feature/tui"
    assert clean_command_to_memory("git checkout main") == "Switch to branch main"
    assert clean_command_to_memory("git push origin main") == "Push changes to remote"
    
    # 3. Multi-command chain
    assert clean_command_to_memory("git add . && git commit -m 'Release v0.1'") == "Release v0.1"


def test_deduplicate_sessions():
    s1 = Session(id=1, start_time=1000, end_time=2000, duration_seconds=1000, project_id=1)
    s2 = Session(id=2, start_time=1000, end_time=2500, duration_seconds=1500, project_id=1) # duplicate expanding
    s3 = Session(id=3, start_time=3000, end_time=4000, duration_seconds=1000, project_id=1) # unique
    
    deduped = deduplicate_sessions([s1, s2, s3])
    assert len(deduped) == 2
    assert deduped[0].id == 2 # kept max end_time
    assert deduped[0].end_time == 2500
    assert deduped[1].id == 3


@pytest.mark.asyncio
async def test_tui_update_session_label():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "test.db")
        db = Database(db_path)
        db.init_db()

        # Save a session
        now = int(datetime.now().timestamp())
        p = Project(id=1, name="Project Alpha", path="~/alpha", first_seen=now, last_seen=now, session_count=1, total_time=600)
        cmd = Command(timestamp=now, command="git diff", session_id=1, project_id=1)
        s = Session(id=1, start_time=now, end_time=now + 600, duration_seconds=600, project_id=1, commands=[cmd])
        db.save_data([p], [s], [cmd])

        app = TermStoryWorkspace(db, days_limit=30, config_override={"has_seen_onboarding": True, "ai_enabled": False})
        async with app.run_test() as pilot:
            tree = app.query_one("#history-navigator")
            # Find the session leaf
            def find_leaf(node):
                if node.data and node.data.get("type") == "session":
                    return node
                for child in node.children:
                    res = find_leaf(child)
                    if res:
                        return res
                return None
            leaf = find_leaf(tree.root)
            assert leaf is not None
            
            # Update label in-place
            tree.update_session_label(1, "Updated summary message")
            assert "Updated summary message" in str(leaf.label)


@pytest.mark.asyncio
async def test_tui_onboarding_dismiss():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "test.db")
        db = Database(db_path)
        db.init_db()

        app = TermStoryWorkspace(db, days_limit=30, config_override={"has_seen_onboarding": False})
        async with app.run_test() as pilot:
            app.handle_onboarding_result({
                "ai_enabled": True,
                "active_provider": "ollama",
                "providers": {
                    "ollama": {
                        "api_key": "",
                        "api_base_url": "http://localhost:11434/v1",
                        "model_name": "llama3"
                    }
                },
                "has_seen_onboarding": True
            })
            await pilot.pause()
            assert app.config["has_seen_onboarding"] is True
            assert app.config["ai_enabled"] is True
            assert app.config["active_provider"] == "ollama"


@pytest.mark.asyncio
async def test_tui_landing_page_after_onboarding():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "test.db")
        db = Database(db_path)
        db.init_db()

        # Insert a mock project and session for today
        now = int(datetime.now().timestamp())
        p = Project(id=1, name="Project Alpha", path="~/alpha", first_seen=now, last_seen=now, session_count=1, total_time=600)
        cmd = Command(timestamp=now, command="git diff", session_id=1, project_id=1)
        s = Session(id=1, start_time=now, end_time=now + 600, duration_seconds=600, project_id=1, commands=[cmd])
        db.save_data([p], [s], [cmd])

        app = TermStoryWorkspace(db, days_limit=30, config_override={"has_seen_onboarding": False})
        async with app.run_test() as pilot:
            # Simulate dismissing onboarding screen with save
            app.handle_onboarding_result({
                "ai_enabled": True,
                "active_provider": "ollama",
                "providers": {
                    "ollama": {
                        "api_key": "",
                        "api_base_url": "http://localhost:11434/v1",
                        "model_name": "llama3"
                    }
                },
                "has_seen_onboarding": True
            })
            await pilot.pause()
            
            # Verify today's date node is selected as landing page
            tree = app.query_one("#history-navigator")
            cursor_node = tree.cursor_node
            assert cursor_node is not None
            assert cursor_node.data is not None
            assert cursor_node.data.get("type") == "date"
            
            today_str = datetime.now().strftime("%Y-%m-%d")
            assert cursor_node.data.get("date_str") == today_str


@pytest.mark.asyncio
async def test_tui_update_stats_header():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "test.db")
        db = Database(db_path)
        db.init_db()

        app = TermStoryWorkspace(db, days_limit=30, config_override={"has_seen_onboarding": True, "ai_enabled": True, "active_provider": "groq"})
        async with app.run_test() as pilot:
            stats_panel = app.query_one("#stats-panel")
            
            # Active and idle
            app.update_stats_header()
            assert "AI: ACTIVE (GROQ)" in str(stats_panel.render())
            assert "Activity (Last 30 Days):" in str(stats_panel.render())
            
            # Active and summarizing
            app.ai_summarizing = True
            app.update_stats_header()
            assert "Summarizing..." in str(stats_panel.render())


@pytest.mark.asyncio
async def test_tui_action_show_onboarding():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "test.db")
        db = Database(db_path)
        db.init_db()

        app = TermStoryWorkspace(db, days_limit=30, config_override={"has_seen_onboarding": True})
        async with app.run_test() as pilot:
            # Trigger onboarding show action
            app.action_show_onboarding()
            # Verify OnboardingScreen is pushed on the stack
            assert isinstance(app.screen, OnboardingScreen)


@pytest.mark.asyncio
async def test_tui_onboarding_click_disabled():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "test.db")
        db = Database(db_path)
        db.init_db()

        app = TermStoryWorkspace(db, days_limit=30, config_override={"has_seen_onboarding": False})
        async with app.run_test() as pilot:
            # Press 'ctrl+d' (Keep Local Only shortcut) on OnboardingScreen
            await pilot.press("ctrl+d")
            assert app.config["has_seen_onboarding"] is True
            assert app.config["ai_enabled"] is False


@pytest.mark.asyncio
async def test_tui_onboarding_mouse_click():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "test.db")
        db = Database(db_path)
        db.init_db()

        app = TermStoryWorkspace(db, days_limit=30, config_override={"has_seen_onboarding": False})
        async with app.run_test(size=(120, 50)) as pilot:
            # Click the disable button on OnboardingScreen
            button = app.screen.query_one("#btn-disable-ai")
            button.press()
            await pilot.pause()
            assert app.config["has_seen_onboarding"] is True
            assert app.config["ai_enabled"] is False


@pytest.mark.asyncio
async def test_tui_render_interactive_ai_buttons(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "test.db")
        db = Database(db_path)
        db.init_db()
        
        now_ts = int(time.time())
        p = Project(id=1, name="Proj A", path="~/proj-a", first_seen=now_ts, last_seen=now_ts, session_count=1, total_time=0)
        cmd = Command(timestamp=now_ts, command="git diff", exit_code=0, session_id=1, project_id=1)
        s = Session(id=1, start_time=now_ts, end_time=now_ts, duration_seconds=0, project_id=1, commands=[cmd], ai_summary=None)
        db.save_data([p], [s], [cmd])
        
        app = TermStoryWorkspace(
            db, 
            days_limit=30, 
            config_override={
                "has_seen_onboarding": True, 
                "ai_enabled": True, 
                "active_provider": "groq",
                "providers": {
                    "groq": {
                        "api_key": "gsk_test",
                        "api_base_url": "https://api.groq.com/openai/v1",
                        "model_name": "llama3"
                    }
                }
            }
        )
        
        called = []
        def mock_generate_ai_summary(commands, api_key, api_base_url, model_name, provider, *args, **kwargs):
            called.append(commands)
            return "Generated AI summary description"
            
        monkeypatch.setattr("termstory.tui.generate_ai_summary", mock_generate_ai_summary)
        
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            
            # Press the Generate Story button programmatically
            app.query_one("#btn-gen-session-1").press()
            await pilot.pause()
            
            assert len(called) == 1
            assert app.sessions[0].ai_summary == "Generated AI summary description"

            import asyncio
            # Wait for the button to disappear due to cooldown
            for _ in range(50):
                try:
                    app.query_one("#btn-gen-session-1")
                except Exception:
                    break  # Button disappeared
                await asyncio.sleep(0.05)
                
            # Clear the cooldown manually to test regeneration
            app.sessions[0].recent_generation = False
            app.refresh_details_canvas()
            await pilot.pause()

            # Press the button again (now it is '⟳ Regenerate' button)
            app.query_one("#btn-gen-session-1").press()
            await pilot.pause()

            assert len(called) == 2
            assert app.sessions[0].ai_summary == "Generated AI summary description"


@pytest.mark.asyncio
async def test_tui_generate_executive_review(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "test.db")
        db = Database(db_path)
        db.init_db()
        
        now_ts = int(time.time())
        p = Project(id=1, name="Proj A", path="~/proj-a", first_seen=now_ts, last_seen=now_ts, session_count=1, total_time=0)
        cmd = Command(timestamp=now_ts, command="git diff", exit_code=0, session_id=1, project_id=1)
        s = Session(id=1, start_time=now_ts, end_time=now_ts, duration_seconds=0, project_id=1, commands=[cmd], ai_summary="Story")
        db.save_data([p], [s], [cmd])
        
        app = TermStoryWorkspace(
            db, 
            days_limit=30, 
            config_override={
                "has_seen_onboarding": True, 
                "ai_enabled": True, 
                "active_provider": "groq",
                "providers": {
                    "groq": {
                        "api_key": "gsk_test",
                        "api_base_url": "https://api.groq.com/openai/v1",
                        "model_name": "llama3"
                    }
                }
            }
        )
        
        called = []
        def mock_generate_timeframe_summary(stats_summary, api_key, api_base_url, model_name, provider):
            called.append(stats_summary)
            return "Generated Executive Review text."
            
        def mock_generate_daily_chronicle(github_username, session_date, sessions, projects, api_key, api_base_url, model_name, provider):
            called.append(session_date)
            return "Generated Executive Review text."
            
        monkeypatch.setattr("termstory.ai.generate_timeframe_summary", mock_generate_timeframe_summary)
        monkeypatch.setattr("termstory.ai.generate_daily_chronicle", mock_generate_daily_chronicle)
        
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            
            # Press the generate executive review button programmatically
            date_str = datetime.fromtimestamp(now_ts).strftime("%Y-%m-%d")
            app.query_one(f"#btn-exec-{date_str}-date").press()
            await pilot.pause()
            
            import asyncio
            for _ in range(50):
                if len(called) == 1:
                    break
                await asyncio.sleep(0.05)
                
            assert len(called) == 1
            cached = db.get_macro_summary(date_str)
            assert cached == "Generated Executive Review text."

@pytest.mark.asyncio
async def test_tui_overall_timeframe_summary(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "test.db")
        db = Database(db_path)
        db.init_db()
        
        now_ts = int(time.time())
        p = Project(id=1, name="Proj A", path="~/proj-a", first_seen=now_ts, last_seen=now_ts, session_count=1, total_time=0)
        cmd = Command(timestamp=now_ts, command="git diff", exit_code=0, session_id=1, project_id=1)
        s = Session(id=1, start_time=now_ts, end_time=now_ts, duration_seconds=0, project_id=1, commands=[cmd], ai_summary="Story")
        db.save_data([p], [s], [cmd])
        
        app = TermStoryWorkspace(
            db, 
            days_limit=30, 
            config_override={
                "has_seen_onboarding": True, 
                "ai_enabled": True, 
                "active_provider": "groq",
                "providers": {
                    "groq": {
                        "api_key": "gsk_test",
                        "api_base_url": "https://api.groq.com/openai/v1",
                        "model_name": "llama3"
                    }
                }
            }
        )
        
        called = []
        def mock_generate_wrapped_summary(**kwargs):
            called.append(kwargs)
            return "Generated Overall Summary."
            
        monkeypatch.setattr("termstory.tui.generate_wrapped_summary", mock_generate_wrapped_summary)
        
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            
            # Select the timeline category node to display the overall summary
            tree = app.query_one("#history-navigator")
            timeline_node = tree.root.children[0]
            tree.select_node(timeline_node)
            await pilot.pause()
            
            # Press the generate executive review button for overall timeframe
            app.query_one("#btn-exec-overall-overall").press()
            await pilot.pause()
            
            import asyncio
            for _ in range(50):
                if len(called) == 1:
                    break
                await asyncio.sleep(0.05)
                
            assert len(called) == 1
            cached = db.get_macro_summary("overall")
            assert cached == "Generated Overall Summary."


@pytest.mark.asyncio
async def test_tui_bulk_auto_summarize(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "test.db")
        db = Database(db_path)
        db.init_db()
        
        monkeypatch.setattr("time.sleep", lambda secs: None)
        
        now_ts = int(time.time())
        p = Project(id=1, name="Proj A", path="~/proj-a", first_seen=now_ts, last_seen=now_ts, session_count=1, total_time=0)
        cmd = Command(timestamp=now_ts, command="git diff", exit_code=0, session_id=1, project_id=1)
        s = Session(id=1, start_time=now_ts, end_time=now_ts, duration_seconds=0, project_id=1, commands=[cmd], ai_summary=None)
        db.save_data([p], [s], [cmd])
        
        app = TermStoryWorkspace(
            db, 
            days_limit=30, 
            config_override={
                "has_seen_onboarding": True, 
                "ai_enabled": True, 
                "active_provider": "groq",
                "providers": {
                    "groq": {
                        "api_key": "gsk_test",
                        "api_base_url": "https://api.groq.com/openai/v1",
                        "model_name": "llama3"
                    }
                }
            }
        )
        
        called = []
        def mock_generate_ai_summary(commands, api_key, api_base_url, model_name, provider, *args, **kwargs):
            called.append(commands)
            return "Bulk summary output"
            
        monkeypatch.setattr("termstory.tui.generate_ai_summary", mock_generate_ai_summary)
        
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            
            # Press the bulk auto-summarize button programmatically
            date_str = datetime.fromtimestamp(now_ts).strftime("%Y-%m-%d")
            app.query_one(f"#btn-bulk-{date_str}-date").press()
            await pilot.pause()
            
            assert len(called) == 1
            assert app.sessions[0].ai_summary == "Bulk summary output"


@pytest.mark.asyncio
async def test_tui_bulk_auto_summarize_fail_fast(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "test.db")
        db = Database(db_path)
        db.init_db()
        
        monkeypatch.setattr("time.sleep", lambda secs: None)
        
        now_ts = int(time.time()) - 120
        p = Project(id=1, name="Proj A", path="~/proj-a", first_seen=now_ts, last_seen=now_ts + 60, session_count=2, total_time=0)
        cmd1 = Command(timestamp=now_ts, command="git diff", exit_code=0, session_id=1, project_id=1)
        s1 = Session(id=1, start_time=now_ts, end_time=now_ts, duration_seconds=0, project_id=1, commands=[cmd1], ai_summary=None)
        cmd2 = Command(timestamp=now_ts + 60, command="git diff", exit_code=0, session_id=2, project_id=1)
        s2 = Session(id=2, start_time=now_ts + 60, end_time=now_ts + 60, duration_seconds=0, project_id=1, commands=[cmd2], ai_summary=None)
        db.save_data([p], [s1, s2], [cmd1, cmd2])
        
        app = TermStoryWorkspace(
            db, 
            days_limit=30, 
            config_override={
                "has_seen_onboarding": True, 
                "ai_enabled": True, 
                "active_provider": "groq",
                "providers": {
                    "groq": {
                        "api_key": "gsk_test",
                        "api_base_url": "https://api.groq.com/openai/v1",
                        "model_name": "llama3"
                    }
                }
            }
        )
        
        called = []
        def mock_generate_ai_summary(commands, api_key, api_base_url, model_name, provider, *args, **kwargs):
            called.append(commands)
            return None  # Simulate failure
            
        monkeypatch.setattr("termstory.tui.generate_ai_summary", mock_generate_ai_summary)
        monkeypatch.setattr("termstory.ai.get_last_ai_error", lambda: "API connection timeout")
        
        notifications = []
        def mock_notify(message, severity="info", title="", timeout=None):
            notifications.append((message, severity))
            
        monkeypatch.setattr(app, "notify", mock_notify)
        
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            
            # Press the bulk auto-summarize button programmatically
            date_str = datetime.fromtimestamp(now_ts).strftime("%Y-%m-%d")
            app.query_one(f"#btn-bulk-{date_str}-date").press()
            await pilot.pause()
            
            import asyncio
            for _ in range(50):
                if len(notifications) >= 2:
                    break
                await asyncio.sleep(0.05)
            
            # Assert generate_ai_summary was called exactly once, since we fail-fast/break on the first error
            assert len(called) == 1
            assert any("Failed to generate story for session 1: API connection timeout" in msg for msg, sev in notifications)
            assert any("Bulk auto-summarization stopped. Succeeded: 0/2." in msg for msg, sev in notifications)


@pytest.mark.asyncio
async def test_tui_help_screen():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "test.db")
        db = Database(db_path)
        db.init_db()
        
        app = TermStoryWorkspace(
            db, 
            days_limit=30, 
            config_override={
                "has_seen_onboarding": True, 
                "ai_enabled": False, 
            }
        )
        
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            
            # Help screen is not showing initially
            assert not any(screen.__class__.__name__ == "HelpScreen" for screen in app.screen_stack)
            
            # Press ?
            await pilot.press("?")
            await pilot.pause()
            
            # Help screen should be showing now
            from termstory.tui import HelpScreen
            help_screen = app.screen
            assert isinstance(help_screen, HelpScreen)
            
            # Dismiss using close button
            help_screen.query_one("#btn-close-help").press()
            await pilot.pause()
            
            # Help screen should be dismissed
            assert not isinstance(app.screen, HelpScreen)
            
            # Open it again
            await pilot.press("?")
            await pilot.pause()
            help_screen = app.screen
            assert isinstance(help_screen, HelpScreen)
            
            # Dismiss using ESC key
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, HelpScreen)

            # Open it again
            await pilot.press("?")
            await pilot.pause()
            help_screen = app.screen
            assert isinstance(help_screen, HelpScreen)
            
            # Dismiss using q key
            await pilot.press("q")
            await pilot.pause()
            assert not isinstance(app.screen, HelpScreen)


def test_tui_copy_to_clipboard(monkeypatch):
    from termstory.database import Database
    from termstory.tui import TermStoryWorkspace
    import subprocess
    from textual.app import App
    
    db = Database(":memory:")
    db.init_db()
    
    app = TermStoryWorkspace(
        db, 
        days_limit=30, 
        config_override={"has_seen_onboarding": True, "ai_enabled": False}
    )
    
    copied_texts = []
    
    class MockProcess:
        def __init__(self):
            self.returncode = 0
        def communicate(self, input):
            copied_texts.append(input.decode('utf-8'))
            return (b'', b'')
            
    def mock_popen(*args, **kwargs):
        return MockProcess()
        
    monkeypatch.setattr(subprocess, "Popen", mock_popen)
    
    parent_called = []
    def mock_parent_copy(self, text):
        parent_called.append(text)
        
    monkeypatch.setattr(App, "copy_to_clipboard", mock_parent_copy)
    
    app.copy_to_clipboard("test-copy-text")
    
    assert "test-copy-text" in copied_texts
    assert "test-copy-text" in parent_called


@pytest.mark.asyncio
async def test_reset_action():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_termstory.db")
        db = Database(db_path)
        db.init_db()
        
        app = TermStoryWorkspace(
            db, 
            days_limit=30, 
            config_override={"has_seen_onboarding": True, "ai_enabled": False}
        )
        
        async with app.run_test() as pilot:
            assert app.was_reset is False
            await pilot.press("ctrl+shift+h")
            await pilot.press("y")
            assert app.was_reset is True


@pytest.mark.asyncio
async def test_tui_month_no_activity_feed():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "test.db")
        db = Database(db_path)
        db.init_db()
        
        now_ts = int(time.time())
        p = Project(id=1, name="Proj A", path="~/proj-a", first_seen=now_ts, last_seen=now_ts, session_count=1, total_time=0)
        cmd = Command(timestamp=now_ts, command="git diff", exit_code=0, session_id=1, project_id=1)
        s = Session(id=1, start_time=now_ts, end_time=now_ts, duration_seconds=0, project_id=1, commands=[cmd], ai_summary="Story")
        db.save_data([p], [s], [cmd])
        
        app = TermStoryWorkspace(
            db, 
            days_limit=30, 
            config_override={"has_seen_onboarding": True, "ai_enabled": False}
        )
        app.auto_select_today_on_mount = False
        
        async with app.run_test() as pilot:
            canvas = app.query_one("#details-canvas")
            canvas.render_wrapped_view("June 2026", "2026-06", [s], [p])
            await pilot.pause()
            
            from textual.css.query import NoMatches
            with pytest.raises(NoMatches):
                canvas.query_one(".feed-container")
                
            canvas.render_time_summary("📊 Overall Dashboard Summary", [s], [p], timeframe_id="overall", timeframe_type="overall")
            await pilot.pause()
            
            feed = canvas.query_one(".feed-container")
            assert feed is not None


@pytest.mark.asyncio
async def test_wrapped_view_generation_and_layout(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "test.db")
        db = Database(db_path)
        db.init_db()
        
        now_ts = int(time.time())
        p = Project(id=1, name="Proj A", path="~/proj-a", first_seen=now_ts, last_seen=now_ts, session_count=1, total_time=0)
        cmd = Command(timestamp=now_ts, command="git diff", exit_code=0, session_id=1, project_id=1)
        s = Session(id=1, start_time=now_ts, end_time=now_ts, duration_seconds=120, project_id=1, commands=[cmd], ai_summary="Story")
        db.save_data([p], [s], [cmd])
        
        app = TermStoryWorkspace(
            db, 
            days_limit=30, 
            config_override={
                "has_seen_onboarding": True, 
                "ai_enabled": True, 
                "active_provider": "groq",
                "providers": {
                    "groq": {
                        "api_key": "gsk_test",
                        "api_base_url": "https://api.groq.com/openai/v1",
                        "model_name": "llama3"
                    }
                }
            }
        )
        app.auto_select_today_on_mount = False
        
        called_args = []
        def mock_generate_wrapped_summary(**kwargs):
            called_args.append(kwargs)
            return "🧠 IMPOSTER SYNDROME INDEX: 94%\n   Mock audit text.\n\n[VERDICT] Mock verdict text."
            
        monkeypatch.setattr("termstory.tui.generate_wrapped_summary", mock_generate_wrapped_summary)
        
        async with app.run_test(size=(120, 50)) as pilot:
            canvas = app.query_one("#details-canvas")
            canvas.render_wrapped_view("June 2026", "2026-06", [s], [p])
            await pilot.pause()
            
            app.query_one("#btn-exec-2026-06-month").press()
            await pilot.pause()
            
            import asyncio
            for _ in range(50):
                if len(called_args) == 1:
                    break
                await asyncio.sleep(0.05)
                
            assert len(called_args) == 1
            assert "focus_hours" in called_args[0]
            assert "additions" in called_args[0]
            assert "deletions" in called_args[0]
            assert "merged_prs" in called_args[0]
            
            cached = db.get_macro_summary("2026-06")
            assert "Mock verdict text." in cached


@pytest.mark.asyncio
async def test_tui_empty_state_handling():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "test_empty.db")
        db = Database(db_path)
        db.init_db()
        
        # Instantiate workspace with 0 sessions
        app = TermStoryWorkspace(
            db, 
            days_limit=30, 
            config_override={"has_seen_onboarding": True, "ai_enabled": True, "active_provider": "openai"}
        )
        app.auto_select_today_on_mount = False
        
        async with app.run_test() as pilot:
            canvas = app.query_one("#details-canvas")
            
            # Since there are no sessions, on_mount will trigger render_time_summary with 0 sessions
            # rendering the empty state welcome message
            canvas.render_time_summary("📊 Overall Dashboard Summary", [], [])
            await pilot.pause()
            
            # Verify the text is rendered correctly
            content = ""
            for widget in canvas.children:
                r = getattr(widget, "_Static__content", None) or getattr(widget, "_renderable", None) or getattr(widget, "renderable", None)
                if r is not None:
                    if hasattr(r, "plain"):
                        content += r.plain
                    elif hasattr(r, "markup"):
                        content += r.markup
                    else:
                        content += str(r)
            
            assert "Welcome to TermStory!" in content
            assert "We couldn't find any shell history yet." in content
            assert "Try running some terminal commands, or check your macOS Privacy permissions." in content
            
            # Verify no timeframe summary buttons are present
            from textual.css.query import NoMatches
            with pytest.raises(NoMatches):
                canvas.query_one(".exec-btn")
            with pytest.raises(NoMatches):
                canvas.query_one(".bulk-btn")


@pytest.mark.asyncio
async def test_tui_deep_search_scope_escape():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "test_search.db")
        db = Database(db_path)
        db.init_db()
        
        # 1. Setup a project and a session
        p1 = Project(id=1, name="My Awesome Project", path="~/projects/my-awesome-project", first_seen=1000, last_seen=1000, session_count=1, total_time=100)
        cmd1 = Command(timestamp=1000, command="git commit -m 'initial commit'", session_id=1, project_id=1)
        s1 = Session(id=1, start_time=1000, end_time=1100, duration_seconds=100, project_id=1, commands=[cmd1])
        db.save_data([p1], [s1], [cmd1])
        
        # 2. Instantiate TUI
        app = TermStoryWorkspace(
            db, 
            days_limit=30, 
            config_override={"has_seen_onboarding": True, "ai_enabled": False}
        )
        app.auto_select_today_on_mount = False
        
        async with app.run_test() as pilot:
            # Check original sessions back up
            assert len(app.original_sessions) == 0
            assert app.is_deep_search_active is False
            
            # Press '/' to open search box
            await pilot.press("/")
            search_box = app.query_one("#search-box")
            assert search_box.styles.display == "block"
            
            # Type search query
            await pilot.click("#search-box")
            await pilot.press(*list("initial"))
            await pilot.pause()
            
            # Trigger deep search by pressing enter
            await pilot.press("enter")
            await pilot.pause()
            
            # Verify deep search is active and populated
            assert app.is_deep_search_active is True
            assert app.deep_search_query == "initial"
            
            # Verify tree root title is customized
            tree = app.query_one("#history-navigator")
            timeline_root = None
            for child in tree.root.children:
                if child.data and child.data.get("category") == "timeline":
                    timeline_root = child
                    break
            assert timeline_root is not None
            assert "Search Results:" in str(timeline_root.label)
            assert "initial" in str(timeline_root.label)
            
            # Verify escape key clears the deep search
            await pilot.press("escape")
            await pilot.pause()
            
            assert app.is_deep_search_active is False
            new_timeline_root = None
            for child in tree.root.children:
                if child.data and child.data.get("category") == "timeline":
                    new_timeline_root = child
                    break
            assert new_timeline_root is not None
            assert "Search Results:" not in str(new_timeline_root.label)
            assert "Timeline" in str(new_timeline_root.label)

@pytest.mark.asyncio
async def test_tui_api_key_validation():
    from termstory.tui import OnboardingScreen
    # Provide a config that defaults to groq with no api key
    screen = OnboardingScreen({"active_provider": "groq", "providers": {}})
    
    # We need an app context to mount the screen
    from textual.app import App
    class DummyApp(App):
        pass
        
    app = DummyApp()
    async with app.run_test() as pilot:
        # Push the screen
        await app.push_screen(screen)
        await pilot.pause()
        
        # Verify initial state of error label
        error_label = screen.query_one("#error-api-key")
        assert error_label.styles.display == "none"
        
        # Click save without entering an API key
        await pilot.click("#btn-save")
        await pilot.pause()
        
        # Verify error label becomes visible and block dismissal
        assert error_label.styles.display == "block"
        assert len(app.screen_stack) > 1  # Screen did not dismiss
        
        # Switch to ollama, which does not require an API key
        await pilot.click("#btn-select-ollama")
        await pilot.pause()
        
        # Click save again
        await pilot.click("#btn-save")
        await pilot.pause()
        
        # Should now dismiss successfully
        assert len(app.screen_stack) == 1

@pytest.mark.asyncio
async def test_tui_worker_cancellation(monkeypatch):
    import tempfile
    import os
    import time
    import asyncio
    from termstory.database import Database
    from termstory.models import Session, Command
    from termstory.tui import TermStoryWorkspace

    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "test.db")
        db = Database(db_path)
        db.init_db()
        
        # Ingest one session
        from termstory.models import Project
        now_t = int(time.time())
        p = Project(id=1, name="test-proj", path="/path/to/test-proj", first_seen=now_t, last_seen=now_t, session_count=1, total_time=60)
        cmd = Command(timestamp=now_t, command="git commit", exit_code=0, session_id=1, project_id=1)
        s = Session(id=1, project_id=1, start_time=now_t-60, end_time=now_t, duration_seconds=60, commands=[cmd])
        db.save_data([p], [s], [cmd])
        
        app = TermStoryWorkspace(
            db,
            days_limit=30,
            config_override={
                "has_seen_onboarding": True,
                "ai_enabled": True,
                "active_provider": "openai",
                "providers": {
                    "openai": {
                        "api_key": "fake-key",
                        "api_base_url": "http://localhost:8080",
                        "model_name": "gpt-4"
                    }
                }
            }
        )
        
        called_cancel = []
        def mock_generate_ai_summary(*args, **kwargs):
            from textual.worker import get_current_worker
            worker = get_current_worker()
            for _ in range(50):
                if worker.is_cancelled:
                    called_cancel.append(True)
                    return None
                time.sleep(0.01)
            return "AI Summary"
            
        monkeypatch.setattr("termstory.tui.generate_ai_summary", mock_generate_ai_summary)
        
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            
            # Start generation
            app.generate_single_session_story(app.sessions[0])
            # Wait briefly for thread to enter the mock generate function
            await asyncio.sleep(0.05)
            # Immediately cancel the worker
            for worker in app.workers:
                if worker.name == "generate_single_session_story":
                    worker.cancel()
            
            await pilot.pause()
            await asyncio.sleep(0.2)
            
            # Since the worker was cancelled, the generation should be aborted,
            # no summary saved, and not marked as failed.
            assert len(called_cancel) > 0
            assert app.sessions[0].ai_summary is None
            assert not getattr(app.sessions[0], "generation_failed", False)


@pytest.mark.asyncio
async def test_tui_batch_8_cyberpunk_animations(monkeypatch):
    """Test the newly added Batch 8 features (Matrix Defrag, Ghost Typer, Heatmap Pulse)."""
    import tempfile
    import sqlite3
    import asyncio
    from termstory.database import Database
    from termstory.models import Session, Project, Command
    from termstory.tui import TermStoryWorkspace, MatrixDefragScreen, GhostTyperScreen
    
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "test.db")
        db = Database(db_path)
        db.init_db()
        
        # Setup dummy data
        p = Project(id=1, name="dummy", path="/dummy", first_seen=1000, last_seen=1100, session_count=1, total_time=100)
        s = Session(id=1, start_time=1000, end_time=1010, duration_seconds=10, project_id=1)
        c = Command(id=1, timestamp=1000, command="echo 'cyberpunk animation test'", session_id=1)
        c.project_id = 1
        s.commands = [c]
        db.save_data([p], [s], [c])
        
        config = {
            "active_provider": "disabled",
            "has_seen_onboarding": True
        }
        app = TermStoryWorkspace(db=db, days_limit=30, config_override=config)
        app.sessions = [s]
        app.projects = [p]
        
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            
            # 1. Test Matrix Defrag animation trigger
            await pilot.press("d")
            await pilot.pause()
            assert isinstance(app.screen, MatrixDefragScreen)
            # Wait for animation to finish or manually dismiss
            app.screen.dismiss()
            await pilot.pause()
            
            # 2. Test Ghost Playback keybinding trigger
            tree = app.query_one("#history-navigator")
            
            # Find the session node in the tree and select it
            session_node = None
            def traverse(node):
                nonlocal session_node
                if node.data and node.data.get("type") == "session":
                    session_node = node
                    return
                for child in node.children:
                    traverse(child)
            traverse(tree.root)
            
            if session_node:
                tree.select_node(session_node)
                await pilot.pause()
                
                await pilot.press("g")
                await pilot.pause()
                assert isinstance(app.screen, GhostTyperScreen)
                # Dismiss
                app.screen.dismiss()
                await pilot.pause()
                
            # 3. Test Heatmap Pulse animation increment
            current_pulse = app.pulse_phase
            app.step_heatmap_pulse()
            assert app.pulse_phase == current_pulse + 1


def test_ghost_typer_bracket_escaping():
    from termstory.tui import GhostTyperScreen
    screen = GhostTyperScreen(commands=["git commit -m '[fix] issue'"])
    screen.set_timer = lambda duration, callback: None
    screen.lines.append("operator@termstory:~$ ")
    # Type until the command completes
    while screen.current_cmd_idx == 0:
        screen.type_character()
    # Check that brackets were escaped in the command line
    assert any("\\[fix]" in line for line in screen.lines)



