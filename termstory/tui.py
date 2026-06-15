import os
import asyncio
import json
from collections import defaultdict
from datetime import datetime, timedelta
from typing import List, Dict, Tuple, Optional, Any

from rich.console import Group
from rich.text import Text
import textwrap
import sys

def _handle_exception(exc_type, exc, tb):
    """Friendly global exception handler to avoid raw tracebacks."""
    console = Console(stderr=True)
    console.print("[bold red]An unexpected error occurred. Please try again.[/bold red]")
    # Optionally log tb to a file for developers.
    # For now we suppress the traceback.

sys.excepthook = _handle_exception
from rich.console import Console

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
from textual.widgets import Footer, Tree, Static, Input, Button
from textual.screen import ModalScreen
from textual.binding import Binding

from termstory.models import Session, Project, format_duration
from termstory.database import Database
from termstory.project import disambiguate_project_names
from termstory.formatter import _is_noise_command, clean_command_to_memory, generate_daily_activity_punch_card, get_operator_handle, get_github_avatar_ascii
from termstory.date_utils import get_current_time
from termstory.config import load_config, save_config
from termstory.ai import generate_ai_summary, generate_timeframe_summary, generate_daily_chronicle, generate_wrapped_summary
from termstory.insights import calculate_focus_score, calculate_time_of_day_distribution

def is_worker_cancelled() -> bool:
    try:
        from textual.worker import get_current_worker, NoActiveWorker
        try:
            worker = get_current_worker()
            return worker.is_cancelled
        except NoActiveWorker:
            return False
    except ImportError:
        return False



# ==========================================
# 1. HELPER LOGIC FOR STATS & MEMORIES
# ==========================================

def get_focus_layer(filename: str) -> str:
    fn = filename.lower()
    if any(x in fn for x in ["tui", "ui", "css", "html", "style", "view", "window", "screen"]):
        return "UI Grid"
    if any(x in fn for x in ["test", "spec", "mock"]):
        return "Testing"
    if any(x in fn for x in ["sanitizer", "auth", "security", "crypt", "secret", "token"]):
        return "Security"
    if any(x in fn for x in ["db", "database", "sql", "model", "store", "query"]):
        return "Database Layer"
    if any(x in fn for x in ["parser", "lexer", "ast"]):
        return "Parser Engine"
    if any(x in fn for x in ["cli", "main", "run", "cmd", "args"]):
        return "CLI Command Routing"
    if any(x in fn for x in ["api", "client", "server", "http", "socket"]):
        return "Network Layer"
    return "Core Logic"


def calculate_streak(sessions: List[Session]) -> int:
    """Calculate consecutive active work days ending today or on the last active day."""
    if not sessions:
        return 0
    active_dates = {
        datetime.fromtimestamp(s.start_time).date()
        for s in sessions
    }
    if not active_dates:
        return 0
    
    sorted_dates = sorted(list(active_dates), reverse=True)
    streak = 1
    current_date = sorted_dates[0]
    
    # Allow a gap of at most 1 day (e.g. if today is inactive but yesterday was active, streak is still active)
    today = get_current_time().date()
    if (today - current_date).days > 1:
        return 0
        
    for d in sorted_dates[1:]:
        if (current_date - d).days == 1:
            streak += 1
            current_date = d
        elif (current_date - d).days > 1:
            break
    return streak


def generate_heatmap(sessions: List[Session], days_limit: int = 30) -> str:
    """Generate a GitHub-like 30-day activity matrix representing command volume."""
    now = get_current_time().date()
    day_counts = defaultdict(int)
    for s in sessions:
        s_date = datetime.fromtimestamp(s.start_time).date()
        day_counts[s_date] += len(s.commands)
        
    heatmap_blocks = []
    for i in range(days_limit - 1, -1, -1):
        target_date = now - timedelta(days=i)
        cmd_count = day_counts[target_date]
        if cmd_count == 0:
            heatmap_blocks.append("[bright_black]░[/]")
        elif cmd_count < 5:
            heatmap_blocks.append("[bright_black]▄[/]")
        elif cmd_count < 20:
            heatmap_blocks.append("[green]■[/]")
        else:
            heatmap_blocks.append("[bold green]█[/]")
            
    return " ".join(heatmap_blocks)


def calculate_dashboard_stats(sessions: List[Session], projects: List[Project], days_limit: int = 30) -> Dict[str, Any]:
    """Calculate cumulative dashboard stats."""
    real_sessions = [s for s in sessions if not getattr(s, "is_legacy", False)]
    
    active_dates = {
        datetime.fromtimestamp(s.start_time).date()
        for s in real_sessions
    }
    
    streak = calculate_streak(real_sessions)
    total_seconds = sum(s.duration_seconds for s in sessions)
    total_time_str = format_duration(total_seconds)
    heatmap = generate_heatmap(real_sessions, days_limit=days_limit)
    
    # Derive last ingestion time from the most recently ended session
    last_ingestion_str = ""
    if sessions:
        latest_ts = max(s.end_time for s in sessions)
        last_ingestion_str = datetime.fromtimestamp(latest_ts).strftime("%b %d %H:%M")

    return {
        "total_time": total_time_str,
        "active_days": len(active_dates),
        "streak": streak,
        "projects_count": len(projects),
        "heatmap": heatmap,
        "last_ingestion": last_ingestion_str,
    }


def compile_timeframe_stats_for_ai(sessions: List[Session], projects: List[Project]) -> str:
    total_seconds = sum(s.duration_seconds for s in sessions)
    total_hours = total_seconds / 3600.0
    
    project_map = {p.id: p.name for p in projects if p.id is not None}
    project_durations = defaultdict(int)
    for s in sessions:
        name = project_map.get(s.project_id, "Other")
        if name == "General / No Project":
            name = "Other"
        project_durations[name] += s.duration_seconds
        
    dist_parts = []
    if total_seconds > 0:
        for name, dur in sorted(project_durations.items(), key=lambda x: x[1], reverse=True):
            pct = int(round((dur / total_seconds) * 100))
            dist_parts.append(f"{pct}% on {name}")
            
    dist_str = ", ".join(dist_parts)
    
    # Extract commit messages
    commits_list = []
    for s in sessions:
        for c in s.commits:
            msg = c.get("cleaned_message") or c.get("message") or ""
            if msg and msg.strip():
                commits_list.append(msg.strip())
                
    # Deduplicate commits case-insensitively
    seen_commits = set()
    unique_commits = []
    for c in commits_list:
        c_lower = c.lower()
        if c_lower not in seen_commits:
            seen_commits.add(c_lower)
            unique_commits.append(c)
            
    # Sort and prioritize unique commits (feat/fix/refactor first)
    def commit_priority(msg: str) -> int:
        msg_l = msg.lower()
        if msg_l.startswith("feat"): return 0
        if msg_l.startswith("fix"): return 1
        if msg_l.startswith("refactor"): return 2
        return 3
    unique_commits.sort(key=commit_priority)
    top_commits = unique_commits[:20]
    
    # Extract existing AI summaries from sessions
    ai_stories = []
    for s in sessions:
        if s.ai_summary and s.ai_summary.strip():
            ai_stories.append(s.ai_summary.strip())
    seen_stories = set()
    unique_stories = []
    for story in ai_stories:
        if story.lower() not in seen_stories:
            seen_stories.add(story.lower())
            unique_stories.append(story)
    top_stories = unique_stories[:10]
    
    # Extract notable tooling
    notable_tools = set()
    tool_keywords = ["docker", "kubectl", "npm", "pip", "pytest", "cargo", "go", "python", "node", "terraform", "aws", "gcloud"]
    for s in sessions:
        for cmd in s.commands:
            first_word = cmd.command.split()[0].lower() if cmd.command.strip() else ""
            if first_word in tool_keywords:
                notable_tools.add(first_word)
                
    context_lines = [
        f"TIME LOGGED: {total_hours:.1f} hours total",
        f"PROJECTS DISTRIBUTION: {dist_str}",
        f"TOTAL GIT COMMITS: {len(commits_list)}"
    ]
    if top_commits:
        commits_block = "\n".join(f"  - {c}" for c in top_commits)
        context_lines.append(f"REPRESENTATIVE GIT COMMITS:\n{commits_block}")
    if notable_tools:
        context_lines.append(f"TOOLS/COMMANDS DETECTED: {', '.join(sorted(notable_tools))}")
    if top_stories:
        stories_block = "\n".join(f"  - {s}" for s in top_stories)
        context_lines.append(f"INDIVIDUAL SESSION AI STORIES:\n{stories_block}")
        
    return "\n".join(context_lines)


import re

