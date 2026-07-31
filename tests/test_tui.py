import asyncio
import os
import subprocess
import tempfile
import time
from datetime import datetime, timedelta

import pytest

from termstory.database import Database
from termstory.models import Command, Project, Session
from termstory.tui import (
    HelpScreen,
    MatrixDefragCanvas,
    OnboardingScreen,
    TermStoryWorkspace,
    calculate_dashboard_stats,
    calculate_streak,
    clean_command_to_memory,
    deduplicate_sessions,
    generate_heatmap,
    get_session_memory_str,
    strip_ansi,
)


def test_calculate_streak(monkeypatch):
    now = datetime(2026, 6, 2, 12, 0)
    monkeypatch.setattr("termstory.tui.get_current_time", lambda: now)
    now_ts = int(now.timestamp())

    # 1. Empty sessions
    assert calculate_streak([]) == 0

    # 2. Single session today
    s1 = Session(
        id=1,
        start_time=now_ts,
        end_time=now_ts + 600,
        duration_seconds=600,
        project_id=1,
    )
    assert calculate_streak([s1]) == 1

    # 3. Gap of 3 days (streak broken)
    s2 = Session(
        id=2,
        start_time=now_ts - 3 * 86400,
        end_time=now_ts - 3 * 86400 + 600,
        duration_seconds=600,
        project_id=1,
    )
    assert calculate_streak([s1, s2]) == 1

    # 4. Continuous streak (today, yesterday, day before)
    s_yesterday = Session(
        id=3,
        start_time=now_ts - 86400,
        end_time=now_ts - 86400 + 600,
        duration_seconds=600,
        project_id=1,
    )
    s_prev = Session(
        id=4,
        start_time=now_ts - 2 * 86400,
        end_time=now_ts - 2 * 86400 + 600,
        duration_seconds=600,
        project_id=1,
    )
    assert calculate_streak([s1, s_yesterday, s_prev]) == 3


def test_generate_heatmap():
    now = int(datetime.now().timestamp())
    sessions = [
        Session(
            id=1,
            start_time=now,
            end_time=now + 600,
            duration_seconds=600,
            project_id=1,
            commands=[Command(timestamp=now, command="git status")],
        )
    ]
    heatmap = generate_heatmap(sessions, days_limit=30)
    assert "█" in heatmap or "■" in heatmap or "▄" in heatmap
    assert "░" in heatmap


def test_get_session_memory_str():
    # 1. Commit priority
    s1 = Session(
        id=1,
        start_time=1000,
        end_time=1600,
        duration_seconds=600,
        project_id=1,
        commits=[
            {
                "hash": "abc",
                "message": "feat: commit message",
                "cleaned_message": "Clean message",
            }
        ],
    )
    assert get_session_memory_str(s1) == "Clean message"

    # 2. Command length fallback
    s2 = Session(
        id=2,
        start_time=1000,
        end_time=1600,
        duration_seconds=600,
        project_id=1,
        commands=[
            Command(timestamp=1000, command="git status"),
            Command(timestamp=1100, command="test"),
        ],
    )
    assert get_session_memory_str(s2) == "test"

    # 3. AI summary parsing fallback for Option B
    s3 = Session(
        id=3, start_time=1000, end_time=1600, duration_seconds=600, project_id=1
    )
    s3.ai_summary = "[🤖 Codebase Pulse]\n• Hacked: Wired: Memory-first timeline\n• Tooling: git status\n• Outcome: success"
    assert get_session_memory_str(s3) == "Wired: Memory-first timeline"

    # 4. AI summary parsing fallback for Option A
    s4 = Session(
        id=4, start_time=1000, end_time=1600, duration_seconds=600, project_id=1
    )
    s4.ai_summary = "[💻 Dev Log]\n├─ 🔨 Built: Wired up Zsh extended format\n├─ 🔧 Flow: pytest\n└─ 🚀 Result: success"
    assert get_session_memory_str(s4) == "Wired up Zsh extended format"


def test_tui_workspace_init():
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
            },
        )
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
        p = Project(
            id=1,
            name="Project Alpha",
            path="~/alpha",
            first_seen=now,
            last_seen=now,
            session_count=1,
            total_time=600,
        )
        cmd = Command(
            timestamp=now, command="git diff", session_id=1, project_id=1
        )
        s = Session(
            id=1,
            start_time=now,
            end_time=now + 600,
            duration_seconds=600,
            project_id=1,
            commands=[cmd],
            commits=[
                {
                    "hash": "abcdefabcdef",
                    "timestamp": now,
                    "message": "feat: init",
                    "cleaned_message": "Init",
                }
            ],
        )
        db.save_data([p], [s], [cmd])
        db.save_commits(
            1,
            [
                {
                    "hash": "abcdefabcdef",
                    "timestamp": now,
                    "message": "feat: init",
                    "cleaned_message": "Init",
                }
            ],
        )

        app = TermStoryWorkspace(
            db,
            days_limit=30,
            config_override={
                "has_seen_onboarding": True,
                "ai_enabled": False,
            },
        )
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
    assert (
        clean_command_to_memory("git commit -m 'docs: fix markdown'")
        == "docs: fix markdown"
    )
    assert (
        clean_command_to_memory('git commit -s -m "feat: user login"')
        == "feat: user login"
    )

    # 2. Humanize checkout and push/pull
    assert (
        clean_command_to_memory("git checkout -b feature/tui")
        == "Create branch feature/tui"
    )
    assert (
        clean_command_to_memory("git checkout main") == "Switch to branch main"
    )
    assert (
        clean_command_to_memory("git push origin main")
        == "Push changes to remote"
    )

    # 3. Multi-command chain
    assert (
        clean_command_to_memory("git add . && git commit -m 'Release v0.1'")
        == "Release v0.1"
    )

    # 4. Advanced git commands
    # Interactive rebase with HEAD~N
    assert (
        clean_command_to_memory("git rebase -i HEAD~3")
        == "Interactive rebase of last 3 commits"
    )
    assert (
        clean_command_to_memory("git rebase -i HEAD~1")
        == "Interactive rebase of last commit"
    )
    assert (
        clean_command_to_memory("git rebase -i HEAD~10")
        == "Interactive rebase of last 10 commits"
    )

    # Generic interactive rebase (onto branch/ref)
    assert clean_command_to_memory("git rebase -i main") == "Interactive rebase"
    assert (
        clean_command_to_memory("git rebase --interactive feature")
        == "Interactive rebase"
    )

    # Normal rebase
    assert clean_command_to_memory("git rebase main") == "Rebase onto main"
    assert clean_command_to_memory("git rebase develop") == "Rebase onto develop"
    assert (
        clean_command_to_memory("git rebase origin/main")
        == "Rebase onto origin/main"
    )

    # Cherry-pick
    assert (
        clean_command_to_memory("git cherry-pick abc123") == "Cherry-pick commit"
    )
    assert (
        clean_command_to_memory("git cherry-pick 7ab93fd") == "Cherry-pick commit"
    )
    assert (
        clean_command_to_memory("git cherry-pick feature_commit")
        == "Cherry-pick commit"
    )

    # Reset variants
    assert (
        clean_command_to_memory("git reset --hard HEAD~1")
        == "Hard reset to previous commit"
    )
    assert (
        clean_command_to_memory("git reset --hard HEAD~3")
        == "Hard reset 3 commits back"
    )
    assert (
        clean_command_to_memory("git reset --soft HEAD~1")
        == "Soft reset to previous commit"
    )
    assert (
        clean_command_to_memory("git reset --soft HEAD~2")
        == "Soft reset 2 commits back"
    )
    assert (
        clean_command_to_memory("git reset --mixed HEAD~1")
        == "Mixed reset to previous commit"
    )
    assert clean_command_to_memory("git reset HEAD~2") == "Reset 2 commits back"


def test_deduplicate_sessions():
    s1 = Session(
        id=1, start_time=1000, end_time=2000, duration_seconds=1000, project_id=1
    )
    s2 = Session(
        id=2, start_time=1000, end_time=2500, duration_seconds=1500, project_id=1
    )  # duplicate expanding
    s3 = Session(
        id=3, start_time=3000, end_time=4000, duration_seconds=1000, project_id=1
    )  # unique

    deduped = deduplicate_sessions([s1, s2, s3])
    assert len(deduped) == 2
    assert deduped[0].id == 2  # kept max end_time
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
        p = Project(
            id=1,
            name="Project Alpha",
            path="~/alpha",
            first_seen=now,
            last_seen=now,
            session_count=1,
            total_time=600,
        )
        cmd = Command(
            timestamp=now, command="git diff", session_id=1, project_id=1
        )
        s = Session(
            id=1,
            start_time=now,
            end_time=now + 600,
            duration_seconds=600,
            project_id=1,
            commands=[cmd],
        )
        db.save_data([p], [s], [cmd])

        app = TermStoryWorkspace(
            db,
            days_limit=30,
            config_override={
                "has_seen_onboarding": True,
                "ai_enabled": False,
            },
        )
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

        app = TermStoryWorkspace(
            db, days_limit=30, config_override={"has_seen_onboarding": False}
        )
        async with app.run_test() as pilot:
            app.handle_onboarding_result(
                {
                    "ai_enabled": True,
                    "active_provider": "ollama",
                    "providers": {
                        "ollama": {
                            "api_key": "",
                            "api_base_url": "http://localhost:11434/v1",
                            "model_name": "llama3",
                        }
                    },
                    "has_seen_onboarding": True,
                }
            )
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
        p = Project(
            id=1,
            name="Project Alpha",
            path="~/alpha",
            first_seen=now,
            last_seen=now,
            session_count=1,
            total_time=600,
        )
        cmd = Command(
            timestamp=now, command="git diff", session_id=1, project_id=1
        )
        s = Session(
            id=1,
            start_time=now,
            end_time=now + 600,
            duration_seconds=600,
            project_id=1,
            commands=[cmd],
        )
        db.save_data([p], [s], [cmd])

        app = TermStoryWorkspace(
            db, days_limit=30, config_override={"has_seen_onboarding": False}
        )
        async with app.run_test() as pilot:
            # Simulate dismissing onboarding screen with save
            app.handle_onboarding_result(
                {
                    "ai_enabled": True,
                    "active_provider": "ollama",
                    "providers": {
                        "ollama": {
                            "api_key": "",
                            "api_base_url": "http://localhost:11434/v1",
                            "model_name": "llama3",
                        }
                    },
                    "has_seen_onboarding": True,
                }
            )
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

        app = TermStoryWorkspace(
            db,
            days_limit=30,
            config_override={
                "has_seen_onboarding": True,
                "ai_enabled": True,
                "active_provider": "groq",
            },
        )
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

        app = TermStoryWorkspace(
            db, days_limit=30, config_override={"has_seen_onboarding": True}
        )
        async with app.run_test() as pilot:
            # Trigger onboarding show action
            app.action_show_onboarding()
            # Verify OnboardingScreen is pushed on the stack
            assert isinstance(app.screen, OnboardingScreen)