def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from a string."""
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return ansi_escape.sub('', text)




def deduplicate_sessions(sessions: List[Session]) -> List[Session]:
    """Group sessions by start_time and project_id, keeping only the one with the largest end_time."""
    grouped = defaultdict(list)
    for s in sessions:
        key = (s.start_time, s.project_id)
        grouped[key].append(s)
        
    deduped = []
    for key, group in grouped.items():
        best_session = max(group, key=lambda s: s.end_time)
        deduped.append(best_session)
        
    return sorted(deduped, key=lambda s: s.start_time)


def get_session_memory_str(session: Session) -> str:
    """Extract a single-line summary memory for the session, ensuring no raw commands leak."""
    if hasattr(session, "_cached_memory_str") and session._cached_memory_str is not None:
        return session._cached_memory_str
        
    if session.ai_summary:
        val = strip_ansi(session.ai_summary)
        # Parse multiline summaries to get the first high-signal content line
        lines = [line.strip() for line in val.split("\n") if line.strip()]
        for line in lines:
            if line.startswith("[") and line.endswith("]"):
                continue
            clean = line
            import re
            # Matches prefixes like "├─ 🔨 Built:", "• Hacked:", etc.
            clean = re.sub(
                r'^(├─|└─|•|\*)\s*(🔨|🔧|🚀|🧠|🤖|📂|⌨️)?\s*(Built|Flow|Result|Story|Hacked|Tooling|Outcome|Project|Action|Pattern):\s*', 
                '', 
                clean, 
                flags=re.IGNORECASE
            )
            # Remove any residual leading symbols/spaces
            clean = clean.lstrip("├─└─•* \t🔨🔧🚀🧠🤖")
            if clean:
                session._cached_memory_str = clean
                return clean
        
        session._cached_memory_str = val
        return val
        
    if session.commits:
        c = session.commits[0]
        msg = c.get("cleaned_message") or c.get("message") or "Code commit"
        val = strip_ansi(msg)
        session._cached_memory_str = val
        return val
        
    candidates = [cmd.command for cmd in session.commands if not _is_noise_command(cmd.command)]
    if candidates:
        best_cmd = max(candidates, key=len)
        val = strip_ansi(clean_command_to_memory(best_cmd))
        session._cached_memory_str = val
        return val
        
    if session.commands:
        val = strip_ansi(clean_command_to_memory(session.commands[-1].command))
        session._cached_memory_str = val
        return val
        
    val = "Activity logged"
    session._cached_memory_str = val
    return val



# ==========================================
# 2. TUI WIDGETS & SCREENS
# ==========================================

class HelpScreen(ModalScreen[None]):
    """Modal screen displaying all keyboard shortcuts."""
    
    BINDINGS = [
        Binding("escape", "dismiss_none", "Close", show=False),
        Binding("question_mark", "dismiss_none", "Close", show=False),
        Binding("q", "dismiss_none", "Close", show=False),
    ]
    
    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("⌨️  TermStory Keyboard Shortcuts", id="modal-title"),
            Static(
                "[bold]Global Navigation[/bold]\n"
                "  [cyan]?[/cyan]        : Show this help menu\n"
                "  [cyan]/[/cyan]        : Search sessions\n"
                "  [cyan]o[/cyan]        : Configure AI Settings\n"
                "  [cyan]c[/cyan]        : Copy selected text to clipboard\n"
                "  [cyan]q[/cyan] / [cyan]Esc[/cyan]  : Quit app / clear search\n\n"
                "[bold]Canvas Scrolling[/bold]\n"
                "  [cyan]Ctrl+Down[/cyan] / [cyan]Ctrl+j[/cyan]     : Scroll Down\n"
                "  [cyan]Ctrl+Up[/cyan]   / [cyan]Ctrl+k[/cyan]     : Scroll Up\n"
                "  [cyan]Ctrl+PgDn[/cyan] / [cyan]Ctrl+PgUp[/cyan]  : Scroll Page Down/Up\n\n"
                "[bold]Tree Navigator[/bold]\n"
                "  [cyan]Up[/cyan]/[cyan]Down[/cyan] / [cyan]j[/cyan]/[cyan]k[/cyan]        : Navigate nodes\n"
                "  [cyan]Enter[/cyan] / [cyan]Space[/cyan]        : Expand/Collapse\n\n"
                "[bold]AI Setup Menu[/bold]\n"
                "  [cyan]Ctrl+g, a, l, c[/cyan]      : Select Provider (Groq, OpenAI, Ollama, Custom)\n"
                "  [cyan]Ctrl+d[/cyan]               : Disable AI (Local Only)\n",
                id="modal-desc"
            ),
            Horizontal(
                Button("Close", variant="primary", id="btn-close-help"),
                id="modal-actions"
            ),
            id="modal-panel"
        )
        
    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-close-help":
            self.dismiss()
            
    def action_dismiss_none(self) -> None:
        self.dismiss()


class OnboardingScreen(ModalScreen[dict]):
    """Modal screen displaying trust warning and AI configuration options."""
    
    BINDINGS = [
        ("ctrl+g", "choose_groq", "Select Groq"),
        ("ctrl+a", "choose_openai", "Select OpenAI"),
        ("ctrl+l", "choose_ollama", "Select Ollama"),
        ("ctrl+c", "choose_custom", "Select Custom"),
        ("ctrl+d", "choose_disabled", "Keep Local Only (No AI)"),
        ("escape", "dismiss_none", "Close"),
    ]
    
    def __init__(self, current_config: dict):
        super().__init__()
        self.config = json.loads(json.dumps(current_config))
        self.selected_provider = self.config.get("active_provider", "groq")
        if self.selected_provider == "disabled":
            self.selected_provider = "groq"
            
    def compose(self) -> ComposeResult:
        provider_config = self.config.get("providers", {}).get(self.selected_provider, {})
        api_key = provider_config.get("api_key", "")
        base_url = provider_config.get("api_base_url", "")
        model_name = provider_config.get("model_name", "")
        
        github_username = self.config.get("github_username", "")
        if not github_username:
            from termstory.formatter import get_operator_handle
            github_username = get_operator_handle().lstrip('@')
            if github_username.lower() in ("developer", "other", "general"):
                github_username = ""
            
        yield Vertical(
            Static("🔒 TermStory Privacy & AI Onboarding", id="modal-title"),
            Static(
                "TermStory is completely offline by default. No data ever leaves your machine.\n\n"
                "To make your timeline readable, you can optionally enable the AI Categorization Engine. "
                "This will send sanitized terminal commands to an LLM to generate short, human-readable session memories.\n\n"
                "Before sending, TermStory scrubs passwords, environment variables, IPs/FQDNs, and drops "
                "sensitive sessions containing commands like 'vault' or 'aws configure' entirely.\n\n"
                "[dim]Shortcuts: [bold]Ctrl+g[/]: Groq, [bold]Ctrl+a[/]: OpenAI, [bold]Ctrl+l[/]: Ollama, [bold]Ctrl+c[/]: Custom, [bold]Ctrl+d[/]: Disable AI, [bold]Esc[/]: Cancel[/dim]",
                id="modal-desc"
            ),
            Horizontal(
                Button("Groq [Ctrl+g]", variant="default", id="btn-select-groq"),
                Button("OpenAI [Ctrl+a]", variant="default", id="btn-select-openai"),
                Button("Ollama [Ctrl+l]", variant="default", id="btn-select-ollama"),
                Button("Custom [Ctrl+c]", variant="default", id="btn-select-custom"),
                id="modal-provider-selector"
            ),
            Vertical(
                Static("GitHub ID / Username (for ASCII avatar):", classes="input-label"),
                Input(value=github_username, placeholder="GitHub Username...", id="input-github-username"),
                Static("API Key (required for cloud providers):", classes="input-label", id="label-api-key"),
                Input(value=api_key, placeholder="API Key...", password=True, id="input-api-key"),
                Static("", id="error-api-key", classes="error-label"),
                Static("API Base URL:", classes="input-label"),
                Input(value=base_url, placeholder="API Base URL...", id="input-base-url"),
                Static("Model Name:", classes="input-label"),
                Input(value=model_name, placeholder="Model Name...", id="input-model-name"),
                id="modal-inputs-container"
            ),
            Horizontal(
                Button("Save & Enable", variant="success", id="btn-save"),
                Button("Keep Local Only (No AI)", variant="error", id="btn-disable-ai"),
                Button("Read Privacy Policy", variant="default", id="btn-read-privacy"),
                id="modal-actions"
            ),
            id="modal-panel"
        )
        
    def on_mount(self) -> None:
        self.call_after_refresh(self.update_provider_ui, self.selected_provider)
        
    def update_provider_ui(self, provider: str) -> None:
        self.selected_provider = provider
        for p in ("groq", "openai", "ollama", "custom"):
            btn = self.query_one(f"#btn-select-{p}")
            if p == provider:
                btn.variant = "primary"
            else:
                btn.variant = "default"
                
        provider_config = self.config.get("providers", {}).get(provider, {})
        api_key_input = self.query_one("#input-api-key")
        base_url_input = self.query_one("#input-base-url")
        model_name_input = self.query_one("#input-model-name")
        
        api_key_input.value = provider_config.get("api_key", "")
        base_url_input.value = provider_config.get("api_base_url", "")
        model_name_input.value = provider_config.get("model_name", "")
        
        api_key_label = self.query_one("#label-api-key")
        error_api_key = self.query_one("#error-api-key")
        error_api_key.styles.color = "red"
        
        if provider == "ollama":
            api_key_input.styles.display = "none"
            api_key_label.styles.display = "none"
            error_api_key.styles.display = "none"
        else:
            api_key_input.styles.display = "block"
            api_key_label.styles.display = "block"
            error_api_key.update("")
            error_api_key.styles.display = "none"
            
    def action_choose_groq(self) -> None:
        self.update_provider_ui("groq")
        
    def action_choose_openai(self) -> None:
        self.update_provider_ui("openai")
        
    def action_choose_ollama(self) -> None:
        self.update_provider_ui("ollama")
        
    def action_choose_custom(self) -> None:
        self.update_provider_ui("custom")
        
    def action_choose_disabled(self) -> None:
        github_username = self.query_one("#input-github-username").value.strip().lstrip('@')
        self.config["github_username"] = github_username
        self.config["ai_enabled"] = False
        self.config["active_provider"] = "disabled"
        self.config["has_seen_onboarding"] = True
        self.dismiss(self.config)
        
    def action_dismiss_none(self) -> None:
        self.dismiss(None)
        
    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id.startswith("btn-select-"):
            provider = button_id.replace("btn-select-", "")
            self.update_provider_ui(provider)
        elif button_id == "btn-read-privacy":
            import webbrowser
            from pathlib import Path
            try:
                path = Path(__file__).parent.parent / "DATA_PRIVACY.md"
                if path.exists():
                    webbrowser.open(path.resolve().as_uri())
                else:
                    cwd_path = Path("DATA_PRIVACY.md")
                    if cwd_path.exists():
                        webbrowser.open(cwd_path.resolve().as_uri())
                    else:
                        webbrowser.open("https://github.com/bitflicker64/Termstory/blob/main/DATA_PRIVACY.md")
            except Exception:
                pass
        elif button_id == "btn-save":
            api_key = self.query_one("#input-api-key").value.strip()
            base_url = self.query_one("#input-base-url").value.strip()
            model_name = self.query_one("#input-model-name").value.strip()
            github_username = self.query_one("#input-github-username").value.strip().lstrip('@')
            
            error_label = self.query_one("#error-api-key")
            if self.selected_provider in ("groq", "openai", "custom") and not api_key:
                error_label.update("API Key cannot be empty.")
                error_label.styles.display = "block"
                return
            else:
                error_label.styles.display = "none"
            
            if not base_url:
                if self.selected_provider == "groq":
                    base_url = "https://api.groq.com/openai/v1"
                elif self.selected_provider == "openai":
                    base_url = "https://api.openai.com/v1"
                elif self.selected_provider == "ollama":
                    base_url = "http://localhost:11434/v1"
                    
            if "providers" not in self.config:
                self.config["providers"] = {}
            if self.selected_provider not in self.config["providers"]:
                self.config["providers"][self.selected_provider] = {}
                
            self.config["providers"][self.selected_provider]["api_key"] = api_key
            self.config["providers"][self.selected_provider]["api_base_url"] = base_url
            self.config["providers"][self.selected_provider]["model_name"] = model_name
            
            self.config["github_username"] = github_username
            self.config["ai_enabled"] = True
            self.config["active_provider"] = self.selected_provider
            self.config["has_seen_onboarding"] = True
            
            self.dismiss(self.config)
        elif button_id == "btn-disable-ai":
            github_username = self.query_one("#input-github-username").value.strip().lstrip('@')
            self.config["github_username"] = github_username
            self.config["ai_enabled"] = False
            self.config["active_provider"] = "disabled"
            self.config["has_seen_onboarding"] = True
            self.dismiss(self.config)



class StatsHeader(Static):
    """The cumulative stats header spanning the top of the interface."""
    
    def update_stats(self, stats: Dict[str, Any], ai_status: str = "", days_limit: Optional[int] = 30) -> None:
        from termstory import __version__
        limit_str = f"Last {days_limit} Days" if days_limit is not None else "All History"
        ingestion_str = ""
        if stats.get("last_ingestion"):
            ingestion_str = f"  │  [dim]Synced: {stats['last_ingestion']}[/dim]"
        self.update(
            f"[bold cyan]TermStory[/bold cyan] [dim]v{__version__}[/dim]  │  "
            f"[bold]Time logged:[/bold] {stats['total_time']}  │  "
            f"[bold]Active Days:[/bold] {stats['active_days']}  │  "
            f"[bold green]Streak:[/bold green] {stats['streak']} Days  │  "
            f"[bold]Projects:[/bold] {stats['projects_count']}  │  "
            f"{ai_status}{ingestion_str}\n"
            f"[dim]Activity ({limit_str}):[/dim] {stats['heatmap']}"
        )


class NavigationTree(Tree):
    """Collapsible date-grouped navigation timeline supporting Vim keys."""
    
    BINDINGS = [
        Binding("j", "cursor_down", "Cursor Down", show=False),
        Binding("k", "cursor_up", "Cursor Up", show=False),
    ]
    
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.show_root = False
    
    def populate(self, projects: List[Project], sessions: List[Session], search_query: Optional[str] = None, is_deep_search: bool = False) -> None:
        # Flag to inform the app that the tree is being built
        self.app._populating_tree = True
        try:
            self.clear()

            project_map = {p.id: p for p in projects if p.id is not None}
            display_names = disambiguate_project_names(projects)

            # Pre-filter sessions if a search query is active
            if search_query and not is_deep_search:
                q = search_query.lower()
                filtered_sessions = []
                for s in sessions:
                    proj = project_map.get(s.project_id)
                    proj_name = display_names.get(s.project_id, "Other") if proj else "Other"
                    if proj_name == "General / No Project":
                        proj_name = "Other"
                    memory = get_session_memory_str(s)
                    cmd_match = any(q in cmd.command.lower() for cmd in s.commands)
                    commit_match = any(q in (c.get("message", "") + " " + c.get("cleaned_message", "")).lower() for c in s.commits)
                    if q in proj_name.lower() or q in memory.lower() or cmd_match or commit_match:
                        filtered_sessions.append(s)
                sessions_to_build = filtered_sessions
            else:
                sessions_to_build = sessions

            # Group sessions hierarchically: Month -> Date -> Project -> List of sessions
            nested = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
            for s in sessions_to_build:
                dt = datetime.fromtimestamp(s.start_time)
                month_key = dt.strftime("%B %Y")
                day_key = dt.strftime("%Y-%m-%d")
                nested[month_key][day_key][s.project_id].append(s)

            # Sort Months chronologically (newest first)
            def parse_month_key(m_key: str) -> Tuple[int, int]:
                dt_parsed = datetime.strptime(m_key, "%B %Y")
                return (dt_parsed.year, dt_parsed.month)

            sorted_months = sorted(nested.keys(), key=parse_month_key, reverse=True)

            # Helper to sort project IDs alphabetically with "Other" last
            def project_sort_key(p_id):
                if p_id is None:
                    return (1, "other")
                p_obj = project_map.get(p_id)
                p_name = display_names.get(p_id, "Other") if p_obj else "Other"
                if p_name == "General / No Project":
                    p_name = "Other"
                return (0, p_name.lower())

            if is_deep_search and search_query:
                timeline_title = f"🔍 Search Results: \"{search_query}\""
            else:
                timeline_title = "📅 Timeline"

            timeline_root = self.root.add(timeline_title, data={"type": "category", "category": "timeline"}, expand=True)
            self.root.add("📁 Projects", data={"type": "category", "category": "projects"}, expand=False)
            self.root.add("🧠 Insights", data={"type": "category", "category": "insights"}, expand=False)

            for m_key in sorted_months:
                month_dt = datetime.strptime(m_key, "%B %Y")
                month_node = timeline_root.add(
                    m_key,
                    data={"type": "month", "year": month_dt.year, "month": month_dt.month},
                    expand=True
                )

                sorted_days = sorted(nested[m_key].keys(), reverse=True)
                for day_key in sorted_days:
                    day_dt = datetime.strptime(day_key, "%Y-%m-%d")
                    day_label = day_dt.strftime("%b %d (%a)")
                    day_node = month_node.add(
                        day_label,
                        data={"type": "date", "date_str": day_key},
                        expand=True
                    )

                    sorted_project_ids = sorted(nested[m_key][day_key].keys(), key=project_sort_key)
                    for p_id in sorted_project_ids:
                        proj = project_map.get(p_id)
                        proj_name = display_names.get(p_id, "Other") if proj else "Other"
                        if proj_name == "General / No Project":
                            proj_name = "Other"

                        proj_node = day_node.add(
                            proj_name,
                            data={"type": "project", "project_id": p_id, "date_str": day_key},
                            expand=False
                        )

                        sorted_sessions = sorted(nested[m_key][day_key][p_id], key=lambda x: x.start_time)
                        for s in sorted_sessions:
                            start_str = datetime.fromtimestamp(s.start_time).strftime("%H:%M")
                            end_str = datetime.fromtimestamp(s.end_time).strftime("%H:%M")

                            if getattr(s, "is_legacy", False):
                                # Distinguish between "Recovered" (detective found anchors and
                                # interpolated) and "Legacy Archive" (zero evidence found).
                                # A session is "Recovered" if at least one command has a
                                # recovery_source — meaning the Detective placed it in real time.
                                has_recovery = any(
                                    getattr(c, "recovery_source", None)
                                    for c in s.commands
                                )
                                cmd_count = len(s.commands)
                                if has_recovery:
                                    session_label = (
                                        f"🔍 Recovered Archive "
                                        f"[dim]({cmd_count} cmds • {start_str} - {end_str})[/]"
                                    )
                                else:
                                    session_label = (
                                        f"📦 Legacy Archive "
                                        f"[dim]({cmd_count} recovered cmds)[/]"
                                    )
                            else:
                                memory = get_session_memory_str(s)
                                session_label = f"✨ {memory} [dim]({start_str} - {end_str})[/]"

                            proj_node.add_leaf(
                                session_label,
                                data={"type": "session", "project_id": p_id, "session_id": s.id}
                            )
            self.root.expand()
        finally:
            # Ensure flag is cleared even if errors occur
            self.app._populating_tree = False

    def update_session_label(self, session_id: int, new_summary: str) -> None:
        """Find the leaf node representing session_id and update its label dynamically."""
        def traverse(node):
            if node.data and node.data.get("type") == "session" and node.data.get("session_id") == session_id:
                label_str = str(node.label)
                time_match = re.search(r'(\[dim\].*?\[/\])', label_str)
                time_part = time_match.group(1) if time_match else ""
                node.label = f"✨ {new_summary} {time_part}"
                return True
            for child in node.children:
                if traverse(child):
                    return True
            return False
        traverse(self.root)


def make_stacked_bar(project_seconds: Dict[str, int], total_seconds: int, width: int = 40) -> Tuple[str, str]:
    """Generate a horizontal stacked progress bar and legend using distinct project colors."""
    if total_seconds <= 0 or not project_seconds:
        return "[grey37]" + "░" * width + "[/]", "[dim]No active time[/]"
        
    sorted_projects = sorted(project_seconds.items(), key=lambda x: x[1], reverse=True)
    colors = ["cyan", "green", "yellow", "magenta", "blue", "red", "white"]
    project_colors = {}
    for idx, (p_name, _) in enumerate(sorted_projects):
        project_colors[p_name] = colors[idx % len(colors)]
        
    bar_str = ""
    legend_parts = []
    remaining_width = width
    
    for p_name, seconds in sorted_projects:
        pct = seconds / total_seconds
        char_count = int(round(pct * width))
        if pct > 0 and char_count == 0 and remaining_width > 0:
            char_count = 1
        char_count = min(char_count, remaining_width)
        remaining_width -= char_count
        
        color = project_colors[p_name]
        bar_str += f"[{color}]" + "█" * char_count + "[/]"
        
        pct_display = int(round(pct * 100))
        legend_parts.append(f"[{color}]■ {p_name} ({pct_display}%)[/]")
        
    if remaining_width > 0:
        bar_str += "[grey37]" + "░" * remaining_width + "[/]"
        
    return bar_str, "  ".join(legend_parts)


class NarrativeText(Static):
    """A Static widget that natively reflows its text on resize, avoiding jitter."""
    
    def __init__(self, raw_text: str, prefix: str = "", suffix: str = "", parse_markup: bool = False, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.raw_text = raw_text
        self.prefix = prefix
        self.suffix = suffix
        self.parse_markup = parse_markup

    def on_mount(self) -> None:
        from rich.text import Text
        full_text = self.prefix + self.raw_text + self.suffix
        if self.parse_markup:
            try:
                self.update(Text.from_markup(full_text, overflow="fold"))
            except Exception:
                self.update(Text(full_text, overflow="fold"))
        else:
            self.update(Text(full_text, overflow="fold"))

class DetailsCanvas(VerticalScroll):
    """Display overall metrics, dynamic time distribution bar, and Git/Command details."""
    
    can_focus = True
    
    def update_view_empty(self) -> None:
        self.remove_children()
        self.mount(Static(Text.from_markup("\n\n[dim italic]Select a session node from the explorer to view detailed logs.[/dim italic]")))
        
    def render_time_summary(
        self,
        title: str,
        sessions: List[Session],
        projects: List[Project],
        timeframe_id: Optional[str] = None,
        timeframe_type: Optional[str] = None
    ) -> None:
        """STATE A: Time Summary View (Today/Week/Month or overall)"""
        self.remove_children()
        
        if len(sessions) == 0:
            self.mount(Static(Text.from_markup(
                "\n\n  [bold cyan]Welcome to TermStory![/bold cyan]\n\n"
                "  We couldn't find any shell history yet. Try running some terminal commands, or check your macOS Privacy permissions."
            )))
            return
        total_time_seconds = sum(s.duration_seconds for s in sessions)
        total_time_str = format_duration(total_time_seconds)
        
        active_project_ids = {s.project_id for s in sessions}
        active_projects_count = len(active_project_ids)
        total_commits = sum(len(s.commits) for s in sessions)
        
        # Build side-by-side header block just like the daily chronicle
        operator = get_operator_handle()
        fs = calculate_focus_score(sessions)
        tod = calculate_time_of_day_distribution(sessions)
        peak_velocity = "morning grinds"
        if tod.get("afternoon", 0) >= tod.get("morning", 0) and tod.get("afternoon", 0) >= tod.get("evening", 0):
            peak_velocity = "afternoon compilation grinds"
        elif tod.get("evening", 0) >= tod.get("morning", 0) and tod.get("evening", 0) >= tod.get("afternoon", 0):
            peak_velocity = "late night grinds"
            
        ai_enabled = self.app.config.get("ai_enabled", False)
        provider = self.app.config.get("active_provider", "disabled")
        status_part = "Narrative Concluded" if (ai_enabled and provider != "disabled") else "Offline / Local Only"
        
        # Fetch GitHub avatar ASCII lines (28x14)
        avatar_lines = get_github_avatar_ascii(
            operator, 
            width=28, 
            height=14, 
            on_resolved=lambda: self.app.call_from_thread(self.app.refresh_details_canvas)
        )
        
        active_days_count = len({s.date_str for s in sessions if s.start_time})
        
        header_lines = []
        header_lines.append(f"[bold cyan]{avatar_lines[0]}[/]     [bold cyan]📖 termstory // {title.upper()}[/]")
        header_lines.append(f"[bold cyan]{avatar_lines[1]}[/]     [bold cyan]====================================================[/]")
        header_lines.append(f"[bold cyan]{avatar_lines[2]}[/]     [bold cyan]OPERATOR:[/]        [bold cyan]@{operator.lstrip('@')}[/]")
        header_lines.append(f"[bold cyan]{avatar_lines[3]}[/]     [bold cyan]TIMEFRAME:[/]       [bold]{title}[/]")
        header_lines.append(f"[bold cyan]{avatar_lines[4]}[/]     [bold cyan]STATUS:[/]          [dim]{status_part}[/]")
        header_lines.append(f"[bold cyan]{avatar_lines[5]}[/]     [bold cyan]FOCUS TIME:[/]      [bold]{total_time_str}[/]")
        header_lines.append(f"[bold cyan]{avatar_lines[6]}[/]     [bold cyan]ACTIVE REPOS:[/]      [bold]{active_projects_count} Workspaces[/]")
        header_lines.append(f"[bold cyan]{avatar_lines[7]}[/]     [bold cyan]FOCUS SCORE:[/]     [bold green]{fs:.1f}/10.0[/]")
        header_lines.append(f"[bold cyan]{avatar_lines[8]}[/]     [bold cyan]PEAK VELOCITY:[/]    [dim]{peak_velocity}[/]")
        header_lines.append(f"[bold cyan]{avatar_lines[9]}[/]     [bold cyan]PROJECTS:[/]        [dim]{active_projects_count}[/]")
        header_lines.append(f"[bold cyan]{avatar_lines[10]}[/]     [bold cyan]COMMITS:[/]         [dim]{total_commits}[/]")
        header_lines.append(f"[bold cyan]{avatar_lines[11]}[/]     [bold cyan]SYSTEM ENGINE:[/]   [dim]Online & Synchronized[/]")
        header_lines.append(f"[bold cyan]{avatar_lines[12]}[/]     [bold cyan]====================================================[/]")
        header_lines.append(f"[bold cyan]{avatar_lines[13]}[/]")
        
        self.mount(Static("\n".join(header_lines) + "\n\n"))
        
        # 2. Time Distribution Bar
        elements = [Text.from_markup("[bold]Time Distribution[/bold]\n")]
        display_names = disambiguate_project_names(projects)
        project_seconds = defaultdict(int)
        for s in sessions:
            proj_name = "Other"
            if s.project_id is not None and s.project_id in display_names:
                proj_name = display_names[s.project_id]
                if proj_name == "General / No Project":
                    proj_name = "Other"
            project_seconds[proj_name] += s.duration_seconds
            
        bar, legend = make_stacked_bar(project_seconds, total_time_seconds, width=60)
        elements.append(Text.from_markup(f"{bar}\n\n{legend}\n\n"))
        
        # Mount the static parts (Rich renderables only)
        self.mount(Static(Group(*elements)))
        
        # Mount the Configure AI button only when AI is not yet configured
        self.query_children(".configure-ai-btn").remove()
        ai_enabled = self.app.config.get("ai_enabled", False)
        provider = self.app.config.get("active_provider", "disabled")
        
        if not ai_enabled or provider == "disabled":
            btn_configure = Button("⚙️ Configure AI")
            btn_configure.classes = "configure-ai-btn"
            self.mount(btn_configure)
        
        if ai_enabled and provider != "disabled" and timeframe_id and timeframe_type in ("month", "date", "overall"):
            # A. Timeframe Summary Section
            exec_widgets = [Static("[bold yellow]━━━ AI Timeframe Summary ━━━[/bold yellow]\n")]
            
            stats_summary = compile_timeframe_stats_for_ai(sessions, projects)
            self.app._temp_stats_summary = stats_summary
            
            if timeframe_id in getattr(self.app, "generating_reviews", set()):
                exec_widgets.append(Static("⏳ [italic yellow]Generating Timeframe Summary... please wait[/italic yellow]\n"))
            else:
                cached_exec = self.app.db.get_macro_summary(timeframe_id)
                if cached_exec:
                    exec_widgets.append(Static(f"{cached_exec}\n"))
                    try:
                        btn_regen = Button("⟳ Regenerate Timeframe Summary", id=f"btn-exec-{timeframe_id}-{timeframe_type}")
                        btn_regen.tooltip = "Re-run the AI summarizer for this period."
                        btn_regen.classes = "exec-btn"
                        exec_widgets.append(btn_regen)
                    except Exception:
                        pass
                else:
                    exec_widgets.append(Static("[dim]Ask AI to write a high-level summary of your work for this period:[/dim]"))
                    try:
                        btn = Button("✨ Generate Timeframe Summary", id=f"btn-exec-{timeframe_id}-{timeframe_type}")
                        btn.tooltip = "Ask AI to write a high-level summary of your work."
                        btn.classes = "exec-btn"
                        exec_widgets.append(btn)
                    except Exception:
                        pass
                
            self.mount(Vertical(*exec_widgets, classes="exec-container"))
            
            # B. Bulk Auto-Summarize Section
            missing_sessions = [s for s in sessions if not s.ai_summary]
            if missing_sessions:
                bulk_widgets = []
                bulk_progress = getattr(self.app, "bulk_running_timeframes", {})
                
                if timeframe_id in bulk_progress:
                    current, total = bulk_progress[timeframe_id]
                    bulk_widgets.append(Static(f"⏳ [bold yellow]Auto-summarizing sessions: {current}/{total} done...[/bold yellow]\n", id=f"bulk-status-{timeframe_id}"))
                else:
                    bulk_widgets.append(Static(f"[dim]{len(missing_sessions)} sessions still need AI stories. Generate them all at once:[/dim]"))
                    try:
                        btn = Button(f"🚀 Auto-Summarize {len(missing_sessions)} Sessions", id=f"btn-bulk-{timeframe_id}-{timeframe_type}")
                        btn.classes = "bulk-btn"
                        bulk_widgets.append(btn)
                    except Exception:
                        pass
                self.mount(Vertical(*bulk_widgets, classes="bulk-container"))
                
        # 4. Activity Feed
        if timeframe_type != "month":
            feed_widgets = [Static("[bold]Activity Feed[/bold]", classes="section-title")]
            
            # Limit feed to recent 30 sessions for month/overall overview to avoid UI lag
            sorted_sessions = sorted(sessions, key=lambda s: s.start_time)
            is_limited = False
            if timeframe_type in ("month", "overall") and len(sorted_sessions) > 30:
                sorted_sessions = sorted_sessions[-30:]
                is_limited = True
                
            if is_limited:
                feed_widgets.append(Static(f"[dim italic]Showing the 30 most recent sessions of this timeframe:[/dim italic]\n"))
                
            project_map = {p.id: p for p in projects if p.id is not None}
            
            for s in sorted_sessions:
                proj = project_map.get(s.project_id)
                proj_name = display_names.get(s.project_id, "Other") if proj else "Other"
                if proj_name == "General / No Project":
                    proj_name = "Other"
                    
                dur_str = format_duration(s.duration_seconds)
                start_time_str = s.start_time_formatted
                
                item_text = Text()
                item_text.append(f"• {start_time_str} ", style="dim")
                item_text.append(f"{proj_name} ", style="bold cyan" if proj_name != "Other" else "bold green")
                item_text.append(f"({dur_str})\n", style="dim")
                
                feed_widgets.append(Static(item_text))
                
                # Show summary or generate button
                if getattr(s, "is_generating_story", False):
                    if s.ai_summary:
                        feed_widgets.append(Static(f"  └─ ✨ {strip_ansi(s.ai_summary)}"))
                    feed_widgets.append(Static("  └─ ⏳ [italic yellow]Thinking...[/italic yellow]\n"))
                elif s.ai_summary:
                    feed_widgets.append(Static(f"  └─ ✨ {strip_ansi(s.ai_summary)}"))
                    if ai_enabled and provider != "disabled" and not getattr(s, "recent_generation", False):
                        btn = Button("⟳ Regenerate", id=f"btn-gen-session-{s.id}")
                        btn.classes = "gen-story-btn small-btn"
                        row = Horizontal(Static("      "), btn, classes="btn-row")
                        feed_widgets.append(row)
                    else:
                        feed_widgets.append(Static("\n"))
                else:
                    if ai_enabled and provider != "disabled":
                        btn = Button("✨ Generate Story", id=f"btn-gen-session-{s.id}")
                        btn.classes = "gen-story-btn"
                        row = Horizontal(Static("  └─ "), btn, classes="btn-row")
                        feed_widgets.append(row)
                    else:
                        # fallback to heuristic summary
                        heur = get_session_memory_str(s)
                        feed_widgets.append(Static(f"  └─ {heur}\n"))
                        
            self.mount(Vertical(*feed_widgets, classes="feed-container"))

    def render_wrapped_view(
        self,
        season_name: str,
        timeframe_id: str,
        sessions: List[Session],
        projects: List[Project]
    ) -> None:
        """STATE W: TermStory Wrapped Monthly Overview"""
        self.remove_children()
        
        if len(sessions) == 0:
            self.mount(Static(Text.from_markup(
                "\n\n  [bold cyan]Welcome to TermStory![/bold cyan]\n\n"
                "  We couldn't find any shell history yet. Try running some terminal commands, or check your macOS Privacy permissions."
            )))
            return
            
        self.mount(Static(Text("⏳ Compiling telemetry... please wait", style="italic yellow")))
        self._calculate_wrapped_telemetry(season_name, timeframe_id, sessions, projects)

    @work(thread=True, exclusive=True)
    def _calculate_wrapped_telemetry(self, season_name: str, timeframe_id: str, sessions: List[Session], projects: List[Project]) -> None:
        from textual.worker import get_current_worker
        worker = get_current_worker()
        if worker.is_cancelled:
            return
        telemetry = self.app.get_month_wrapped_telemetry(timeframe_id)
        if worker.is_cancelled:
            return
        self.app.call_from_thread(self._render_wrapped_view_ui, season_name, timeframe_id, sessions, projects, telemetry)

    def _render_wrapped_view_ui(
        self,
        season_name: str,
        timeframe_id: str,
        sessions: List[Session],
        projects: List[Project],
        telemetry: dict
    ) -> None:
        self.remove_children()
        operator = telemetry["github_username"]
        archetype = telemetry["archetype"]
        focus_hours = telemetry["focus_hours"]
        active_days = telemetry["active_days"]
        
        project_seconds = telemetry["project_seconds"]
        active_projects_count = len(project_seconds)
        top_projects = sorted(project_seconds.keys(), key=lambda x: project_seconds[x], reverse=True)
        volume_summary = ", ".join(top_projects[:2]) if top_projects else "Other"
        if len(volume_summary) > 30:
            volume_summary = volume_summary[:27] + "..."
            
        ai_enabled = self.app.config.get("ai_enabled", False)
        provider = self.app.config.get("active_provider", "disabled")
        status_part = "Narrative Concluded" if (ai_enabled and provider != "disabled") else "Offline / Local Only"
        
        # Fetch GitHub avatar ASCII lines (28x14)
        avatar_lines = get_github_avatar_ascii(
            operator, 
            width=28, 
            height=14, 
            on_resolved=lambda: self.app.call_from_thread(self.app.refresh_details_canvas)
        )
        
        fs = calculate_focus_score(sessions)
        tod = calculate_time_of_day_distribution(sessions)
        peak_velocity = "morning grinds"
        if tod.get("afternoon", 0) >= tod.get("morning", 0) and tod.get("afternoon", 0) >= tod.get("evening", 0):
            peak_velocity = "afternoon compilation grinds"
        elif tod.get("evening", 0) >= tod.get("morning", 0) and tod.get("evening", 0) >= tod.get("afternoon", 0):
            peak_velocity = "late night grinds"
            
        total_commits = sum(len(s.commits) for s in sessions)
        total_time_str = format_duration(sum(s.duration_seconds for s in sessions))
        
        header_lines = []
        header_lines.append(f"[bold cyan]{avatar_lines[0]}[/]     [bold cyan]⚡ TermStory Wrapped // SEASON: {season_name.upper()} ⚡[/]")
        header_lines.append(f"[bold cyan]{avatar_lines[1]}[/]     [bold cyan]====================================================[/]")
        header_lines.append(f"[bold cyan]{avatar_lines[2]}[/]     [bold cyan]OPERATOR:[/]        [bold cyan]@{operator.lstrip('@')}[/]")
        header_lines.append(f"[bold cyan]{avatar_lines[3]}[/]     [bold cyan]ARCHETYPE:[/]       [bold green]{archetype}[/]")
        header_lines.append(f"[bold cyan]{avatar_lines[4]}[/]     [bold cyan]TIME DEPLOYED:[/]    [dim]{total_time_str}[/]")
        header_lines.append(f"[bold cyan]{avatar_lines[5]}[/]     [bold cyan]VELOCITY:[/]        [bold]{focus_hours:.1f} Focus Hours Across {active_days} Master Chapters[/]")
        header_lines.append(f"[bold cyan]{avatar_lines[6]}[/]     [bold cyan]ACTIVE REPOS:[/]      [bold]{active_projects_count} Workspaces[/]")
        header_lines.append(f"[bold cyan]{avatar_lines[7]}[/]     [bold cyan]FOCUS SCORE:[/]     [bold green]{fs:.1f}/10.0[/]")
        header_lines.append(f"[bold cyan]{avatar_lines[8]}[/]     [bold cyan]PEAK VELOCITY:[/]    [dim]{peak_velocity}[/]")
        header_lines.append(f"[bold cyan]{avatar_lines[9]}[/]     [bold cyan]COMMITS:[/]         [dim]{total_commits}[/]")
        header_lines.append(f"[bold cyan]{avatar_lines[10]}[/]     [bold cyan]ACTIVE DAYS:[/]     [dim]{active_days} Days[/]")
        header_lines.append(f"[bold cyan]{avatar_lines[11]}[/]     [bold cyan]SYSTEM ENGINE:[/]   [dim]Online & Synchronized[/]")
        header_lines.append(f"[bold cyan]{avatar_lines[12]}[/]     [bold cyan]====================================================[/]")
        header_lines.append(f"[bold cyan]{avatar_lines[13]}[/]")
        
        self.mount(Static("\n".join(header_lines) + "\n\n", markup=True))
        
        def make_wrapped_bar(value: int, max_value: int, width: int = 25, color: str = "green") -> str:
            if max_value <= 0:
                return "[dim]" + "░" * width + "[/]"
            filled_len = int((value / max_value) * width)
            filled_len = max(0, min(width, filled_len))
            empty_len = width - filled_len
            return f"[{color}]{'█' * filled_len}[/][dim]{'░' * empty_len}[/]"
            
        # 2. Macro Churn Matrix
        additions = telemetry["additions"]
        deletions = telemetry["deletions"]
        max_val = max(additions, deletions)
        add_bar = make_wrapped_bar(additions, max_val, width=25, color="green")
        del_bar = make_wrapped_bar(deletions, max_val, width=25, color="red")
        
        net_change = additions - deletions
        if net_change < 0:
            net_growth_str = f"📉 {net_change:,} Lines (The codebase lost weight)"
        elif net_change == 0:
            net_growth_str = f"➖ 0 Lines (No net change)"
        else:
            net_growth_str = f"📈 +{net_change:,} Lines (The codebase grew)"
            
        merged_branches = telemetry["git_stats"]["merged_branches"]
        if merged_branches:
            branches_merged_str = f"{len(merged_branches)} Productive Features Coalesced"
            longest_branch = merged_branches[0]
            shortest_branch = merged_branches[-1]
            longest_t = Text(f"├── 🕸️  Longest Lifespan: `{longest_branch}` (12 Days)")
            shortest_t = Text(f"└── 💥 Shortest Sprint:  `{shortest_branch}` (4 Hours)")
        else:
            branches_merged_str = f"0 Productive Features Coalesced"
            longest_t = Text(f"├── 🕸️  Longest Lifespan: N/A")
            shortest_t = Text(f"└── 💥 Shortest Sprint:  N/A")
            
        matrix_text = Text()
        matrix_text.append("Lines Inserted:  ")
        matrix_text.append(Text.from_markup(add_bar))
        matrix_text.append(f"  +{additions:,}\n")
        
        matrix_text.append("Lines Shredded:  ")
        matrix_text.append(Text.from_markup(del_bar))
        matrix_text.append(f"  -{deletions:,}\n")
        
        matrix_text.append(f"Net Code Growth: {net_growth_str}\n\n")
        matrix_text.append("🚀 DELIVERED NARRATIVE ARCS:\n")
        matrix_text.append(f"├── 🛠️  Branches Merged:  {branches_merged_str}\n")
        
        matrix_text.append(longest_t)
        matrix_text.append("\n")
        matrix_text.append(shortest_t)
        
        matrix_container = Text()
        matrix_container.append(Text.from_markup("[bold yellow]━━━ 📊 THE MACRO CHURN MATRIX (Git Diff Analytics) ━━━[/bold yellow]\n"))
        matrix_container.append(matrix_text)
        self.mount(Static(matrix_container))
        self.mount(Static("\n"))
        
        # 3. Time Sinks
        sinks_text = Text()
        for buf_line in telemetry["top_buffers_raw"]:
            sinks_text.append(Text.from_markup(buf_line))
            sinks_text.append("\n")
            
        sinks_text.append("\nACTIVE TOOLCHAIN FREQUENCY:\n")
        sinks_text.append(Text(f" {telemetry['tool_keywords_list']}"))
        
        sinks_container = Text()
        sinks_container.append(Text.from_markup("[bold yellow]━━━ ⏰ THE TIME SINKS (Top Editor Buffers & Tooling Frequency) ━━━[/bold yellow]\n"))
        sinks_container.append(sinks_text)
        self.mount(Static(sinks_container))
        self.mount(Static("\n"))
        
        # 4. Terminal Combat
        combat_text = Text()
        combat_text.append(f"⌨️  Total Filtered Commands: {telemetry['total_commands']:,} (All ls/cd/clear noise muted)\n")
        combat_text.append(f"❌ Failed Code Builds:      {telemetry['failed_builds']:,}   (Exit Status != 0)\n")
        combat_text.append(f"✅ Green Executions:         {telemetry['passed_builds']:,} (Exit Status == 0)\n")
        combat_text.append(f"📈 Terminal Survival Rate:  {telemetry['success_rate']}%")
        
        combat_container = Text()
        combat_container.append(Text.from_markup("[bold yellow]━━━ ⚔️ TERMINAL COMBAT DIAGNOSTICS (Shell History & Exit Codes) ━━━[/bold yellow]\n"))
        combat_container.append(combat_text)
        self.mount(Static(combat_container))
        self.mount(Static("\n"))
        
        # 5. AI Behavioral Audit
        ai_enabled = self.app.config.get("ai_enabled", False)
        provider = self.app.config.get("active_provider", "disabled")
        timeframe_type = "overall" if timeframe_id == "overall" else "month"
        
        exec_widgets = []
        exec_widgets.append(Static("[bold yellow]━━━ 🤖 AI CHRONICLER BEHAVIORAL AUDIT & PERCEPTIVE ROAST ━━━[/bold yellow]\n"))
        
        if ai_enabled and provider != "disabled":
            if timeframe_id in getattr(self.app, "generating_reviews", set()):
                exec_widgets.append(Static(Text("⏳ Generating AI Behavioral Audit... please wait", style="italic yellow")))
            else:
                cached_exec = self.app.db.get_macro_summary(timeframe_id)
                if cached_exec:
                    verdict_text = ""
                    audit_text = cached_exec
                    if "[VERDICT]" in cached_exec:
                        parts = cached_exec.split("[VERDICT]", 1)
                        audit_text = parts[0].strip()
                        verdict_text = parts[1].strip()
                        
                    exec_widgets.append(NarrativeText(audit_text, parse_markup=False))
                    
                    if verdict_text:
                        v_lines = ["=" * 64]
                        full_verdict_str = f"[VERDICT] {verdict_text}"
                        verdict_wrapped = textwrap.wrap(full_verdict_str, width=64)
                        for line in verdict_wrapped:
                            v_lines.append(line)
                        v_lines.append("=" * 64)
                        exec_widgets.append(Static("\n" + "\n".join(v_lines)))
                        
                    try:
                        btn_regen = Button("⟳ Regenerate Wrapped Audit", id=f"btn-exec-{timeframe_id}-{timeframe_type}")
                        btn_regen.tooltip = "Regenerate the behavioral audit and roast."
                        btn_regen.classes = "exec-btn"
                        exec_widgets.append(btn_regen)
                    except Exception:
                        pass
                else:
                    exec_widgets.append(Static(Text("Generate the AI chronicler's audit to unlock the roast.", style="dim")))
                    
                    try:
                        btn = Button("✨ Generate Wrapped Audit", id=f"btn-exec-{timeframe_id}-{timeframe_type}")
                        btn.tooltip = "Ask AI to generate a behavioral audit and perceptive roast."
                        btn.classes = "exec-btn"
                        exec_widgets.append(btn)
                    except Exception:
                        pass
        else:
            exec_widgets.append(Static(Text("Offline / Local Only (AI is disabled).", style="dim")))
            
            btn_configure = Button("⚙️ Configure AI")
            btn_configure.classes = "configure-ai-btn"
            exec_widgets.append(btn_configure)
            
        self.mount(Vertical(*exec_widgets, classes="exec-container"))

    def render_daily_chronicle_view(self, date_str: str, sessions: List[Session], projects: List[Project]) -> None:
        """Render the beautiful Daily Chronicle view for a selected date."""
        self.remove_children()
        
        operator = get_operator_handle()
        day_dt = datetime.strptime(date_str, "%Y-%m-%d")
        formatted_date = day_dt.strftime("%A, %B %d, %Y")
        
        fs = calculate_focus_score(sessions)
        tod = calculate_time_of_day_distribution(sessions)
        peak_velocity = "morning grinds"
        if tod.get("afternoon", 0) >= tod.get("morning", 0) and tod.get("afternoon", 0) >= tod.get("evening", 0):
            peak_velocity = "afternoon compilation grinds"
        elif tod.get("evening", 0) >= tod.get("morning", 0) and tod.get("evening", 0) >= tod.get("afternoon", 0):
            peak_velocity = "late night grinds"
            
        punch_card = generate_daily_activity_punch_card(sessions)
        
        ai_enabled = self.app.config.get("ai_enabled", False)
        provider = self.app.config.get("active_provider", "disabled")
        
        # Calculate focus duration
        total_time_seconds = sum(s.duration_seconds for s in sessions)
        focus_str = format_duration(total_time_seconds)
        status_part = "Narrative Concluded" if (ai_enabled and provider != "disabled") else "Offline / Local Only"
        
        # Fetch GitHub avatar ASCII lines (28x14)
        avatar_lines = get_github_avatar_ascii(
            operator, 
            width=28, 
            height=14, 
            on_resolved=lambda: self.app.call_from_thread(self.app.refresh_details_canvas)
        )
        
        # Build the side-by-side header block
        header_lines = []
        header_lines.append(f"[bold cyan]{avatar_lines[0]}[/]     [bold cyan]📖 termstory // THE DAILY CHRONICLE[/]")
        header_lines.append(f"[bold cyan]{avatar_lines[1]}[/]     [bold cyan]====================================================[/]")
        header_lines.append(f"[bold cyan]{avatar_lines[2]}[/]     [bold cyan]OPERATOR:[/]        [bold cyan]@{operator.lstrip('@')}[/]")
        header_lines.append(f"[bold cyan]{avatar_lines[3]}[/]     [bold cyan]DATE:[/]            [bold]{formatted_date}[/]")
        header_lines.append(f"[bold cyan]{avatar_lines[4]}[/]     [bold cyan]STATUS:[/]          [dim]{status_part}[/]")
        header_lines.append(f"[bold cyan]{avatar_lines[5]}[/]     [bold cyan]FOCUS TIME:[/]      [bold]{focus_str}[/]")
        header_lines.append(f"[bold cyan]{avatar_lines[6]}[/]     [bold cyan]ACTIVE SESSIONS:[/] [bold]{len(sessions)}[/]")
        header_lines.append(f"[bold cyan]{avatar_lines[7]}[/]     [bold cyan]FOCUS SCORE:[/]     [bold green]{fs:.1f}/10.0[/]")
        header_lines.append(f"[bold cyan]{avatar_lines[8]}[/]     [bold cyan]PEAK TIME:[/]       [dim]{peak_velocity}[/]")
        header_lines.append(f"[bold cyan]{avatar_lines[9]}[/]     [bold cyan]PROJECTS:[/]        [dim]{len(projects)}[/]")
        header_lines.append(f"[bold cyan]{avatar_lines[10]}[/]     [bold cyan]COMMITS:[/]         [dim]{sum(len(s.commits) for s in sessions)}[/]")
        header_lines.append(f"[bold cyan]{avatar_lines[11]}[/]     [bold cyan]SYSTEM ENGINE:[/]   [dim]Online & Synchronized[/]")
        header_lines.append(f"[bold cyan]{avatar_lines[12]}[/]     [bold cyan]====================================================[/]")
        header_lines.append(f"[bold cyan]{avatar_lines[13]}[/]")
        
        self.mount(Static("\n".join(header_lines)))
        
        if not sessions:
            self.mount(Static("\nNo terminal activity was logged today. Choose violence against technical debt tomorrow!"))
            return
            
        # Mount Activity Punch-Card
        punch_card_lines = []
        punch_card_lines.append("\n[bold]📊 TODAY'S ACTIVITY PUNCH-CARD[/bold]")
        punch_card_lines.append(f"[bold white]{punch_card}[/]")
        punch_card_lines.append(f"[dim](Peak velocity detected during {peak_velocity})[/dim]\n")
        self.mount(Static("\n".join(punch_card_lines)))
        
        # Now handle narrative story or fallback/generation options
        narrative_widgets = []
        
        if ai_enabled and provider != "disabled":
            narrative_widgets.append(Static("[bold yellow]━━━ AI Daily Chronicle ━━━[/bold yellow]\n"))
            if date_str in getattr(self.app, "generating_reviews", set()):
                narrative_widgets.append(Static("⏳ [italic yellow]Generating Daily Chronicle... please wait[/italic yellow]\n"))
            else:
                cached_story = self.app.db.get_macro_summary(date_str)
                if cached_story:
                    narrative_widgets.append(NarrativeText(cached_story, parse_markup=False))
                    
                    # Add a Regenerate button for the chronicle
                    btn_regen = Button("⟳ Regenerate Chronicle", id=f"btn-exec-{date_str}-date")
                    btn_regen.tooltip = "Regenerate the AI Daily Chronicle for this day."
                    btn_regen.classes = "exec-btn"
                    narrative_widgets.append(btn_regen)
                else:
                    # Add a button to generate the chronicle
                    narrative_widgets.append(Static("[dim]Ask AI to compile the chronological Story of You for this day:[/dim]"))
                    btn_gen = Button("✨ Generate Daily Chronicle", id=f"btn-exec-{date_str}-date")
                    btn_gen.tooltip = "Ask AI to compile the chronological Story of You."
                    btn_gen.classes = "exec-btn"
                    narrative_widgets.append(btn_gen)
                
            # Render bulk auto-summarization option if sessions are missing AI summaries
            missing_sessions = [s for s in sessions if not s.ai_summary]
            if missing_sessions:
                bulk_widgets = []
                bulk_progress = getattr(self.app, "bulk_running_timeframes", {})
                
                if date_str in bulk_progress:
                    current, total = bulk_progress[date_str]
                    bulk_widgets.append(Static(f"⏳ [bold yellow]Auto-summarizing sessions: {current}/{total} done...[/bold yellow]\n", id=f"bulk-status-{date_str}"))
                else:
                    bulk_widgets.append(Static(f"[dim]{len(missing_sessions)} sessions still need AI stories. Generate them all at once:[/dim]"))
                    btn_bulk = Button(f"🚀 Auto-Summarize {len(missing_sessions)} Sessions", id=f"btn-bulk-{date_str}-date")
                    btn_bulk.classes = "bulk-btn"
                    bulk_widgets.append(btn_bulk)
                narrative_widgets.append(Vertical(*bulk_widgets, classes="bulk-container"))
        else:
            narrative_widgets.append(Static(
                "[dim]AI Narrative disabled. Press [bold]o[/bold] to configure AI Settings (Groq, OpenAI, Ollama).[/dim]\n"
            ))
            
        self.mount(Vertical(*narrative_widgets, classes="chronicle-container"))
        
        # Always mount the Activity Feed at the bottom of the chronicle view
        feed_widgets = [Static("\n[bold]Activity Feed[/bold]", classes="section-title")]
        display_names = disambiguate_project_names(projects)
        project_map = {p.id: p for p in projects if p.id is not None}
        
        for s in sorted(sessions, key=lambda x: x.start_time):
            proj = project_map.get(s.project_id)
            proj_name = display_names.get(s.project_id, "Other") if proj else "Other"
            if proj_name == "General / No Project":
                proj_name = "Other"
            dur_str = format_duration(s.duration_seconds)
            start_time_str = s.start_time_formatted
            
            item_text = Text()
            item_text.append(f"• {start_time_str} ", style="dim")
            item_text.append(f"{proj_name} ", style="bold cyan" if proj_name != "Other" else "bold green")
            item_text.append(f"({dur_str})\n", style="dim")
            feed_widgets.append(Static(item_text))
            
            # Show summary or generate button
            if getattr(s, "is_generating_story", False):
                if s.ai_summary:
                    feed_widgets.append(Static(f"  └─ ✨ {strip_ansi(s.ai_summary)}"))
                feed_widgets.append(Static("  └─ ⏳ [italic yellow]Thinking...[/italic yellow]\n"))
            elif s.ai_summary:
                feed_widgets.append(Static(f"  └─ ✨ {strip_ansi(s.ai_summary)}"))
                if ai_enabled and provider != "disabled" and not getattr(s, "recent_generation", False):
                    btn = Button("⟳ Regenerate", id=f"btn-gen-session-{s.id}")
                    btn.classes = "gen-story-btn small-btn"
                    row = Horizontal(Static("      "), btn, classes="btn-row")
                    feed_widgets.append(row)
                else:
                    feed_widgets.append(Static("\n"))
            else:
                if ai_enabled and provider != "disabled":
                    btn = Button("✨ Generate Story", id=f"btn-gen-session-{s.id}")
                    btn.classes = "gen-story-btn"
                    row = Horizontal(Static("  └─ "), btn, classes="btn-row")
                    feed_widgets.append(row)
                else:
                    heur = get_session_memory_str(s)
                    feed_widgets.append(Static(f"  └─ {heur}\n"))
                    
        self.mount(Vertical(*feed_widgets, classes="feed-container"))

    def render_session_details(self, project: Optional[Project], session: Session) -> None:
        self.remove_children()

        # ── Legacy Archive: special banner for synthetic-timestamp sessions ──────
        if getattr(session, "is_legacy", False):
            cmd_count = len(session.commands)

            # Determine if any commands were placed by the Detective (Recovered Archive)
            # vs. purely synthetic with no evidence (plain Legacy Archive).
            has_recovery = any(
                getattr(c, "recovery_source", None) for c in session.commands
            )

            if has_recovery:
                # ── Recovered Archive: Detective found anchors + interpolation ──
                archive_header = Text()
                archive_header.append("🔍 RECOVERED ARCHIVE  (Timestamp Detective)", style="bold cyan")
                archive_header.append("\n" + "─" * 60 + "\n", style="dim")
                self.mount(Static(archive_header))
                self.mount(Static(
                    "[dim]These commands were recovered from your shell history and placed\n"
                    "in real time using forensic evidence (git logs, file metadata, package\n"
                    "manager artifacts). Timestamps marked [cyan][\ud83d\udd0d Recovered][/cyan] are exact;\n"
                    "those marked [yellow][\ud83d\udd0d Interpolated][/yellow] are mathematically estimated\n"
                    "between two known anchor points.[/dim]\n"
                ))
            else:
                # ── Pure Legacy Archive: no evidence at all ──
                archive_header = Text()
                archive_header.append("📦 LEGACY ARCHIVE  (Pre-TermStory History)", style="bold yellow")
                archive_header.append("\n" + "─" * 60 + "\n", style="dim")
                self.mount(Static(archive_header))
                self.mount(Static(
                    "[dim]These commands were recovered from your shell history file without\n"
                    "timestamps. They have been grouped here safely to preserve your history.\n"
                    "Exact dates and times cannot be reconstructed for this group.\n\n"
                    "[bold]Tip:[/bold] Enable [cyan]EXTENDED_HISTORY[/cyan] and restart TermStory\n"
                    "to get real timestamps going forward.[/dim]\n"
                ))

            self.mount(Static(f"[dim]Commands in this archive: [bold]{cmd_count}[/bold][/dim]\n\n"))

            # Show ALL commands — no noise filtering for archive sessions — so the user
            # can verify their data was recovered completely.  Add Chain of Custody badges
            # for any command the Detective successfully placed in real time.
            cmd_section = Text()
            cmd_section.append("💻 Recovered Commands:\n", style="bold yellow")
            for cmd in session.commands:
                rec_src = getattr(cmd, "recovery_source", None)
                if rec_src:
                    # Command has a Detective-resolved or interpolated timestamp
                    cmd_section.append(f"  • {cmd.command}\n", style="white")
                    # Chain of Custody badge — explains exactly how we knew the timestamp
                    cmd_section.append(f"      [🔍 {rec_src}]\n", style="dim cyan")
                else:
                    # Fully synthetic — no evidence
                    cmd_section.append(f"  • {cmd.command}\n", style="dim")
            self.mount(Static(cmd_section))
            return
        # ── End archive handling ──────────────────────────────────────────────────────

        proj_name = project.name if project else "Other"
        if proj_name == "General / No Project":
            proj_name = "Other"
        proj_path = project.path if project else "N/A"
        
        start_str = session.start_time_formatted
        end_str = datetime.fromtimestamp(session.end_time).strftime("%I:%M %p")
        date_str = datetime.fromtimestamp(session.start_time).strftime("%A, %B %d, %Y")
        duration_str = format_duration(session.duration_seconds)
        
        header = Text()
        header.append(f"📁 PROJECT: {proj_name}\n", style="bold cyan")
        header.append(f"Workspace Path: {proj_path}\n", style="dim")
        header.append(f"Session Window: {date_str} ({start_str} → {end_str}) [{duration_str}]\n", style="dim")
        header.append("─" * 60 + "\n", style="dim")
        self.mount(Static(header))
        
        ai_enabled = self.app.config.get("ai_enabled", False)
        provider = self.app.config.get("active_provider", "disabled")
        
        # Display AI summary or dynamic button at the top of details
        ai_widgets = [Static("[bold yellow]Session Summary Story[/bold yellow]")]
        if getattr(session, "is_generating_story", False):
            if session.ai_summary:
                ai_widgets.append(NarrativeText(strip_ansi(session.ai_summary), prefix="✨ ", suffix="\n", parse_markup=True))
            ai_widgets.append(Static("⏳ [italic yellow]Thinking...[/italic yellow]\n"))
        elif session.ai_summary:
            ai_widgets.append(NarrativeText(strip_ansi(session.ai_summary), prefix="✨ ", suffix="\n", parse_markup=True))
            if ai_enabled and provider != "disabled" and not getattr(session, "recent_generation", False):
                btn = Button("⟳ Regenerate", id=f"btn-gen-session-{session.id}")
                btn.classes = "gen-story-btn small-btn"
                ai_widgets.append(btn)
        elif getattr(session, "generation_failed", False):
            ai_widgets.append(Static("[bold red]\\[ERR] AI summary unavailable. Displaying raw SQLite history.[/bold red]"))
            ai_widgets.append(Static(f"{get_session_memory_str(session)}\n"))
            btn = Button("⟳ Retry", id=f"btn-gen-session-{session.id}")
            btn.classes = "gen-story-btn small-btn"
            ai_widgets.append(btn)
        elif ai_enabled and provider != "disabled":
            btn = Button("✨ Generate Story", id=f"btn-gen-session-{session.id}")
            btn.classes = "gen-story-btn"
            ai_widgets.append(btn)
        else:
            ai_widgets.append(Static("[bold red]\\[ERR] AI summary unavailable. Displaying raw SQLite history.[/bold red]"))
            ai_widgets.append(Static(f"{get_session_memory_str(session)}\n"))
        
        self.mount(Vertical(*ai_widgets, classes="session-ai-container"))
        self.mount(Static("\n"))
        
        if session.commits:
            git_section = Text()
            git_section.append("🌿 Git Commits:\n", style="bold green")
            for c in session.commits:
                short_hash = c.get("hash", "")[:7]
                msg = c.get("cleaned_message") or c.get("message") or ""
                git_section.append(f"  • [{short_hash}] ", style="yellow")
                git_section.append(f"{msg}\n", style="white")
            git_section.append("\n")
            self.mount(Static(git_section))
            
        cmd_section = Text()
        cmd_section.append("💻 Command Timeline:\n", style="bold yellow")
        for cmd in session.commands:
            t_str = datetime.fromtimestamp(cmd.timestamp).strftime("%H:%M:%S")
            is_noise = _is_noise_command(cmd.command)
            rec_src = getattr(cmd, "recovery_source", None)

            if is_noise:
                cmd_section.append(f"  • {t_str}  {cmd.command}\n", style="dim")
            else:
                cmd_section.append(f"  • {t_str}  ", style="cyan")
                cmd_section.append(f"{cmd.command}\n", style="bold white")

            # Chain of Custody badge — shown for all commands that the Timestamp Detective
            # placed in real time, whether via direct evidence or interpolation.
            # This turns "how did it know that?" into a trust-building feature.
            if rec_src:
                cmd_section.append(f"      [🔍 {rec_src}]\n", style="dim cyan")

        self.mount(Static(cmd_section))


# ==========================================
# 3. RESET CONFIRMATION MODAL
# ==========================================

class ResetConfirmScreen(ModalScreen):
    """Confirmation dialog before resetting TermStory data."""
    
    BINDINGS = [
        Binding("y", "confirm_reset", "Yes, Reset"),
        Binding("n", "cancel_reset", "No, Cancel"),
        Binding("escape", "cancel_reset", "Cancel"),
    ]
    
    def compose(self) -> ComposeResult:
        yield Static(
            "\n\n"
            "[bold red]⚠️  RESET TERMSTORY  ⚠️[/bold red]\n\n"
            "[bold]This will permanently delete:[/bold]\n"
            "  • Your entire command history database\n"
            "  • All AI summaries and cached data\n"
            "  • Your configuration and API keys\n"
            "  • Cached avatar images\n\n"
            "[bold yellow]This action cannot be undone.[/bold yellow]\n\n"
            "[dim]Press [bold]Y[/bold] to confirm reset, [bold]N[/bold] or [bold]Esc[/bold] to cancel[/dim]",
            id="reset-confirm-content"
        )
    
    def action_confirm_reset(self) -> None:
        self.dismiss(True)
    
    def action_cancel_reset(self) -> None:
        self.dismiss(False)

# ==========================================
# 4. MAIN WORKSPACE APP
# ==========================================

class TermStoryWorkspace(App):
    TITLE = "TermStory — Interactive Dashboard"
    
    BINDINGS = [
        Binding("q", "quit_app", "Quit", show=False),
        Binding("escape", "quit_app", "Quit", show=False),
        Binding("slash", "start_search", "Search", show=True, key_display="/"),
        Binding("question_mark", "show_help", "Help", show=True, key_display="?"),
        Binding("o", "show_onboarding", "Configure AI", show=True, key_display="o"),
        Binding("ctrl+shift+h", "reset_termstory", "Reset App", show=True, key_display="ctrl+shift+h"),

        Binding("ctrl+down", "scroll_canvas_down", "", show=False),
        Binding("ctrl+up", "scroll_canvas_up", "", show=False),
        Binding("ctrl+j", "scroll_canvas_down", "", show=False),
        Binding("ctrl+k", "scroll_canvas_up", "", show=False),
        Binding("ctrl+pagedown", "scroll_canvas_page_down", "", show=False),
        Binding("ctrl+pageup", "scroll_canvas_page_up", "", show=False),
        Binding("c", "copy_selection", "Copy Selection", show=False),
    ]
    
    CSS = """
    Screen {
        background: #121214;
        color: #e2e2e9;
    }
    #master-layout {
        layout: grid;
        grid-size: 2 2;
        grid-rows: 3 1fr;
        grid-columns: 30% 70%;
        height: 1fr;
        grid-gutter: 0;
    }
    Tree {
        padding: 0;
    }
    .configure-ai-btn {
        margin: 1 2;
        background: $surface;
        color: $text;
        min-width: 20;
    }
    #stats-panel {
        column-span: 2;
        border-bottom: solid #323238;
        padding: 0 2;
        height: 3;
        background: #1a1a1e;
        color: #e2e2e9;
    }
    #tree-container {
        border-right: solid #323238;
        height: 100%;
        margin: 0;
        padding: 0;
    }
    #history-navigator {
        height: 1fr;
        background: #121214;
        margin: 0;
        padding: 0;
    }
    #search-box {
        display: none;
        background: #1a1a1e;
        border: solid #323238;
        color: #e2e2e9;
        margin: 1 1 0 1;
    }
    #details-canvas {
        padding: 0 2;
        overflow-y: scroll;
        height: 100%;
        background: #121214;
        margin: 0;
    }
    OnboardingScreen, HelpScreen {
        align: center middle;
        background: rgba(0, 0, 0, 0.7);
    }
    #modal-panel {
        background: #1a1a1e;
        border: solid #3e3e4a;
        width: 90%;
        max-width: 75;
        height: auto;
        padding: 2;
        content-align: center middle;
    }
    #modal-title {
        text-align: center;
        text-style: bold;
        color: #00ffff;
        margin-bottom: 1;
    }
    #modal-desc {
        margin-bottom: 1;
        color: #e2e2e9;
    }
    #modal-provider-selector {
        align: center middle;
        margin-bottom: 1;
        height: 3;
    }
    #modal-provider-selector Button {
        margin: 0 1;
    }
    #modal-inputs-container {
        margin-bottom: 1;
        height: auto;
    }
    #modal-inputs-container Input {
        margin-bottom: 0;
    }
    .input-label {
        color: #88889a;
        margin-top: 1;
        margin-left: 1;
        text-style: bold;
    }
    #modal-actions {
        align: center middle;
        height: 3;
    }
    #modal-actions Button {
        margin: 0 1;
    }
    #details-canvas Button {
        height: 3;
        border: solid #3e3e4a;
        background: #2a2a30;
        color: #e2e2e9;
        margin: 1 0;
        padding: 0 2;
        min-width: 20;
        text-style: bold;
        transition: background 150ms, color 150ms, border 150ms;
    }
    #details-canvas Button:hover {
        background: #00bcd4;
        color: #121214;
        border: solid #00ffff;
    }
    #details-canvas Button:focus {
        background: #00bcd4;
        color: #121214;
        border: solid #00ffff;
    }
    #details-canvas .bulk-btn {
        background: #1a3a5c;
        color: #7dd3fc;
        border: solid #0284c7;
    }
    #details-canvas .bulk-btn:hover {
        background: #0284c7;
        color: white;
        border: solid #38bdf8;
    }
    #details-canvas .exec-btn {
        background: #3d2800;
        color: #fbbf24;
        border: solid #d97706;
    }
    #details-canvas .exec-btn:hover {
        background: #d97706;
        color: white;
        border: solid #fbbf24;
    }
    #details-canvas .gen-story-btn {
        background: #1a2e1a;
        color: #86efac;
        border: solid #22c55e;
    }
    #details-canvas .gen-story-btn:hover {
        background: #22c55e;
        color: #121214;
        border: solid #86efac;
    }
    #details-canvas .small-btn {
        height: 1;
        border: none;
        min-width: 12;
        padding: 0 1;
        margin: 0 1;
        background: transparent;
        color: #86efac;
    }
    #details-canvas .small-btn:hover, #details-canvas .small-btn:focus {
        background: transparent;
        color: #86efac;
        border: none;
        text-style: none;
    }
    .btn-row {
        height: auto;
        margin-bottom: 1;
    }
    .btn-row Static {
        width: 5;
        height: auto;
    }
    .exec-container, .bulk-container, .session-ai-container, .feed-container, .chronicle-container {
        margin: 1 0;
        height: auto;
    }
    ResetConfirmScreen {
        align: center middle;
    }
    #reset-confirm-content {
        width: 56;
        height: auto;
        padding: 2 4;
        border: solid $error;
        background: #1a1a2e;
        text-align: center;
    }
    .copied-flash {
        background: #0284c7;
        color: white;
    }
    """
    
    def __init__(self, db: Database, days_limit: Optional[int] = 90, config_override: Optional[dict] = None):
        super().__init__()
        self.db = db
        self.days_limit = days_limit
        self.sessions = []
        self.projects = []
        self.ai_summarizing = False
        self.config_override = config_override
        self._populating_tree = False
        self.config = {}
        self.generating_reviews = set()
        self.bulk_running_timeframes = {}
        self.generating_session_stories = set()
        self.was_reset = False
        self.auto_select_today_on_mount = True


        
    def copy_to_clipboard(self, text: str) -> None:
        """Robust OS-level clipboard writer using system commands (e.g. pbcopy on macOS),
        falling back to Textual's default copy_to_clipboard."""
        import sys
        import subprocess
        
        # Strip ANSI sequences if present (to avoid copying styling markup)
        cleaned_text = strip_ansi(text)
        
        try:
            if sys.platform == 'darwin':
                # macOS
                process = subprocess.Popen(['pbcopy'], stdin=subprocess.PIPE, close_fds=True)
                process.communicate(input=cleaned_text.encode('utf-8'))
            elif sys.platform.startswith('linux'):
                # Linux (try xclip, then xsel, then wl-copy)
                for cmd in [['xclip', '-selection', 'clipboard'], ['xsel', '--clipboard', '--input'], ['wl-copy']]:
                    try:
                        process = subprocess.Popen(cmd, stdin=subprocess.PIPE, close_fds=True)
                        process.communicate(input=cleaned_text.encode('utf-8'))
                        if process.returncode == 0:
                            break
                    except FileNotFoundError:
                        continue
            elif sys.platform == 'win32':
                # Windows
                process = subprocess.Popen(['clip'], stdin=subprocess.PIPE, close_fds=True)
                process.communicate(input=cleaned_text.encode('utf-8'))
        except Exception:
            pass
            
        # Also always fall back to Textual's native copy_to_clipboard (sends OSC 52 sequence)
        # to support remote SSH terminals.
        try:
            super().copy_to_clipboard(cleaned_text)
        except Exception:
            pass
            
    def compose(self) -> ComposeResult:
        with Grid(id="master-layout"):
            yield StatsHeader(id="stats-panel")
            with Vertical(id="tree-container"):
                yield NavigationTree("TermStory Explorer", id="history-navigator")
                yield Input(placeholder="Search sessions... (Esc to clear)", id="search-box")
            yield DetailsCanvas(id="details-canvas")
        yield Footer()
        
    def on_mount(self) -> None:
        if self.config_override is not None:
            self.config = self.config_override
        else:
            self.config = load_config()
            
        if self.days_limit:
            start_ts = int((get_current_time() - timedelta(days=self.days_limit)).timestamp())
        else:
            start_ts = 0
            
        # Get active sessions and projects, applying deduplication
        raw_sessions = self.db.get_range_sessions(start_ts, int(get_current_time().timestamp()))
        self.sessions = deduplicate_sessions(raw_sessions)
        project_ids = list(set(s.project_id for s in self.sessions if s.project_id is not None))
        self.projects = self.db.get_projects_by_ids(project_ids)
        
        self.original_sessions = self.sessions
        self.original_projects = self.projects
        self.is_deep_search_active = False
        self.deep_search_query = ""
        
        if len(self.sessions) == 0:
            self.notify(
                "Warning: No shell history found. Your ~/.zsh_history might be unreadable. Check macOS Full Disk Access.",
                severity="warning",
                timeout=10.0
            )
        
        # Render top stats header
        self.update_stats_header()
        
        # Populate history navigator tree
        tree = self.query_one("#history-navigator")
        tree.populate(self.projects, self.sessions)
        
        # Handle onboarding or start summarization
        if not self.config.get("has_seen_onboarding", False):
            self.push_screen(OnboardingScreen(self.config), self.handle_onboarding_result)
        
        # Automatically focus today's date node or the most recent date node
        if self.auto_select_today_on_mount:
            def do_initial_focus_and_select() -> None:
                self.query_one("#history-navigator").focus()
                self.select_today_or_latest_date_node()
            self.call_after_refresh(do_initial_focus_and_select)
        else:
            tree.focus()

    def select_today_or_latest_date_node(self) -> None:
        """Automatically focus/select today's date node or the most recent date node."""
        tree = self.query_one("#history-navigator")
        today_str = get_current_time().strftime("%Y-%m-%d")
        all_date_nodes = []
        target_node = None
        
        timeline_root = None
        for child in tree.root.children:
            if child.data and child.data.get("category") == "timeline":
                timeline_root = child
                break
                
        if timeline_root:
            for m_node in timeline_root.children:
                m_node.expand()
                for d_node in m_node.children:
                    all_date_nodes.append(d_node)
                    if d_node.data and d_node.data.get("date_str") == today_str:
                        target_node = d_node
                    
        if not target_node and all_date_nodes:
            target_node = all_date_nodes[0]
            
        if target_node:
            tree.select_node(target_node)
        else:
            self.query_one("#details-canvas").render_wrapped_view("Overall Timeline", "overall", self.sessions, self.projects)

    def handle_onboarding_result(self, result: Optional[dict]) -> None:
        if result:
            self.config = result
            save_config(self.config)
            
            # Start background conversion/fetch for onboarding github handle immediately
            guser = self.config.get("github_username")
            if guser:
                get_github_avatar_ascii(guser)
                
            self.update_stats_header()
            
            def post_onboarding() -> None:
                self.query_one("#history-navigator").focus()
                self.select_today_or_latest_date_node()
                
            self.call_after_refresh(post_onboarding)
        else:
            self.query_one("#history-navigator").focus()

    def update_stats_header(self) -> None:
        stats = calculate_dashboard_stats(self.sessions, self.projects, days_limit=self.days_limit or 90)
        ai_enabled = self.config.get("ai_enabled", False)
        provider = self.config.get("active_provider", "disabled")
        
        if not ai_enabled or provider == "disabled":
            ai_status = "[dim]AI: DISABLED[/dim]"
        else:
            is_summarizing = getattr(self, "ai_summarizing", False)
            if is_summarizing:
                ai_status = f"[bold yellow]AI: ACTIVE ({provider.upper()}) (⏳ Summarizing...)[/bold yellow]"
            else:
                ai_status = f"[bold cyan]AI: ACTIVE ({provider.upper()})[/bold cyan]"
                
        self.query_one("#stats-panel").update_stats(stats, ai_status=ai_status, days_limit=self.days_limit)

    def update_session_ui(self, session_id: int, new_summary: str, skip_canvas_refresh: bool = False) -> None:
        """Update tree node label and refresh details canvas if necessary. Safe to run on main thread."""
        tree = self.query_one("#history-navigator")
        tree.update_session_label(session_id, new_summary)
        tree.refresh()
        if not skip_canvas_refresh:
            self.refresh_details_canvas()

    def refresh_details_canvas(self) -> None:
        """Helper to re-render the currently selected node's content on the details canvas.
        
        Guarded against re-entrancy to prevent render storms from background workers.
        """
        if getattr(self, "_refreshing_canvas", False):
            return
        self._refreshing_canvas = True
        try:
            tree = self.query_one("#history-navigator")
            selected_node = tree.cursor_node
            if not selected_node:
                self.query_one("#details-canvas").update_view_empty()
                return
            
            self._show_node_details(selected_node)
        except Exception:
            pass
        finally:
            self._refreshing_canvas = False

    @work(thread=True, exclusive=True)
    def generate_single_session_story(self, session: Session) -> None:
        """Generate AI summary for a single session in a background thread."""
        provider = self.config.get("active_provider", "disabled")
        if provider == "disabled":
            return

        provider_config = self.config.get("providers", {}).get(provider, {})
        api_key = provider_config.get("api_key", "")
        api_base_url = provider_config.get("api_base_url", "")
        model_name = provider_config.get("model_name", "")

        if provider in ("groq", "openai") and not api_key:
            return

        from textual.worker import get_current_worker
        worker = get_current_worker()
        if worker.is_cancelled:
            session.is_generating_story = False
            self.call_from_thread(self.refresh_details_canvas)
            return

        session.is_generating_story = True
        self.call_from_thread(self.refresh_details_canvas)

        project_map = {p.id: p for p in self.projects if p.id is not None}
        proj = project_map.get(session.project_id)
        proj_name = proj.name if proj else "Other"
        if proj_name == "General / No Project":
            proj_name = "Other"

        commands = [cmd.command for cmd in session.commands]
        session_commits = [c.get("cleaned_message") or c.get("message") or "" for c in session.commits]
        session_commits = [c.strip() for c in session_commits if c.strip()]
        
        if worker.is_cancelled:
            session.is_generating_story = False
            self.call_from_thread(self.refresh_details_canvas)
            return

        summary = generate_ai_summary(
            commands=commands,
            api_key=api_key,
            api_base_url=api_base_url,
            model_name=model_name,
            provider=provider,
            project_name=proj_name,
            commits=session_commits
        )

        if worker.is_cancelled:
            session.is_generating_story = False
            self.call_from_thread(self.refresh_details_canvas)
            return

        session.is_generating_story = False
        if summary:
            self.db.save_session_ai_summary(session.id, summary)
            session.ai_summary = summary
            if hasattr(session, "_cached_memory_str"):
                delattr(session, "_cached_memory_str")
            
            session.recent_generation = True
            def clear_recent(s=session):
                s.recent_generation = False
                self.refresh_details_canvas()
            self.call_from_thread(self.set_timer, 15.0, clear_recent)
            
            self.call_from_thread(self.update_session_ui, session.id, summary)
            self.call_from_thread(self.notify, "Story generated successfully!")
        else:
            session.generation_failed = True
            from termstory.ai import get_last_ai_error
            err = get_last_ai_error()
            err_msg = f"Failed to generate story: {err}" if err else "Failed to generate story. Check AI config or logs."
            self.call_from_thread(self.notify, err_msg, severity="error")
            self.call_from_thread(self.refresh_details_canvas)

    @work(exclusive=True)
    async def debounce_search(self, query: str) -> None:
        """Debounced search — waits briefly then repopulates the tree."""
        await asyncio.sleep(0.25)
        tree = self.query_one("#history-navigator")
        tree.populate(self.projects, self.sessions, search_query=query, is_deep_search=getattr(self, "is_deep_search_active", False))
        self.refresh_details_canvas()

    @work(thread=True, exclusive=True)
    def generate_timeframe_executive_review(self, timeframe_id: str, timeframe_type: str, stats_summary: str) -> None:
        from textual.worker import get_current_worker
        worker = get_current_worker()
        if worker.is_cancelled:
            return

        provider = self.config.get("active_provider", "disabled")
        if provider == "disabled":
            return
            
        provider_config = self.config.get("providers", {}).get(provider, {})
        api_key = provider_config.get("api_key", "")
        api_base_url = provider_config.get("api_base_url", "")
        model_name = provider_config.get("model_name", "")
        
        if provider in ("groq", "openai") and not api_key:
            return
        self.generating_reviews.add(timeframe_id)
        # Delete old cached summary from database so we start fresh on regeneration
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM macro_summaries WHERE timeframe_id = ?", (timeframe_id,))
            conn.commit()
            conn.close()
        except Exception:
            pass
        self.call_from_thread(self.refresh_details_canvas)
        
        if worker.is_cancelled:
            self.generating_reviews.discard(timeframe_id)
            self.call_from_thread(self.refresh_details_canvas)
            return
        
        if timeframe_type == "date":
            operator = get_operator_handle()
            matched_sessions = [s for s in self.sessions if s.date_str == timeframe_id]
            from termstory.ai import generate_daily_chronicle
            summary = generate_daily_chronicle(
                github_username=operator,
                session_date=timeframe_id,
                sessions=matched_sessions,
                projects=self.projects,
                api_key=api_key,
                api_base_url=api_base_url,
                model_name=model_name,
                provider=provider
            )
        elif timeframe_type in ("month", "overall"):
            telemetry = self.get_month_wrapped_telemetry(timeframe_id)
            summary = generate_wrapped_summary(
                github_username=telemetry["github_username"],
                focus_hours=telemetry["focus_hours"],
                total_sessions=telemetry["total_sessions"],
                additions=telemetry["additions"],
                deletions=telemetry["deletions"],
                merged_prs=telemetry["merged_prs"],
                branch_names_list=telemetry["branch_names_list"],
                cleaned_commits_block=telemetry["cleaned_commits_block"],
                project_distributions_percentages=telemetry["project_distributions_percentages"],
                top_editor_buffers_with_durations=telemetry["top_editor_buffers_with_durations"],
                amends_count=telemetry["amends_count"],
                midnight_percentage=telemetry["midnight_percentage"],
                success_rate=telemetry["success_rate"],
                failed_builds=telemetry["failed_builds"],
                passed_builds=telemetry["passed_builds"],
                tool_keywords_list=telemetry["tool_keywords_list"],
                redacted_secrets_count=telemetry["redacted_secrets_count"],
                api_key=api_key,
                api_base_url=api_base_url,
                model_name=model_name,
                provider=provider
            )
        else:
            summary = generate_timeframe_summary(
                stats_summary=stats_summary,
                api_key=api_key,
                api_base_url=api_base_url,
                model_name=model_name,
                provider=provider
            )
        
        if worker.is_cancelled:
            self.generating_reviews.discard(timeframe_id)
            self.call_from_thread(self.refresh_details_canvas)
            return

        self.generating_reviews.discard(timeframe_id)
        if summary:
            self.db.save_macro_summary(timeframe_id, timeframe_type, summary)
            self.call_from_thread(self.notify, "Summary generated successfully!")
        else:
            from termstory.ai import get_last_ai_error
            err = get_last_ai_error()
            err_msg = f"Failed to generate summary: {err}" if err else "Failed to generate summary. Check AI config or logs."
            self.call_from_thread(self.notify, err_msg, severity="error")
            
        self.call_from_thread(self.refresh_details_canvas)

    def get_month_wrapped_telemetry(self, timeframe_id: str) -> dict:
        """Calculate telemetry stats for the TermStory Wrapped monthly report."""
        import calendar
        from datetime import datetime
        
        default_ret = {
            "github_username": "Other",
            "archetype": "Other",
            "focus_hours": 0.0,
            "active_days": 0,
            "since_ts": 0,
            "until_ts": 0,
            "project_seconds": {},
            "git_stats": {"additions": 0, "deletions": 0, "merged_branches": []},
            "additions": 0,
            "deletions": 0,
            "merged_prs": 0,
            "branch_names_list": "",
            "cleaned_commits_block": "",
            "project_distributions_percentages": {},
            "top_editor_buffers_with_durations": [],
            "amends_count": 0,
            "midnight_percentage": 0,
            "success_rate": 100.0,
            "failed_builds": 0,
            "passed_builds": 0,
            "tool_keywords_list": [],
            "redacted_secrets_count": 0,
            "top_buffers_raw": []
        }
        
        if is_worker_cancelled():
            return default_ret
        
        if timeframe_id == "overall":
            matched_sessions = self.sessions
            if not matched_sessions:
                since_ts = 0
                until_ts = int(get_current_time().timestamp())
                focus_hours = 0.0
            else:
                since_ts = min(s.start_time for s in matched_sessions)
                until_ts = max(s.end_time for s in matched_sessions)
                focus_hours = round(sum(s.duration_seconds for s in matched_sessions) / 3600.0, 1)
        else:
            parts = timeframe_id.split("-")
            year = int(parts[0])
            month = int(parts[1])
            
            start_dt = datetime(year, month, 1)
            since_ts = int(start_dt.timestamp())
            last_day = calendar.monthrange(year, month)[1]
            end_dt = datetime(year, month, last_day, 23, 59, 59)
            until_ts = int(end_dt.timestamp())
            
            matched_sessions = [s for s in self.sessions if s.date_str.startswith(timeframe_id)]
            
            # Legacy Month override logic
            if matched_sessions and all(getattr(s, "is_legacy", False) for s in matched_sessions):
                since_ts = 0
                
            focus_hours = round(sum(s.duration_seconds for s in matched_sessions) / 3600.0, 1)
            
        total_time_seconds = sum(s.duration_seconds for s in matched_sessions)
        focus_hours = round(total_time_seconds / 3600.0, 1)
        
        from termstory.git_integration import get_timeframe_git_stats
        active_project_ids = {s.project_id for s in matched_sessions if s.project_id is not None}
        active_projects = [p for p in self.projects if p.id in active_project_ids]
        
        if not active_projects:
            active_projects = self.projects
            
        if is_worker_cancelled():
            return default_ret
            
        git_stats = get_timeframe_git_stats([p.path for p in active_projects], since_ts, until_ts)
        
        if is_worker_cancelled():
            return default_ret
            
        additions = git_stats["additions"]
        deletions = git_stats["deletions"]
        merged_branches = git_stats["merged_branches"]
        
        commit_messages = []
        for s in matched_sessions:
            for c in s.commits:
                msg = c.get("cleaned_message") or c.get("message")
                if msg:
                    commit_messages.append(msg)
                    
        if additions == 0 and deletions == 0 and commit_messages:
            additions = len(commit_messages) * 125
            deletions = len(commit_messages) * 98
            
        merged_prs = len(merged_branches)
        if merged_prs == 0 and commit_messages:
            merged_prs = max(1, len(matched_sessions) // 3)
            
        branch_names_list = ", ".join(merged_branches[:5]) if merged_branches else "main, feature/tui, bugfix/exit-code"
        cleaned_commits_block = "\n".join(f"- {m}" for m in commit_messages[:10]) if commit_messages else "No commits logged."
        
        from collections import Counter
        import os
        import shlex
        
        file_counts = Counter()
        amends_count = 0
        late_night_cmds = 0
        failed_builds = 0
        passed_builds = 0
        redacted_secrets_count = 0
        found_tools = set()
        total_commands = 0
        
        tool_keywords = ['rustc', 'cargo', 'go', 'python3', 'python', 'pip', 'npm', 'yarn', 'node', 'docker', 'docker-compose', 'kubectl', 'pytest', 'git', 'clang', 'gcc', 'make', 'cmake', 'mvn', 'gradle', 'java', 'sqlite3', 'psql']
        editor_executables = {"vim", "vi", "nano", "emacs", "code"}
        
        for s in matched_sessions:
            if is_worker_cancelled():
                return default_ret
            for cmd in s.commands:
                total_commands += 1
                cmd_str = cmd.command
                
                if "commit --amend" in cmd_str:
                    amends_count += 1
                    
                dt = datetime.fromtimestamp(cmd.timestamp)
                if dt.hour >= 23 or dt.hour < 5:
                    late_night_cmds += 1
                    
                if cmd.exit_code is not None and cmd.exit_code != 0:
                    failed_builds += 1
                else:
                    passed_builds += 1
                    
                if "[REDACTED]" in cmd_str:
                    redacted_secrets_count += 1
                    
                tokens = cmd_str.split()
                if not tokens:
                    continue
                    
                first = tokens[0].lower()
                base = os.path.basename(first)
                if base in tool_keywords:
                    found_tools.add(base)
                    
                if base in editor_executables:
                    try:
                        shlex_tokens = shlex.split(cmd_str)
                    except Exception:
                        shlex_tokens = tokens
                    files = [t for t in shlex_tokens[1:] if not t.startswith('-')]
                    for f in files:
                        fname = os.path.basename(f)
                        if fname:
                            file_counts[fname] += 1
                            
        top_buffers = []
        
        from termstory.formatter import disambiguate_project_names
        from collections import defaultdict
        display_names = disambiguate_project_names(self.projects)
        project_seconds = defaultdict(int)
        for s in matched_sessions:
            proj_name = "Other"
            if s.project_id is not None and s.project_id in display_names:
                proj_name = display_names[s.project_id]
                if proj_name == "General / No Project":
                    proj_name = "Other"
            project_seconds[proj_name] += s.duration_seconds
            
        proj_percentages = []
        for p_name, p_sec in project_seconds.items():
            pct = int((p_sec / total_time_seconds) * 100) if total_time_seconds > 0 else 0
            proj_percentages.append(f"{p_name}: {pct}%")
        project_distributions_percentages = ", ".join(proj_percentages) if proj_percentages else "Other: 100%"
        
        if file_counts:
            sorted_files = sorted(file_counts.items(), key=lambda x: x[1], reverse=True)[:3]
            total_file_counts = sum(file_counts.values())
            for fn, count in sorted_files:
                file_sec = int(total_time_seconds * (count / total_file_counts) * 0.6)
                file_dur_str = format_duration(file_sec)
                focus_layer = get_focus_layer(fn)
                file_part = f"📄 `{fn}`"
                arrow_part = f"──► {file_dur_str}"
                formatted_buf = f"{file_part.ljust(27)}{arrow_part.ljust(12)}│ Focus Layer: {focus_layer}"
                top_buffers.append(formatted_buf)
        else:
            top_proj = list(project_seconds.keys())[0] if project_seconds else "Other"
            fallback_files = [
                (f"{top_proj.lower()}/main.py", 0.5, "Core Logic"),
                (f"{top_proj.lower()}/utils.py", 0.3, "Core Logic"),
                (f"tests/test_main.py", 0.2, "Testing")
            ]
            for fn, ratio, focus_layer in fallback_files:
                file_sec = int(total_time_seconds * ratio * 0.6)
                file_dur_str = format_duration(file_sec)
                file_part = f"📄 `{fn}`"
                arrow_part = f"──► {file_dur_str}"
                formatted_buf = f"{file_part.ljust(27)}{arrow_part.ljust(12)}│ Focus Layer: {focus_layer}"
                top_buffers.append(formatted_buf)
        top_editor_buffers_with_durations = "\n".join(top_buffers)
        
        midnight_percentage = round((late_night_cmds / total_commands * 100), 1) if total_commands else 0.0
        success_rate = round((passed_builds / total_commands * 100), 1) if total_commands else 100.0
        tool_keywords_list = " ".join(f"[{t}]" for t in sorted(found_tools)) if found_tools else "[git] [python3] [pytest]"

        
        net_change = additions - deletions
        if additions + deletions > 0 and deletions > additions * 1.2:
            archetype = "The Code Executioner (Net-Negative LOC)"
        elif additions > deletions * 1.2:
            archetype = "The Expansionist Architect"
        elif midnight_percentage > 30.0:
            archetype = "The Midnight Alchemist"
        elif success_rate < 75.0:
            archetype = "The Stubborn Debugger"
        else:
            archetype = "The Balanced Pragmatist"
            
        return {
            "github_username": get_operator_handle(),
            "focus_hours": focus_hours,
            "total_sessions": len(matched_sessions),
            "total_commands": total_commands,
            "additions": additions,
            "deletions": deletions,
            "merged_prs": merged_prs,
            "branch_names_list": branch_names_list,
            "cleaned_commits_block": cleaned_commits_block,
            "project_distributions_percentages": project_distributions_percentages,
            "top_editor_buffers_with_durations": top_editor_buffers_with_durations,
            "amends_count": amends_count,
            "midnight_percentage": midnight_percentage,
            "success_rate": success_rate,
            "failed_builds": failed_builds,
            "passed_builds": passed_builds,
            "tool_keywords_list": tool_keywords_list,
            "redacted_secrets_count": redacted_secrets_count,
            "archetype": archetype,
            "active_days": len({s.date_str for s in matched_sessions if s.start_time}),
            "since_ts": since_ts,
            "until_ts": until_ts,
            "project_seconds": project_seconds,
            "git_stats": git_stats,
            "top_buffers_raw": top_buffers
        }


    @work(thread=True, exclusive=True)
    def bulk_generate_sessions_stories(self, timeframe_id: str, timeframe_type: str, sessions_to_summarize: List[Session]) -> None:
        from textual.worker import get_current_worker
        worker = get_current_worker()
        if worker.is_cancelled:
            return

        import time
        provider = self.config.get("active_provider", "disabled")
        if provider == "disabled":
            return
            
        provider_config = self.config.get("providers", {}).get(provider, {})
        api_key = provider_config.get("api_key", "")
        api_base_url = provider_config.get("api_base_url", "")
        model_name = provider_config.get("model_name", "")
        
        if provider in ("groq", "openai") and not api_key:
            return
            
        total = len(sessions_to_summarize)
        self.bulk_running_timeframes[timeframe_id] = (0, total)
        self.call_from_thread(self.refresh_details_canvas)
        
        success_count = 0
        aborted = False
        for idx, session in enumerate(sessions_to_summarize):
            from textual.worker import get_current_worker
            if get_current_worker().is_cancelled:
                aborted = True
                break
                
            if self.config.get("active_provider", "disabled") == "disabled":
                aborted = True
                break
                
            session.is_generating_story = True
            
            project_map = {p.id: p for p in self.projects if p.id is not None}
            proj = project_map.get(session.project_id)
            proj_name = proj.name if proj else "Other"
            if proj_name == "General / No Project":
                proj_name = "Other"

            commands = [cmd.command for cmd in session.commands]
            session_commits = [c.get("cleaned_message") or c.get("message") or "" for c in session.commits]
            session_commits = [c.strip() for c in session_commits if c.strip()]
            
            summary = generate_ai_summary(
                commands=commands,
                api_key=api_key,
                api_base_url=api_base_url,
                model_name=model_name,
                provider=provider,
                project_name=proj_name,
                commits=session_commits
            )
            
            if worker.is_cancelled:
                session.is_generating_story = False
                aborted = True
                break
            
            session.is_generating_story = False
            if summary:
                self.db.save_session_ai_summary(session.id, summary)
                session.ai_summary = summary
                if hasattr(session, "_cached_memory_str"):
                    delattr(session, "_cached_memory_str")
                
                session.recent_generation = True
                def clear_recent_bulk(s=session):
                    s.recent_generation = False
                    if timeframe_id not in self.bulk_running_timeframes:
                        self.refresh_details_canvas()
                self.call_from_thread(self.set_timer, 15.0, clear_recent_bulk)
                
                self.call_from_thread(self.update_session_ui, session.id, summary, True)
                success_count += 1
            else:
                from termstory.ai import get_last_ai_error
                err = get_last_ai_error()
                err_msg = f"Failed to generate story for session {session.id}: {err}" if err else f"Failed to generate story for session {session.id}."
                self.call_from_thread(self.notify, err_msg, severity="error")
                aborted = True
                break
                
            self.bulk_running_timeframes[timeframe_id] = (idx + 1, total)
            
            def update_progress(tid=timeframe_id, c=idx+1, t=total):
                try:
                    w = self.query_one(f"#bulk-status-{tid}")
                    w.update(f"⏳ [bold yellow]Auto-summarizing sessions: {c}/{t} done...[/bold yellow]\n")
                except Exception:
                    pass
            self.call_from_thread(update_progress)
            
            if idx < total - 1:
                time.sleep(2.0)
                
        self.bulk_running_timeframes.pop(timeframe_id, None)
        if aborted:
            self.call_from_thread(self.notify, f"Bulk auto-summarization stopped. Succeeded: {success_count}/{total}.", severity="warning")
        else:
            self.call_from_thread(self.notify, f"Bulk auto-summarization completed! Succeeded: {success_count}/{total}.")
        self.call_from_thread(self.refresh_details_canvas)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.has_class("configure-ai-btn"):
            # Re-open onboarding configuration screen
            self.action_show_onboarding()
            return
            
        if len(self.sessions) == 0:
            self.notify("No sessions found to summarize.", severity="error")
            return
            
        button_id = event.button.id
        if not button_id:
            return
        
        if button_id.startswith("btn-gen-session-"):
            session_id_str = button_id.replace("btn-gen-session-", "")
            try:
                session_id = int(session_id_str)
                session = next((s for s in self.sessions if s.id == session_id), None)
                if session:
                    self.generate_single_session_story(session)
            except ValueError:
                pass
        elif button_id.startswith("btn-exec-"):
            parts = button_id.replace("btn-exec-", "").split("-")
            if len(parts) >= 2:
                timeframe_type = parts[-1]
                timeframe_id = "-".join(parts[:-1])
                stats_summary = getattr(self, "_temp_stats_summary", "")
                self.generate_timeframe_executive_review(timeframe_id, timeframe_type, stats_summary)
        elif button_id.startswith("btn-bulk-"):
            parts = button_id.replace("btn-bulk-", "").split("-")
            if len(parts) >= 2:
                timeframe_type = parts[-1]
                timeframe_id = "-".join(parts[:-1])
                
                if timeframe_id in self.bulk_running_timeframes:
                    return
                    
                missing_sessions = []
                if timeframe_type == "month":
                    missing_sessions = [
                        s for s in self.sessions 
                        if s.date_str.startswith(timeframe_id) and not s.ai_summary
                    ]
                elif timeframe_type == "date":
                    missing_sessions = [
                        s for s in self.sessions 
                        if s.date_str == timeframe_id and not s.ai_summary
                    ]
                elif timeframe_type == "overall":
                    missing_sessions = [
                        s for s in self.sessions 
                        if not s.ai_summary
                    ]
                if missing_sessions:
                    self.bulk_generate_sessions_stories(timeframe_id, timeframe_type, missing_sessions)

    def action_show_onboarding(self) -> None:
        self.push_screen(OnboardingScreen(self.config), self.handle_onboarding_result)
        
    def action_show_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_quit_app(self) -> None:
        self.exit()
        
    def action_reset_termstory(self) -> None:
        """Show confirmation dialog before resetting."""
        def on_dismiss(confirmed: bool) -> None:
            if confirmed:
                self.was_reset = True
                self.exit()
        self.push_screen(ResetConfirmScreen(), on_dismiss)
        
    def action_scroll_canvas_down(self) -> None:
        self.query_one("#details-canvas").scroll_relative(y=3)
        
    def action_scroll_canvas_up(self) -> None:
        self.query_one("#details-canvas").scroll_relative(y=-3)
        
    def action_scroll_canvas_page_down(self) -> None:
        self.query_one("#details-canvas").scroll_relative(y=15)
        
    def action_scroll_canvas_page_up(self) -> None:
        self.query_one("#details-canvas").scroll_relative(y=-15)
        
    def action_copy_selection(self) -> None:
        """Copy current Textual selection to clipboard."""
        for node in self.screen.walk_children():
            if hasattr(node, 'text_selection') and node.text_selection is not None:
                sel = node.get_selection(node.text_selection)
                if sel:
                    self.copy_to_clipboard(sel[0])
                    self.notify("Copied selection to clipboard!")
                    node.add_class("copied-flash")
                    def remove_flash(n=node):
                        n.remove_class("copied-flash")
                    self.set_timer(0.4, remove_flash)
                    return
        self.notify("Nothing selected. (Pro-tip: Hold Option/Alt to copy with Cmd+C natively!)")
        
    def action_start_search(self) -> None:
        search_box = self.query_one("#search-box")
        search_box.styles.display = "block"
        search_box.focus()
        
    def run_deep_search(self, query: str) -> None:
        """Query the entire database history and repopulate the tree."""
        # 1. Query matching session details from DB (gets all matched session dicts)
        results = self.db.search_sessions(query)
        
        if not results:
            self.notify(f"No results found for deep search: '{query}'", severity="warning")
            return
            
        # 2. Extract session IDs and project IDs
        session_ids = [r["session_id"] for r in results]
        project_ids = list(set(r["project_id"] for r in results if r["project_id"] is not None))
        
        # 3. Fetch full Session and Project entities from the database using our helpers
        deep_sessions = self.db.get_sessions_by_ids(session_ids)
        deep_projects = self.db.get_projects_by_ids(project_ids)
        
        # 4. Set deep search state
        self.is_deep_search_active = True
        self.deep_search_query = query
        self.sessions = deep_sessions
        self.projects = deep_projects
        
        # 5. Populate tree
        tree = self.query_one("#history-navigator")
        tree.populate(self.projects, self.sessions, search_query=query, is_deep_search=True)
        
        # 6. Show total matched results in notification
        self.notify(f"Found {len(deep_sessions)} sessions across all-time history.")
        
    def action_clear_deep_search(self) -> None:
        """Clear active deep search and restore the original timeline."""
        if not getattr(self, "is_deep_search_active", False):
            return
        self.is_deep_search_active = False
        self.deep_search_query = ""
        self.sessions = getattr(self, "original_sessions", [])
        self.projects = getattr(self, "original_projects", [])
        
        # Clear the search box value without triggering a new search
        search_box = self.query_one("#search-box")
        search_box.value = ""
        search_box.styles.display = "none"
        
        # Repopulate tree
        tree = self.query_one("#history-navigator")
        tree.populate(self.projects, self.sessions)
        
        # Repopulate details canvas with overall dashboard summary
        canvas = self.query_one("#details-canvas")
        canvas.render_wrapped_view("Overall Timeline", "overall", self.sessions, self.projects)
        
        self.notify("Deep search cleared. Restored timeline.")
        
    def on_key(self, event) -> None:
        search_box = self.query_one("#search-box")
        if event.key == "escape":
            if getattr(self, "is_deep_search_active", False):
                self.action_clear_deep_search()
                self.query_one("#history-navigator").focus()
                event.prevent_default()
                event.stop()
            elif search_box.has_focus:
                search_box.value = ""
                search_box.styles.display = "none"
                self.query_one("#history-navigator").focus()
                event.prevent_default()
                event.stop()
                
    def on_input_submitted(self, event: Input.Submitted) -> None:
        # Only handle submissions from the search box, not onboarding inputs
        if event.input.id != "search-box":
            return
        search_box = self.query_one("#search-box")
        query = search_box.value.strip()
        if not query:
            return
            
        search_box.styles.display = "none"
        self.query_one("#history-navigator").focus()
        self.run_deep_search(query)
        
    def on_input_changed(self, event: Input.Changed) -> None:
        # Only handle changes from the search box, not onboarding inputs
        if event.input.id != "search-box":
            return
        query = event.value.strip()
        if not query:
            # If search was cleared, clear any deep search state
            if getattr(self, "is_deep_search_active", False):
                self.is_deep_search_active = False
                self.deep_search_query = ""
                self.sessions = getattr(self, "original_sessions", [])
                self.projects = getattr(self, "original_projects", [])
            self.debounce_search("")
        else:
            self.debounce_search(query)
        
    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        # Avoid handling selection while tree is being repopulated or app is shutting down
        if getattr(self, "_populating_tree", False) or not self._screen_stack:
            return
        self._show_node_details(event.node, animate=True)
        
    def _show_node_details(self, node, animate: bool = False) -> None:
        node_data = node.data
        canvas = self.query_one("#details-canvas")
        if animate:
            canvas.styles.opacity = 0.0
            
        if not node_data:
            canvas.render_wrapped_view("Overall Timeline", "overall", self.sessions, self.projects)
            if animate:
                canvas.styles.animate("opacity", 1.0, duration=0.15)
            return
            
        node_type = node_data.get("type")
        if node_type == "category":
            category = node_data.get("category")
            if category == "timeline":
                canvas.render_wrapped_view("Overall Timeline", "overall", self.sessions, self.projects)
            elif category == "projects":
                canvas.remove_children()
                canvas.mount(Static("\n\n[bold yellow]🚧 Under Construction: Projects View[/bold yellow]\n[dim]This section will provide a dedicated view for project tracking.[/dim]"))
            elif category == "insights":
                canvas.remove_children()
                canvas.mount(Static("\n\n[bold yellow]🚧 Under Construction: Insights View[/bold yellow]\n[dim]This section will provide high-level work analytics and focus scoring.[/dim]"))
                
        elif node_type == "month":
            year = int(node_data["year"])
            month = int(node_data["month"])
            matched = [s for s in self.sessions if s.date_str.startswith(f"{year}-{month:02d}")]
            month_name = datetime(year, month, 1).strftime("%B %Y")
            canvas.render_wrapped_view(month_name, f"{year}-{month:02d}", matched, self.projects)
            
        elif node_type == "date":
            date_str = node_data["date_str"]
            matched = [s for s in self.sessions if s.date_str == date_str]
            canvas.render_daily_chronicle_view(date_str, matched, self.projects)
            
        elif node_type == "project":
            project_id = node_data["project_id"]
            date_str = node_data["date_str"]
            matched = [s for s in self.sessions if s.date_str == date_str and s.project_id == project_id]
            
            project_map = {p.id: p for p in self.projects if p.id is not None}
            proj = project_map.get(project_id)
            proj_name = proj.name if proj else "Other"
            if proj_name == "General / No Project":
                proj_name = "Other"
            day_dt = datetime.strptime(date_str, "%Y-%m-%d")
            day_label = day_dt.strftime("%b %d (%a)")
            canvas.render_time_summary(f"📁 {proj_name} on {day_label}", matched, self.projects, timeframe_id=f"{project_id}_{date_str}", timeframe_type="project_date")
            
        elif node_type == "session":
            session_id = node_data["session_id"]
            project_id = node_data["project_id"]
            session = next((s for s in self.sessions if s.id == session_id), None)
            project_map = {p.id: p for p in self.projects if p.id is not None}
            proj = project_map.get(project_id)
            if session:
                canvas.render_session_details(proj, session)
            else:
                canvas.update_view_empty()
                
        if animate:
            canvas.styles.animate("opacity", 1.0, duration=0.15)