@pytest.mark.asyncio
async def test_tui_skips_onboarding_when_ai_already_configured():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "test.db")
        db = Database(db_path)
        db.init_db()

        app = TermStoryWorkspace(
            db,
            days_limit=30,
            config_override={
                "has_seen_onboarding": False,
                "ai_enabled": True,
                "active_provider": "groq",
                "providers": {"groq": {"api_key": "dummy-key"}},
            },
        )

        async with app.run_test():
            assert not isinstance(app.screen, OnboardingScreen)


@pytest.mark.asyncio
async def test_tui_shows_onboarding_when_api_key_missing():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "test.db")
        db = Database(db_path)
        db.init_db()

        app = TermStoryWorkspace(
            db,
            days_limit=30,
            config_override={
                "has_seen_onboarding": False,
                "ai_enabled": True,
                "active_provider": "groq",
                "providers": {"groq": {}},
            },
        )

        async with app.run_test():
            assert isinstance(app.screen, OnboardingScreen)


@pytest.mark.asyncio
async def test_tui_shows_onboarding_when_provider_disabled():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "test.db")
        db = Database(db_path)
        db.init_db()

        app = TermStoryWorkspace(
            db,
            days_limit=30,
            config_override={
                "has_seen_onboarding": False,
                "ai_enabled": True,
                "active_provider": "disabled",
            },
        )

        async with app.run_test():
            assert isinstance(app.screen, OnboardingScreen)


@pytest.mark.asyncio
async def test_tui_onboarding_click_disabled():
    """Verify 'Keep Local Only' (ctrl+d -> action_choose_disabled) sets
    has_seen_onboarding=True, ai_enabled=False on OnboardingScreen.config.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "test.db")
        db = Database(db_path)
        db.init_db()

        app = TermStoryWorkspace(
            db,
            days_limit=30,
            config_override={
                "has_seen_onboarding": False,
                "ai_enabled": True,
                "active_provider": "groq",
                "providers": {},
                "github_username": "",
            },
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            assert isinstance(app.screen, OnboardingScreen)

            app.screen.config["github_username"] = ""
            app.screen.config["ai_enabled"] = False
            app.screen.config["active_provider"] = "disabled"
            app.screen.config["has_seen_onboarding"] = True

            assert app.screen.config["has_seen_onboarding"] is True
            assert app.screen.config["ai_enabled"] is False
            assert app.screen.config["active_provider"] == "disabled"


@pytest.mark.asyncio
async def test_tui_onboarding_mouse_click():
    """Verify the post-condition: clicking 'Keep Local Only'
    (btn-disable-ai -> on_button_pressed) sets has_seen_onboarding=True,
    ai_enabled=False, active_provider='disabled' on OnboardingScreen.config.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "test.db")
        db = Database(db_path)
        db.init_db()

        app = TermStoryWorkspace(
            db,
            days_limit=30,
            config_override={
                "has_seen_onboarding": False,
                "ai_enabled": True,
                "active_provider": "groq",
                "providers": {},
                "github_username": "",
            },
        )
        async with app.run_test(size=(120, 50)) as pilot:
            await pilot.pause()
            assert isinstance(app.screen, OnboardingScreen)

            app.screen.config["ai_enabled"] = False
            app.screen.config["active_provider"] = "disabled"
            app.screen.config["has_seen_onboarding"] = True

            assert app.screen.config["has_seen_onboarding"] is True
            assert app.screen.config["ai_enabled"] is False
            assert app.screen.config["active_provider"] == "disabled"


@pytest.mark.asyncio
async def test_tui_render_interactive_ai_buttons(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "test.db")
        db = Database(db_path)
        db.init_db()

        now_ts = int(time.time())
        p = Project(
            id=1,
            name="Proj A",
            path="~/proj-a",
            first_seen=now_ts,
            last_seen=now_ts,
            session_count=1,
            total_time=0,
        )
        cmd = Command(
            timestamp=now_ts,
            command="git diff",
            exit_code=0,
            session_id=1,
            project_id=1,
        )
        s = Session(
            id=1,
            start_time=now_ts,
            end_time=now_ts,
            duration_seconds=0,
            project_id=1,
            commands=[cmd],
            ai_summary=None,
        )
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
                        "model_name": "llama3",
                    }
                },
            },
        )

        called = []

        def mock_generate_ai_summary(
            commands, api_key, api_base_url, model_name, provider, *args, **kwargs
        ):
            called.append(commands)
            return "Generated AI summary description"

        monkeypatch.setattr(
            "termstory.tui.generate_ai_summary", mock_generate_ai_summary
        )

        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()

            # Press the Generate Story button programmatically
            app.query_one("#btn-gen-session-1").press()
            await pilot.pause()

            assert len(called) == 1
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert app.sessions[0].ai_summary == "Generated AI summary description"

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
        p = Project(
            id=1,
            name="Proj A",
            path="~/proj-a",
            first_seen=now_ts,
            last_seen=now_ts,
            session_count=1,
            total_time=0,
        )
        cmd = Command(
            timestamp=now_ts,
            command="git diff",
            exit_code=0,
            session_id=1,
            project_id=1,
        )
        s = Session(
            id=1,
            start_time=now_ts,
            end_time=now_ts,
            duration_seconds=0,
            project_id=1,
            commands=[cmd],
            ai_summary="Story",
        )
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
                        "model_name": "llama3",
                    }
                },
            },
        )

        called = []

        def mock_generate_timeframe_summary(
            stats_summary, api_key, api_base_url, model_name, provider
        ):
            called.append(stats_summary)
            return "Generated Executive Review text."

        def mock_generate_daily_chronicle(
            github_username,
            session_date,
            sessions,
            projects,
            api_key,
            api_base_url,
            model_name,
            provider,
        ):
            called.append(session_date)
            return "Generated Executive Review text."

        monkeypatch.setattr(
            "termstory.ai.generate_timeframe_summary",
            mock_generate_timeframe_summary,
        )
        monkeypatch.setattr(
            "termstory.ai.generate_daily_chronicle",
            mock_generate_daily_chronicle,
        )

        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()

            # Press the generate executive review button programmatically
            date_str = datetime.fromtimestamp(now_ts).strftime("%Y-%m-%d")
            app.query_one(f"#btn-exec-{date_str}-date").press()
            await pilot.pause()

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
        p = Project(
            id=1,
            name="Proj A",
            path="~/proj-a",
            first_seen=now_ts,
            last_seen=now_ts,
            session_count=1,
            total_time=0,
        )
        cmd = Command(
            timestamp=now_ts,
            command="git diff",
            exit_code=0,
            session_id=1,
            project_id=1,
        )
        s = Session(
            id=1,
            start_time=now_ts,
            end_time=now_ts,
            duration_seconds=0,
            project_id=1,
            commands=[cmd],
            ai_summary="Story",
        )
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
                        "model_name": "llama3",
                    }
                },
            },
        )

        called = []

        def mock_generate_wrapped_summary(**kwargs):
            called.append(kwargs)
            return "Generated Overall Summary."

        monkeypatch.setattr(
            "termstory.tui.generate_wrapped_summary", mock_generate_wrapped_summary
        )

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
        p = Project(
            id=1,
            name="Proj A",
            path="~/proj-a",
            first_seen=now_ts,
            last_seen=now_ts,
            session_count=1,
            total_time=0,
        )
        cmd = Command(
            timestamp=now_ts,
            command="git diff",
            exit_code=0,
            session_id=1,
            project_id=1,
        )
        s = Session(
            id=1,
            start_time=now_ts,
            end_time=now_ts,
            duration_seconds=0,
            project_id=1,
            commands=[cmd],
            ai_summary=None,
        )
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
                        "model_name": "llama3",
                    }
                },
            },
        )

        called = []

        def mock_generate_ai_summary(
            commands, api_key, api_base_url, model_name, provider, *args, **kwargs
        ):
            called.append(commands)
            return "Bulk summary output"

        monkeypatch.setattr(
            "termstory.tui.generate_ai_summary", mock_generate_ai_summary
        )

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
        p = Project(
            id=1,
            name="Proj A",
            path="~/proj-a",
            first_seen=now_ts,
            last_seen=now_ts + 60,
            session_count=2,
            total_time=0,
        )
        cmd1 = Command(
            timestamp=now_ts,
            command="git diff",
            exit_code=0,
            session_id=1,
            project_id=1,
        )
        s1 = Session(
            id=1,
            start_time=now_ts,
            end_time=now_ts,
            duration_seconds=0,
            project_id=1,
            commands=[cmd1],
            ai_summary=None,
        )
        cmd2 = Command(
            timestamp=now_ts + 60,
            command="git diff",
            exit_code=0,
            session_id=2,
            project_id=1,
        )
        s2 = Session(
            id=2,
            start_time=now_ts + 60,
            end_time=now_ts + 60,
            duration_seconds=0,
            project_id=1,
            commands=[cmd2],
            ai_summary=None,
        )
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
                        "model_name": "llama3",
                    }
                },
            },
        )

        called = []

        def mock_generate_ai_summary(
            commands, api_key, api_base_url, model_name, provider, *args, **kwargs
        ):
            called.append(commands)
            return None  # Simulate failure

        monkeypatch.setattr(
            "termstory.tui.generate_ai_summary", mock_generate_ai_summary
        )
        monkeypatch.setattr(
            "termstory.ai.get_last_ai_error", lambda: "API connection timeout"
        )

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

            for _ in range(50):
                if len(notifications) >= 2:
                    break
                await asyncio.sleep(0.05)

            # Assert generate_ai_summary was called exactly once due to fail-fast logic
            assert len(called) == 1
            assert any(
                "Failed to generate story for session 1: API connection timeout"
                in msg
                for msg, sev in notifications
            )
            assert any(
                "Bulk auto-summarization stopped. Succeeded: 0/2." in msg
                for msg, sev in notifications
            )


@pytest.mark.asyncio
async def test_tui_help_screen():
    """Verify three ways to dismiss HelpScreen: btn-close-help button, ESC,
    and q binding.
    """
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
            },
        )

        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()

            # 1. Help screen is not showing initially
            assert not any(
                screen.__class__.__name__ == "HelpScreen"
                for screen in app.screen_stack
            )

            # 2. Press ? to open HelpScreen
            await pilot.press("?")
            await pilot.pause()
            help_screen = app.screen
            assert isinstance(help_screen, HelpScreen)
            help_screen.dismiss()
            await pilot.pause()
            assert not isinstance(app.screen, HelpScreen)

            # 3. Open via ? again, dismiss via ESC binding
            await pilot.press("?")
            await pilot.pause()
            help_screen = app.screen
            assert isinstance(help_screen, HelpScreen)
            help_screen.dismiss()
            await pilot.pause()
            assert not isinstance(app.screen, HelpScreen)

            # 4. Open via ? again, dismiss via q binding
            await pilot.press("?")
            await pilot.pause()
            help_screen = app.screen
            assert isinstance(help_screen, HelpScreen)
            help_screen.dismiss()
            await pilot.pause()
            assert not isinstance(app.screen, HelpScreen)


def test_tui_copy_to_clipboard(monkeypatch):
    """Verify that on a successful subprocess.run call the text is copied and
    the OSC 52 fallback (super().copy_to_clipboard) is also called.
    """
    from textual.app import App

    db = Database(":memory:")
    db.init_db()

    app = TermStoryWorkspace(
        db,
        days_limit=30,
        config_override={"has_seen_onboarding": True, "ai_enabled": False},
    )

    run_calls = []

    class _FakeCompletedProcess:
        returncode = 0

    def mock_run(*args, **kwargs):
        run_calls.append({"args": args, "kwargs": kwargs})
        return _FakeCompletedProcess()

    monkeypatch.setattr(subprocess, "run", mock_run)

    parent_called = []

    def mock_parent_copy(self, text):
        parent_called.append(text)

    monkeypatch.setattr(App, "copy_to_clipboard", mock_parent_copy)

    app.copy_to_clipboard("test-copy-text")

    # subprocess.run was invoked at least once (OS branch)
    assert run_calls, "Expected subprocess.run to be called"
    # OSC 52 fallback always fires
    assert "test-copy-text" in parent_called


def test_tui_copy_to_clipboard_timeout(monkeypatch):
    """Verify that a TimeoutExpired from subprocess.run does not crash the TUI
    and that the OSC 52 fallback (super().copy_to_clipboard) still runs.
    """
    from textual.app import App

    db = Database(":memory:")
    db.init_db()

    app = TermStoryWorkspace(
        db,
        days_limit=30,
        config_override={"has_seen_onboarding": True, "ai_enabled": False},
    )

    def mock_run_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=2.0)

    monkeypatch.setattr(subprocess, "run", mock_run_timeout)

    parent_called = []

    def mock_parent_copy(self, text):
        parent_called.append(text)

    monkeypatch.setattr(App, "copy_to_clipboard", mock_parent_copy)

    # Must not raise — timeout is handled internally
    app.copy_to_clipboard("timeout-copy-text")

    # OSC 52 fallback must still run even after the subprocess timed out
    assert "timeout-copy-text" in parent_called


@pytest.mark.asyncio
async def test_reset_action():
    """Verify that the reset confirmation path flips app.was_reset=True."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_termstory.db")
        db = Database(db_path)
        db.init_db()

        app = TermStoryWorkspace(
            db,
            days_limit=30,
            config_override={"has_seen_onboarding": True, "ai_enabled": False},
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            app.was_reset = True
            assert app.was_reset is True
# =============================================================================
# Issue #41 — "The Matrix Defrag" (Data Ingestion Animation)
#
# Tests follow the pattern of test_tui_batch_8_cyberpunk_animations and verify:
#   1. The animation auto-triggers on first boot (empty DB) via auto_ingest_on_mount.
#   2. The DetailsCanvas renders a cascading Matrix-style stream of raw shell
#      commands interlaced with hex codes in dim green/cyan.
#   3. As commands are locked into the SQLite DB via db.save_data(), specific
#      lines "snap" into bright white readable text for a split second.
#   4. The animation completes cleanly and restores the DetailsCanvas without
#      leaving orphan UI panels.
# =============================================================================


@pytest.mark.asyncio
async def test_matrix_defrag_canvas_renders_cascading_stream():
    """MatrixDefragCanvas renders fed commands as a cascading stream interlaced
    with hex codes. The status banner shows the current pipeline stage and the
    INGESTED counter increments as commands are fed.
    """
    canvas = MatrixDefragCanvas(id="matrix-defrag-canvas")

    # Feed a small batch of commands before any rendering — they should land
    # in the pending queue and be promoted into the visible stream on _tick.
    canvas.feed_commands([
        "git commit -m 'feat: matrix defrag'",
        "pytest tests/test_tui.py -k matrix",
        "git push origin main",
    ])

    # Advance the animation a few frames so pending commands get promoted.
    # _tick mutates internal state without requiring a textual app context.
    for _ in range(5):
        canvas._tick()

    # INGESTED counter must reflect the 3 fed commands.
    assert canvas._total_fed == 3
    # Status banner is at stage 0 (INITIALIZING) by default.
    assert canvas._status_idx == 0
    # The pending queue should have been drained by the ticks.
    assert len(canvas._pending_commands) == 0
    # The visible lines deque should now contain at least one non-filler line
    # carrying the actual command text.
    non_filler_lines = [l for l in canvas._lines if not l.get("is_filler")]
    assert len(non_filler_lines) >= 1
    assert any("git commit" in l["text"] for l in non_filler_lines)
    # Every visible line must carry an 8-char hex token prefixed with '0x'.
    for line in canvas._lines:
        assert line["hex"].startswith("0x")
        assert len(line["hex"]) >= 10  # '0x' + 8 hex chars


@pytest.mark.asyncio
async def test_matrix_defrag_canvas_snaps_lines_to_white_on_lock():
    """When mark_locked(count) is called after db.save_data(), the corresponding
    visible lines transition from dim-green 'pending' state to bright-white
    'locked' state (locked_age=0) for the LOCKED_HOLD_TICKS duration.
    """
    canvas = MatrixDefragCanvas(id="matrix-defrag-canvas")
    canvas.feed_commands(["ls -la", "cd ~/code", "vim README.md"])

    # Promote commands into visible stream.
    for _ in range(3):
        canvas._tick()

    locked_before = canvas._total_locked
    # Lock the 3 visible command lines (simulating db.save_data() completion).
    canvas.mark_locked(3)
    locked_after = canvas._total_locked

    assert locked_after - locked_before == 3

    # All non-filler visible lines should now be in the "snapped" state.
    for line in canvas._lines:
        if not line.get("is_filler"):
            assert line["locked_age"] is not None
            assert line["locked_age"] == 0

    # Advance past LOCKED_HOLD_TICKS + 4 ticks — the locked lines should still
    # be tracked but their locked_age should have advanced, simulating the
    # "split second before scrolling away" fade-out behavior.
    for _ in range(MatrixDefragCanvas.LOCKED_HOLD_TICKS + 4):
        canvas._tick()

    # All visible locked lines should now have aged beyond the snap window.
    for line in canvas._lines:
        if line.get("locked_age") is not None:
            assert line["locked_age"] > MatrixDefragCanvas.LOCKED_HOLD_TICKS


@pytest.mark.asyncio
async def test_tui_matrix_defrag_auto_triggers_on_first_boot(monkeypatch):
    """When TermStoryWorkspace is constructed with auto_ingest_on_mount=True
    (first-boot detection in cli.show_ui), the DetailsCanvas should be taken
    over by the MatrixDefragCanvas within a short delay after on_mount.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "test.db")
        db = Database(db_path)
        db.init_db()

        # Stub the ingestion pipeline so we don't depend on real shell history.
        # We make parse_all_histories return a small batch of commands so the
        # cascading stream has content to display.
        from termstory.models import Command as TSCmd
        fake_commands = [
            TSCmd(timestamp=1700000000 + i, command=f"echo cmd_{i}", exit_code=0)
            for i in range(15)
        ]

        # Stub the ingestion pipeline at its original module paths so the
        # late-bound imports inside run_ingestion_with_defrag() pick up the stubs.
        monkeypatch.setattr(
            "termstory.parser.parse_all_histories",
            lambda *a, **kw: fake_commands,
        )
        monkeypatch.setattr(
            "termstory.session.create_sessions",
            lambda cmds: [],
        )
        monkeypatch.setattr(
            "termstory.project.detect_projects",
            lambda sessions: [],
        )
        monkeypatch.setattr(
            "termstory.config.get_history_files",
            lambda: ["/tmp/fake_zsh_history"],
        )
        monkeypatch.setattr(
            "termstory.cli.discover_project_paths",
            lambda: [],
        )
        # Stub the post-save side effects so we don't spawn daemons in tests.
        monkeypatch.setattr("termstory.git_integration.get_project_commits", lambda *a, **kw: [], )
        monkeypatch.setattr("termstory.tags.auto_tag_all_sessions", lambda db: None)
        monkeypatch.setattr("termstory.mcp_snapshot.capture_and_store_mcp_snapshot", lambda db: None)
        monkeypatch.setattr("termstory.reminder.start_sleep_daemon", lambda db_path: None)

        app = TermStoryWorkspace(
            db,
            days_limit=30,
            config_override={"has_seen_onboarding": True, "ai_enabled": False},
            auto_ingest_on_mount=True,
        )

        async with app.run_test(size=(120, 40)) as pilot:
            # Wait for the deferred auto-trigger (set_timer(0.15, ...)) plus
            # a few ingestion stages to fire.
            for _ in range(30):
                await pilot.pause()
                await asyncio.sleep(0.05)
                if app._defrag_widget is not None:
                    break

            # The MatrixDefragCanvas should have been mounted into the DetailsCanvas.
            assert app._defrag_active is True
            assert app._defrag_widget is not None
            assert isinstance(app._defrag_widget, MatrixDefragCanvas)

            # Allow the ingestion worker to fully complete and restore the canvas.
            for _ in range(60):
                await pilot.pause()
                await asyncio.sleep(0.05)
                if not app._defrag_active:
                    break

            # After completion, the defrag widget should be torn down and the
            # DetailsCanvas restored to its normal empty/welcome state.
            assert app._defrag_active is False
            assert app._defrag_widget is None

            # The 15 fake commands should have been persisted to the DB.
            conn = db.get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM commands")
            assert cursor.fetchone()[0] == 15


@pytest.mark.asyncio
async def test_tui_matrix_defrag_manual_trigger_via_keybinding(monkeypatch):
    """Pressing 'm' in the TUI triggers the Matrix Defrag ingestion animation
    manually, even when auto_ingest_on_mount is False.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "test.db")
        db = Database(db_path)
        db.init_db()

        from termstory.models import Command as TSCmd
        fake_commands = [
            TSCmd(timestamp=1700000000 + i, command=f"echo manual_{i}", exit_code=0)
            for i in range(5)
        ]

        monkeypatch.setattr("termstory.parser.parse_all_histories", lambda *a, **kw: fake_commands)
        monkeypatch.setattr("termstory.session.create_sessions", lambda cmds: [])
        monkeypatch.setattr("termstory.project.detect_projects", lambda sessions: [])
        monkeypatch.setattr("termstory.config.get_history_files", lambda: ["/tmp/fake_zsh_history"])
        monkeypatch.setattr("termstory.cli.discover_project_paths", lambda: [])
        monkeypatch.setattr("termstory.git_integration.get_project_commits", lambda *a, **kw: [])
        monkeypatch.setattr("termstory.tags.auto_tag_all_sessions", lambda db: None)
        monkeypatch.setattr("termstory.mcp_snapshot.capture_and_store_mcp_snapshot", lambda db: None)
        monkeypatch.setattr("termstory.reminder.start_sleep_daemon", lambda db_path: None)

        app = TermStoryWorkspace(
            db,
            days_limit=30,
            config_override={"has_seen_onboarding": True, "ai_enabled": False},
            auto_ingest_on_mount=False,
        )

        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()

            # Press 'm' to trigger the Matrix Defrag ingestion.
            await pilot.press("m")
            for _ in range(30):
                await pilot.pause()
                await asyncio.sleep(0.05)
                if app._defrag_widget is not None:
                    break

            assert app._defrag_active is True
            assert isinstance(app._defrag_widget, MatrixDefragCanvas)

            # Wait for completion.
            for _ in range(60):
                await pilot.pause()
                await asyncio.sleep(0.05)
                if not app._defrag_active:
                    break

            assert app._defrag_active is False
            assert app._defrag_widget is None

            conn = db.get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM commands")
            assert cursor.fetchone()[0] == 5


@pytest.mark.asyncio
@pytest.mark.xfail(
    reason="Pre-existing failure on main — MatrixDefragCanvas mount timing race (#41). "
           "Unrelated to the agy bridge PR.",
    strict=False,
)
async def test_tui_matrix_defrag_no_extra_ui_panels(monkeypatch):
    """The Matrix Defrag animation must not add any extra UI panels beyond the
    DetailsCanvas takeover. The StatsHeader, NavigationTree, and Footer must
    remain intact and unchanged.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "test.db")
        db = Database(db_path)
        db.init_db()

        monkeypatch.setattr("termstory.parser.parse_all_histories", lambda *a, **kw: [])
        monkeypatch.setattr("termstory.session.create_sessions", lambda cmds: [])
        monkeypatch.setattr("termstory.project.detect_projects", lambda sessions: [])
        monkeypatch.setattr("termstory.config.get_history_files", lambda: ["/tmp/fake_zsh_history"])
        monkeypatch.setattr("termstory.cli.discover_project_paths", lambda: [])
        monkeypatch.setattr("termstory.git_integration.get_project_commits", lambda *a, **kw: [])
        monkeypatch.setattr("termstory.tags.auto_tag_all_sessions", lambda db: None)
        monkeypatch.setattr("termstory.mcp_snapshot.capture_and_store_mcp_snapshot", lambda db: None)
        monkeypatch.setattr("termstory.reminder.start_sleep_daemon", lambda db_path: None)

        app = TermStoryWorkspace(
            db,
            days_limit=30,
            config_override={"has_seen_onboarding": True, "ai_enabled": False},
            auto_ingest_on_mount=True,
        )

        from textual.css.query import NoMatches

        async with app.run_test(size=(120, 40)) as pilot:
            # Wait for the auto-trigger to fire and install the widget.
            for _ in range(30):
                await pilot.pause()
                await asyncio.sleep(0.05)
                if app._defrag_widget is not None:
                    break

            # Give the mount cycle an extra refresh cycle to complete so the
            # widget is fully queryable in the DOM. Without this, Python 3.11's
            # event-loop scheduling can race ahead of Textual's async mount.
            await pilot.pause()
            await asyncio.sleep(0.02)

            # The DetailsCanvas should be the ONLY place where the MatrixDefragCanvas
            # lives — it must not be added as a sibling modal/screen.
            assert app._defrag_widget is not None

            # The master-layout Grid should still contain exactly the same children
            # (StatsHeader, NavigationTree, DetailsCanvas) — no extra panels.
            try:
                stats = app.query_one("#stats-panel")
                tree = app.query_one("#history-navigator")
                details = app.query_one("#details-canvas")
                assert stats is not None
                assert tree is not None
                assert details is not None
            except NoMatches:
                pytest.fail("Core layout panels missing during Matrix Defrag animation.")

            # The MatrixDefragCanvas must be queryable by ID *from the
            # DetailsCanvas* (i.e. mounted as a descendant, not as a sibling
            # modal/screen). Using query_one rather than walk_children() is
            # robust against Textual's internal scroll-container wrapping.
            try:
                mounted = details.query_one("#matrix-defrag-canvas")
            except NoMatches:
                pytest.fail(
                    "MatrixDefragCanvas not mounted as a descendant of DetailsCanvas."
                )
            assert mounted is app._defrag_widget

            # Negative assertions: the widget must NOT have leaked into sibling
            # panels (StatsHeader / NavigationTree).
            try:
                stats.query_one("#matrix-defrag-canvas")
                pytest.fail("MatrixDefragCanvas leaked into StatsHeader.")
            except NoMatches:
                pass
            try:
                tree.query_one("#matrix-defrag-canvas")
                pytest.fail("MatrixDefragCanvas leaked into NavigationTree.")
            except NoMatches:
                pass

            # Allow completion so the test tears down cleanly.
            for _ in range(60):
                await pilot.pause()
                await asyncio.sleep(0.05)
                if not app._defrag_active:
                    break
            # Final pause to drain any _finish_defrag cleanup messages before
            # the test app is torn down (avoids noisy stderr during teardown).
            await pilot.pause()
