import os
import re
import shlex
import calendar
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import List, Dict, Tuple, Optional, Any

from termstory.models import Session, Project, Command, format_duration
from termstory.date_utils import get_current_time, format_date_range
from termstory.project import disambiguate_project_names

from rich.console import Console, Group
from rich.table import Table
from rich.text import Text


DISPLAY_NAMES = {
    "git": "Git",
    "docker": "Docker",
    "npm": "NPM/Yarn/PNPM",
    "python": "Python",
    "maven": "Maven",
    "vim": "Editor (Vim/Nano/etc)",
}

def classify_command(cmd_text: str) -> str:
    """Classify the command type based on the executable name"""
    tokens = cmd_text.strip().split()
    if not tokens:
        return "other"
        
    first_token = tokens[0].lower()
    
    if len(tokens) > 1 and first_token == "docker" and tokens[1].lower() == "compose":
        return "docker"
        
    classifications = {
        "git": ["git", "gh"],
        "docker": ["docker", "docker-compose"],
        "npm": ["npm", "yarn", "pnpm", "npx"],
        "python": ["python", "python3", "pip", "pip3", "pytest", "poetry"],
        "maven": ["mvn", "maven"],
        "vim": ["vim", "vi", "nano", "emacs"],
    }
    
    for category, triggers in classifications.items():
        if first_token in triggers:
            return category
            
    return first_token

def format_time(timestamp: int) -> str:
    """Format Unix timestamp to 12-hour local time format without leading zeroes, e.g. '9:00 AM'"""
    dt = datetime.fromtimestamp(timestamp)
    time_str = dt.strftime("%I:%M %p")
    if time_str.startswith("0"):
        time_str = time_str[1:]
    return time_str

def extract_files_from_commands(commands: List[Command]) -> Dict[str, int]:
    """Helper to extract edited files from command line arguments of editors (vim, nano, code, etc.)"""
    file_counts = Counter()
    editor_executables = {"vim", "vi", "nano", "emacs", "code"}
    
    for cmd in commands:
        try:
            tokens = shlex.split(cmd.command)
        except Exception:
            tokens = cmd.command.split()
            
        if not tokens:
            continue
            
        exec_name = os.path.basename(tokens[0].lower())
        if exec_name in editor_executables:
            # Arguments are likely file paths. Skip flags
            files = [t for t in tokens[1:] if not t.startswith('-')]
            for f in files:
                base = os.path.basename(f)
                if base:
                    file_counts[base] += 1
    return dict(file_counts)

def render_to_string(renderable: Any) -> str:
    """Helper to capture Rich console output as a string"""
    console = Console(width=80)
    with console.capture() as capture:
        console.print(renderable)
    return capture.get().strip()

def make_visual_bar(value: int, max_value: int, width: int = 15) -> str:
    """Generate a colorized visual progress bar using block characters"""
    if max_value <= 0:
        return "[grey37]" + "░" * width + "[/]"
    filled_len = int((value / max_value) * width)
    filled_len = max(0, min(width, filled_len))
    empty_len = width - filled_len
    return f"[bold green]{'█' * filled_len}[/][grey37]{'░' * empty_len}[/]"

def format_today_output(sessions: List[Session], projects: List[Project], compare_sessions: List[Session] = None) -> str:
    """Format today's sessions, command aggregates, and project details as a clean, box-free list"""
    is_override = "TERMSTORY_DATE_OVERRIDE" in os.environ
    today_str = get_current_time().strftime("%A, %B %d, %Y")
    
    if is_override:
        header_title = f"📋 Report for {today_str}"
    else:
        header_title = f"📋 Today ({today_str})"
        
    if not sessions:
        if is_override:
            return render_to_string(Text.from_markup(f"{header_title}\n\nNo sessions recorded on {today_str}."))
        return render_to_string(Text.from_markup(f"{header_title}\n\nNo sessions recorded today."))
        
    display_names = disambiguate_project_names(projects)
    
    # Group sessions by project name
    project_sessions = defaultdict(list)
    for s in sessions:
        proj_name = "Other"
        if s.project_id is not None and s.project_id in display_names:
            proj_name = display_names[s.project_id]
            if proj_name == "General / No Project":
                proj_name = "Other"
        project_sessions[proj_name].append(s)
        
    sorted_projects = sorted(project_sessions.keys(), key=lambda p: (p == "Other", p.lower()))
    
    output_lines = [header_title, ""]
    
    for proj_name in sorted_projects:
        proj_sessions = project_sessions[proj_name]
        total_time_seconds = sum(s.duration_seconds for s in proj_sessions)
        
        # Calculate yesterday comparison if compare_sessions is provided
        compare_str = ""
        if compare_sessions is not None:
            yesterday_seconds = 0
            p_ids_for_name = [pid for pid, name in display_names.items() if name == proj_name or (proj_name == "Other" and name == "General / No Project")]
            yesterday_seconds = sum(
                s.duration_seconds for s in compare_sessions
                if (s.project_id in p_ids_for_name) or (proj_name == "Other" and s.project_id is None)
            )
            diff = total_time_seconds - yesterday_seconds
            sign = "+" if diff >= 0 else "-"
            diff_color = "green" if diff >= 0 else "red"
            compare_str = f", [{diff_color}]{sign}{format_duration(abs(diff))} vs yesterday[/]"
            
        proj_header = f"[bold cyan]{proj_name}[/] [dim]({format_duration(total_time_seconds)}{compare_str})[/]"
        output_lines.append(proj_header)
        output_lines.append("[dim]────────────────────[/]")
        
        # Extract memories per session
        seen_memories = set()
        bullet_lines = []
        for s in sorted(proj_sessions, key=lambda x: x.start_time):
            # 1. Commits
            for c in s.commits:
                msg = c["cleaned_message"] or c["message"]
                mem = f"{msg} (commit)"
                if mem not in seen_memories:
                    seen_memories.add(mem)
                    bullet_lines.append(f"• {mem}")
            
            # 2. Key non-noise commands if no commits
            if not s.commits:
                candidates = [cmd.command for cmd in s.commands if not _is_noise_command(cmd.command)]
                if candidates:
                    best_cmd = max(candidates, key=len)
                    cleaned = clean_command_to_memory(best_cmd)
                    if cleaned not in seen_memories:
                        seen_memories.add(cleaned)
                        bullet_lines.append(f"• {cleaned}")
                else:
                    # 3. Fallback: raw commands
                    raw_cmds = []
                    for cmd in s.commands:
                        if not raw_cmds or raw_cmds[-1] != cmd.command:
                            raw_cmds.append(cmd.command)
                    for cmd in raw_cmds:
                        cleaned = clean_command_to_memory(cmd)
                        if cleaned not in seen_memories:
                            seen_memories.add(cleaned)
                            bullet_lines.append(f"• {cleaned}")
                            
        for line in bullet_lines:
            output_lines.append(line)
        output_lines.append("")
        
    return render_to_string(Text.from_markup("\n".join(output_lines).strip()))

def format_week_output(sessions: List[Session], projects: List[Project], start_ts: int, end_ts: int) -> str:
    """Format weekly summary report, grouping project hours by days of the week"""
    range_str = format_date_range(start_ts, end_ts)
    header_title = f"📊 This Week ({range_str})"
    
    if not sessions:
        return render_to_string(Text.from_markup(f"{header_title}\\n\\n[yellow]No sessions recorded this week.[/]"))
        
    display_names = disambiguate_project_names(projects)
    project_map = {p.id: p for p in projects if p.id is not None}
    
    sessions_by_project = defaultdict(list)
    for s in sessions:
        sessions_by_project[s.project_id].append(s)
        
    project_ids = list(sessions_by_project.keys())
    
    def project_sort_key(p_id):
        if p_id is None:
            return (1, "")
        p = project_map.get(p_id)
        name = p.name if p else ""
        return (0, name)
        
    project_ids.sort(key=project_sort_key)
    
    total_week_time = 0
    total_week_sessions = len(sessions)
    elements = []
    
    for p_id in project_ids:
        proj_sessions = sessions_by_project[p_id]
        proj_name = "General / No Project"
        if p_id is not None and p_id in display_names:
            proj_name = display_names[p_id]
            
        session_word = "session" if len(proj_sessions) == 1 else "sessions"
        proj_total_time = sum(s.duration_seconds for s in proj_sessions)
        total_week_time += proj_total_time
        
        proj_group_items = [
            Text.from_markup(f"📁 [bold cyan]{proj_name}[/] ([dim]{len(proj_sessions)} {session_word}[/])"),
            Text.from_markup(f"⏱️  Total Time: [bold green]{format_duration(proj_total_time)}[/]\n")
        ]
        
        # Calculate day-by-day breakdown
        day_times = defaultdict(int)
        for s in proj_sessions:
            day_name = datetime.fromtimestamp(s.start_time).strftime('%A')
            day_times[day_name] += s.duration_seconds
            
        days_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        max_day_time = max(day_times.values()) if day_times else 0
        
        day_table = Table(box=None, show_header=False, padding=(0, 2))
        for day in days_order:
            if day_times[day] > 0:
                bar = make_visual_bar(day_times[day], max_day_time, width=15)
                day_table.add_row(f"  {day:<10}", bar, f"[dim]{format_duration(day_times[day])}[/]")
                
        proj_group_items.append(day_table)
        
        # Commands
        cmd_counts = Counter()
        for s in proj_sessions:
            for cmd in s.commands:
                category = classify_command(cmd.command)
                cmd_counts[category] += 1
                
        if cmd_counts:
            cmd_strs = []
            for category, count in cmd_counts.most_common(5):
                display_cat = DISPLAY_NAMES.get(category, category)
                cmd_strs.append(f"[bold]{display_cat}[/] ({count})")
            proj_group_items.append(Text.from_markup("\n[bold]Commands:[/]\n  " + " • ".join(cmd_strs)))
            
        # Commits during this week
        proj_commits = []
        seen_hashes = set()
        for s in proj_sessions:
            for commit in s.commits:
                if commit["hash"] not in seen_hashes:
                    seen_hashes.add(commit["hash"])
                    proj_commits.append(commit)
                    
        if proj_commits:
            commit_lines = ["\n[bold]Commits This Week:[/]"]
            for c in proj_commits[:10]:
                short_hash = c["hash"][:7]
                msg = c["cleaned_message"] or c["message"]
                commit_lines.append(f"  [bold yellow]•[/] [cyan]{short_hash}[/] {msg}")
            if len(proj_commits) > 10:
                commit_lines.append(f"  [dim]... and {len(proj_commits) - 10} more commits[/]")
            proj_group_items.append(Text.from_markup("\n".join(commit_lines)))
            
        proj_group_items.append(Text.from_markup("\n[dim]" + "─" * 40 + "[/]"))
        elements.append(Group(*proj_group_items))
        
    footer_text = [
        f"📈 Total Work Time This Week: [bold green]{format_duration(total_week_time)}[/]",
        f"📝 Projects: [bold]{len(project_ids)}[/] • Sessions: [bold]{total_week_sessions}[/]"
    ]
    
    outer_group = Group(
        Text.from_markup(f"[bold green]{header_title}[/]\\n"),
        *elements,
        Text.from_markup("\\n" + "\\n".join(footer_text))
    )
    
    return render_to_string(outer_group)

def format_month_output(sessions: List[Session], projects: List[Project], year: int, month: int) -> str:
    """Format monthly summary report, listing total times and days worked for each project"""
    month_name = calendar.month_name[month]
    total_days = calendar.monthrange(year, month)[1]
    
    logged_dates = set(datetime.fromtimestamp(s.start_time).date() for s in sessions)
    days_logged = len(logged_dates)
    header_title = f"📊 {month_name} {year} ({days_logged} of {total_days} days logged)"
    
    if not sessions:
        return render_to_string(Text.from_markup(f"{header_title}\\n\\n[yellow]No sessions recorded this month.[/]"))
        
    display_names = disambiguate_project_names(projects)
    project_map = {p.id: p for p in projects if p.id is not None}
    
    sessions_by_project = defaultdict(list)
    for s in sessions:
        sessions_by_project[s.project_id].append(s)
        
    project_ids = list(sessions_by_project.keys())
    
    def project_sort_key(p_id):
        if p_id is None:
            return (1, "")
        p = project_map.get(p_id)
        name = p.name if p else ""
        return (0, name)
        
    project_ids.sort(key=project_sort_key)
    
    total_month_time = 0
    total_work_days = len(logged_dates)
    elements = []
    
    for p_id in project_ids:
        proj_sessions = sessions_by_project[p_id]
        proj_name = "General / No Project"
        if p_id is not None and p_id in display_names:
            proj_name = display_names[p_id]
            
        proj_total_time = sum(s.duration_seconds for s in proj_sessions)
        total_month_time += proj_total_time
        
        proj_logged_dates = set(datetime.fromtimestamp(s.start_time).date() for s in proj_sessions)
        days_worked = len(proj_logged_dates)
        day_word = "day" if days_worked == 1 else "days"
        
        sorted_dates = sorted(list(proj_logged_dates))
        days_str = ", ".join(f"{d.strftime('%b')} {d.day}" for d in sorted_dates)
        
        proj_group_items = [
            Text.from_markup(f"📁 [bold cyan]{proj_name}[/]"),
            Text.from_markup(f"⏱️  Total: [bold green]{format_duration(proj_total_time)}[/] ({days_worked} {day_word} worked)"),
            Text.from_markup(f"  Days: [dim]{days_str}[/]")
        ]
        
        proj_group_items.append(Text.from_markup("\\n[dim]" + "─" * 40 + "[/]"))
        elements.append(Group(*proj_group_items))
        
    footer_text = [
        f"Total Work Days: [bold]{total_work_days}[/]",
        f"Total Work Time: [bold green]{format_duration(total_month_time)}[/]"
    ]
    if total_work_days > 0:
        avg_per_day = int(total_month_time / total_work_days)
        footer_text.append(f"Average Per Day: [bold yellow]{format_duration(avg_per_day)}[/]")
        
    outer_group = Group(
        Text.from_markup(f"[bold green]{header_title}[/]\\n"),
        *elements,
        Text.from_markup("\\n" + "\\n".join(footer_text))
    )
    
    return render_to_string(outer_group)

def format_project_output(sessions: List[Session], project: Project) -> str:
    """Format project-specific detailed history as a clean, box-free list"""
    if not sessions:
        return f"📁 Project: [bold cyan]{project.name}[/] [dim]({project.path})[/]\n\nNo activity recorded."

    total_time_seconds = sum(s.duration_seconds for s in sessions)
    unique_days_set = set(datetime.fromtimestamp(s.start_time).strftime("%Y-%m-%d") for s in sessions)
    unique_days = len(unique_days_set)
    day_word = "day" if unique_days == 1 else "days"

    header_lines = [
        f"📁 Project: [bold cyan]{project.name}[/] [dim]({project.path})[/]",
        f"Active: [bold]{unique_days}[/] {day_word} worked | Total: [bold green]{format_duration(total_time_seconds)}[/]",
        ""
    ]

    # Group sessions by calendar day
    sessions_by_day = defaultdict(list)
    for s in sessions:
        day_key = datetime.fromtimestamp(s.start_time).strftime("%Y-%m-%d")
        sessions_by_day[day_key].append(s)

    # Sort days reverse-chronologically (newest first)
    sorted_day_keys = sorted(sessions_by_day.keys(), reverse=True)

    output_lines = list(header_lines)

    for day_key in sorted_day_keys:
        day_sessions = sessions_by_day[day_key]
        # Sort sessions on this day chronologically
        day_sessions.sort(key=lambda s: s.start_time)

        # Extract unique memories for this day
        day_memories = []
        seen_memories = set()
        for s in day_sessions:
            # 1. Commits
            for c in s.commits:
                msg = c["cleaned_message"] or c["message"]
                if msg not in seen_memories:
                    seen_memories.add(msg)
                    day_memories.append(msg)

            # 2. Key non-noise commands if no commits in session
            if not s.commits:
                candidates = [cmd.command for cmd in s.commands if not _is_noise_command(cmd.command)]
                if candidates:
                    best_cmd = max(candidates, key=len)
                    cleaned = clean_command_to_memory(best_cmd)
                    if cleaned not in seen_memories:
                        seen_memories.add(cleaned)
                        day_memories.append(cleaned)
                else:
                    # 3. Fallback: raw commands
                    raw_cmds = []
                    for cmd in s.commands:
                        if not raw_cmds or raw_cmds[-1] != cmd.command:
                            raw_cmds.append(cmd.command)
                    for cmd in raw_cmds:
                        cleaned = clean_command_to_memory(cmd)
                        if cleaned not in seen_memories:
                            seen_memories.add(cleaned)
                            day_memories.append(cleaned)

        if not day_memories:
            continue

        # Format day memories
        dt = datetime.strptime(day_key, "%Y-%m-%d")
        day_str = dt.strftime("%b %d")

        for idx, mem in enumerate(day_memories):
            if idx == 0:
                output_lines.append(f"{day_str:<8}  {mem}")
            else:
                output_lines.append(f"{'':<8}  {mem}")

    return render_to_string(Text.from_markup("\n".join(output_lines).strip()))

def format_projects_list(projects: List[Project]) -> str:
    """Format all-time projects list card"""
    header_title = "📚 Your Projects (All Time)"
    
    if not projects:
        return render_to_string(Text.from_markup(f"{header_title}\\n\\n[yellow]No projects found.[/]"))
        
    total_time = sum(p.total_time for p in projects)
    total_sessions = sum(p.session_count for p in projects)
    
    table = Table(box=None, border_style="blue", show_header=True)
    table.add_column("#", justify="right", style="dim")
    table.add_column("Project", style="cyan bold")
    table.add_column("Total Time", style="green")
    table.add_column("Sessions", justify="right")
    table.add_column("Active Range", style="dim")
    
    display_names = disambiguate_project_names(projects)
    for idx, p in enumerate(projects, 1):
        name = display_names.get(p.id, p.name)
        first_dt = datetime.fromtimestamp(p.first_seen)
        last_dt = datetime.fromtimestamp(p.last_seen)
        first_str = f"{first_dt.strftime('%b')} {first_dt.day}, {first_dt.strftime('%Y')}"
        last_str = f"{last_dt.strftime('%b')} {last_dt.day}, {last_dt.strftime('%Y')}"
        table.add_row(
            str(idx),
            name,
            format_duration(p.total_time),
            str(p.session_count),
            f"{first_str} - {last_str}"
        )
        
    footer_text = f"Total: [bold]{len(projects)}[/] projects, [bold]{total_sessions}[/] sessions, [bold green]{format_duration(total_time)}[/] worked"
    
    outer_group = Group(
        Text.from_markup(f"[bold green]{header_title}[/]\\n"),
        table,
        Text.from_markup("\\n" + footer_text)
    )
    
    return render_to_string(outer_group)

def format_detailed_sessions(sessions: List[Session]) -> str:
    """Detailed formatting showing all commands inside each session"""
    if not sessions:
        return "No sessions found."
        
    group_elements = []
    for idx, s in enumerate(sessions, 1):
        start_str = format_time(s.start_time)
        end_str = format_time(s.end_time)
        dur_str = format_duration(s.duration_seconds)
        date_str = datetime.fromtimestamp(s.start_time).strftime("%B %d, %Y")
        
        session_title = f"SESSION {idx}: [bold]{start_str} - {end_str}[/] ([bold green]{dur_str}[/]) on [bold cyan]{date_str}[/]"
        
        table = Table(box=None, show_header=True)
        table.add_column("Time", style="dim", width=10)
        table.add_column("Command", style="bold yellow")
        table.add_column("Exit Code", justify="right", width=10)
        
        for cmd in s.commands:
            t_str = datetime.fromtimestamp(cmd.timestamp).strftime("%H:%M:%S")
            exit_style = "green" if cmd.exit_code == 0 else "bold red"
            table.add_row(t_str, cmd.command, f"[{exit_style}]{cmd.exit_code}[/]")
            
        # If there are commits in this session, show them too!
        commit_group = None
        if s.commits:
            commit_table = Table(box=None, show_header=True)
            commit_table.add_column("Hash", style="cyan", width=8)
            commit_table.add_column("Commit Message")
            for c in s.commits:
                commit_table.add_row(c["hash"][:7], c["cleaned_message"] or c["message"])
            commit_group = commit_table
            
        session_group = [Text.from_markup(session_title), table]
        if commit_group:
            session_group.append(Text("\nCommits in Session:"))
            session_group.append(commit_group)
            
        session_group.append(Text.from_markup("\\n[dim]" + "─" * 40 + "[/]"))
        group_elements.append(Group(*session_group))
        
    return render_to_string(Group(*group_elements))

def highlight_query(text_str: str, query: str) -> Text:
    """Case-insensitively highlight query in text_str, returning a Rich Text object"""
    text = Text(text_str)
    if not query:
        return text
    query_lower = query.lower()
    len_q = len(query)
    start = 0
    while True:
        pos = text_str.lower().find(query_lower, start)
        if pos == -1:
            break
        text.stylize("bold red", pos, pos + len_q)
        start = pos + len_q
    return text

# --- Search helpers: noise filtering and memory extraction ---

_NOISE_COMMANDS_EXACT = frozenset({
    'cd', 'ls', 'pwd', 'clear', 'history', 'exit', 'q',
    'docker ps', 'docker images', 'docker logs', 'docker stop',
    'docker restart', 'docker system prune -a',
    'git status', 'git branch', 'git log', 'git diff', 'git stash',
    'top', 'htop', 'whoami',
})

def _is_noise_command(cmd: str) -> bool:
    """Check if a command is low-value noise (navigation, status checks, debugging).
    Shell comments and code fences are always noise. Multi-command chains (&&, ;)
    pass through only if they don't match other noise patterns."""
    stripped = cmd.strip()
    if not stripped:
        return True
    lower = stripped.lower()
    if lower in _NOISE_COMMANDS_EXACT:
        return True
    # Shell comments and pasted code fences — always noise, even in chains
    if stripped.startswith('#') or stripped.startswith('```'):
        return True
    # Multi-command chains are intentional work (if not caught above)
    if '&&' in stripped or '; ' in stripped:
        return False
    # Navigation
    if lower.startswith(('cd ', 'cd\t', 'ls ', 'ls\t')):
        return True
    # Debugging/inspection commands (not creative work)
    if lower.startswith(('docker logs ', 'docker exec ', 'docker stop ',
                         'docker restart ')):
        return True
    if lower.startswith(('tail ', 'head ', 'cat ', 'grep ', 'wc ')):
        return True
    # Scripting/utility commands (not memorable milestones)
    if lower.startswith(('sed ', 'echo ', 'find ', 'awk ', 'sort ',
                         'ssh ', 'scp ', 'mkdir ', 'touch ',
                         'rm ', 'mv ', 'cp ', 'chmod ', 'chown ')):
        return True
    return False

def split_command_chain(cmd_str: str) -> List[str]:
    """Split a command chain on '&&' or ';' without breaking inside quoted strings."""
    parts = []
    current = []
    in_single_quote = False
    in_double_quote = False
    escaped = False
    i = 0
    n = len(cmd_str)
    while i < n:
        char = cmd_str[i]
        if escaped:
            current.append(char)
            escaped = False
            i += 1
            continue
        if char == '\\':
            escaped = True
            current.append(char)
            i += 1
            continue
        if char == "'" and not in_double_quote:
            in_single_quote = not in_single_quote
            current.append(char)
        elif char == '"' and not in_single_quote:
            in_double_quote = not in_double_quote
            current.append(char)
        elif not in_single_quote and not in_double_quote:
            if char == ';':
                parts.append("".join(current).strip())
                current = []
            elif char == '&' and i + 1 < n and cmd_str[i+1] == '&':
                parts.append("".join(current).strip())
                current = []
                i += 2
                continue
            else:
                current.append(char)
        else:
            current.append(char)
        i += 1
    if current:
        parts.append("".join(current).strip())
    return [p for p in parts if p]

def clean_command_to_memory(cmd_str: str) -> str:
    """Extract a clean memory string from a command.
    Specifically, if it is a git commit command, extract the commit message.
    Otherwise, if it's a long command chain, clean/truncate it or humanize it."""
    # 1. Match git commit messages (quoted and unquoted, supporting flags like -m, -am, -sm, -asm, --message)
    commit_match = re.search(r'(?:-a?s?m|--message)(?:\s+|=)["\']([^"\']+)["\']', cmd_str)
    if commit_match:
        return commit_match.group(1).strip()
        
    commit_match_unquoted = re.search(r'(?:-a?s?m|--message)(?:\s+|=)([^\s"\']+)', cmd_str)
    if commit_match_unquoted:
        return commit_match_unquoted.group(1).strip()
        
    # 2. Humanize common git commands
    if cmd_str.strip().startswith("git "):
        checkout_b = re.search(r'checkout\s+-b\s+(\S+)', cmd_str)
        if checkout_b:
            return f"Create branch {checkout_b.group(1)}"
        checkout = re.search(r'checkout\s+(\S+)', cmd_str)
        if checkout:
            return f"Switch to branch {checkout.group(1)}"
        if "push" in cmd_str:
            return "Push changes to remote"
        if "pull" in cmd_str:
            return "Pull latest changes"
            
    # 3. Clean newlines and split chains using quote-aware tokenizer
    clean = cmd_str.replace("\n", " ").strip()
    parts = split_command_chain(clean)
    if len(parts) > 1:
        meaningful = [p for p in parts if not _is_noise_command(p)]
        if meaningful:
            return clean_command_to_memory(meaningful[-1])
            
    return clean

def _get_session_memory(result: Dict) -> Optional[Tuple[int, str, bool]]:
    """Extract the single best 'memory' from a search result session.
    Priority: matching commits > non-noise matching commands > non-noise commands.
    Returns (timestamp, description, is_commit) or None if nothing meaningful."""
    ts = result["start_time"]

    # Priority 1: Matching commits — these ARE the memory
    if result.get("matching_commits"):
        c = result["matching_commits"][0]
        return (ts, c["cleaned_message"] or c["message"], True)

    # Priority 2: Non-noise matching commands (pick most descriptive)
    if result.get("matching_commands"):
        candidates = [cmd for cmd in result["matching_commands"] if not _is_noise_command(cmd)]
        if candidates:
            return (ts, clean_command_to_memory(max(candidates, key=len)), False)

    # Priority 3: Non-noise any commands
    if result.get("all_commands"):
        candidates = [cmd for cmd in result["all_commands"] if not _is_noise_command(cmd)]
        if candidates:
            return (ts, clean_command_to_memory(max(candidates, key=len)), False)

    # Priority 4: Fallback to longest raw command if all else is noise (memory extraction fails)
    if result.get("all_commands"):
        return (ts, clean_command_to_memory(max(result["all_commands"], key=len)), False)

    # Nothing meaningful in this session
    return None

def _collapse_by_day(entries: List[Tuple[int, str, bool]]) -> List[Tuple[int, str]]:
    """Collapse a list of (timestamp, description, is_commit) entries to one per day.
    Prefers commit-sourced memories over command-sourced ones, then longest."""
    day_groups = defaultdict(list)
    for ts, desc, is_commit in entries:
        day_key = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        day_groups[day_key].append((ts, desc, is_commit))

    collapsed = []
    for day_key in sorted(day_groups.keys(), reverse=True):
        group = day_groups[day_key]
        # Prefer commits over commands, then longest description
        best = max(group, key=lambda x: (x[2], len(x[1])))
        collapsed.append((best[0], best[1]))
    return collapsed

# --- Main search formatter ---

def format_search_results(query: str, results: List[Dict], detailed: bool = False) -> str:
    """Format search results as a memory-first timeline, not history rows."""
    if not results:
        query_title = query.capitalize()
        return f"[bold cyan]{query_title}[/]\n\n[dim]No matches found[/]"

    header_str = f"🔍 Search: [bold cyan]{query}[/]"

    if not detailed:
        # Step 1: Extract ONE memory per session, grouped by project
        project_memories = defaultdict(list)
        project_durations = defaultdict(int)
        for r in results:
            proj_name = r["project_name"]
            if not proj_name or proj_name == "General / No Project":
                proj_name = "Other"
            
            project_durations[proj_name] += r["duration_seconds"]
            memory = _get_session_memory(r)
            if memory:
                project_memories[proj_name].append(memory)

        # Step 2: Collapse to one entry per day per project
        for proj in list(project_memories.keys()):
            project_memories[proj] = _collapse_by_day(project_memories[proj])

        # Step 3: Build header
        header_lines = [
            f"🔍 Search: [bold cyan]{query}[/]",
            ""
        ]

        # Sort: Other goes last
        sorted_projects = sorted(
            project_memories.keys(),
            key=lambda p: (1 if p == "Other" else 0, p.lower())
        )

        # Step 4: Render project groups
        body_elements = []
        for proj in sorted_projects:
            entries = project_memories[proj]
            if not entries:
                continue

            proj_header = f"[bold cyan]{proj}[/] [dim]({format_duration(project_durations[proj])})[/]"
            proj_group = [
                Text.from_markup(proj_header),
                Text.from_markup("[dim]" + "─" * 20 + "[/]")
            ]

            table = Table(box=None, show_header=False, padding=0)
            table.add_column("date", style="dim", width=8, no_wrap=True)
            table.add_column("desc", no_wrap=False)

            for ts, desc in entries:
                day_str = datetime.fromtimestamp(ts).strftime("%b %d")
                t_obj = highlight_query(desc, query)
                t_obj.no_wrap = True
                t_obj.overflow = "ellipsis"
                table.add_row(day_str, t_obj)

            proj_group.append(table)
            proj_group.append(Text(""))
            body_elements.append(Group(*proj_group))

        outer_group = Group(
            Text.from_markup("\n".join(header_lines)),
            *body_elements
        )
        return render_to_string(outer_group)

    # Detailed mode — full inspection view with timestamps, durations, commands
    group_items = [Text.from_markup(header_str + "\n")]
    for idx, r in enumerate(results, 1):
        s_id = r["session_id"]
        start_str = format_time(r["start_time"])
        end_str = format_time(r["end_time"])
        date_str = datetime.fromtimestamp(r["start_time"]).strftime("%B %d, %Y")
        dur_str = format_duration(r["duration_seconds"])

        proj_name = r["project_name"]
        if not proj_name or proj_name == "General / No Project":
            proj_name = "Other"

        session_header = f"MATCH {idx}: Session {s_id} on [bold]{date_str}[/] ({start_str} - {end_str}) [[bold green]{dur_str}[/]]"
        proj_line = f"📁 Project: [bold cyan]{proj_name}[/]"

        session_group = [
            Text.from_markup(session_header),
            Text.from_markup(proj_line)
        ]

        if r["matching_commands"]:
            session_group.append(Text("\nMatching Commands:"))
            for cmd in r["matching_commands"]:
                session_group.append(Group(Text("  • "), highlight_query(cmd, query)))

        if r["matching_commits"]:
            session_group.append(Text("\nMatching Commits:"))
            for c in r["matching_commits"]:
                short_hash = c["hash"][:7]
                msg = c["cleaned_message"] or c["message"]
                session_group.append(Group(Text(f"  • [cyan]{short_hash}[/] "), highlight_query(msg, query)))

        session_group.append(Text("\n" + "─" * 60 + "\n"))
        group_items.append(Group(*session_group))

    return render_to_string(Group(*group_items))

def _get_project_main_achievement(sessions: List[Session]) -> Tuple[str, str]:
    """Helper to find the single most meaningful and recent commit or key command across a project's sessions"""
    # Sort sessions reverse-chronologically to find the most recent achievement
    sorted_sessions = sorted(sessions, key=lambda s: s.start_time, reverse=True)
    for s in sorted_sessions:
        # Check commits
        if s.commits:
            # Get the most recent commit in this session
            c = sorted(s.commits, key=lambda x: x["timestamp"], reverse=True)[0]
            date_str = datetime.fromtimestamp(c["timestamp"]).strftime("%b %d")
            return c["cleaned_message"] or c["message"], date_str
    
    # If no commits in any session, look for key non-noise commands
    for s in sorted_sessions:
        candidates = [cmd for cmd in s.commands if not _is_noise_command(cmd.command)]
        if candidates:
            # Pick the longest/most descriptive command from the session
            best_cmd = max(candidates, key=lambda c: len(c.command))
            date_str = datetime.fromtimestamp(best_cmd.timestamp).strftime("%b %d")
            return clean_command_to_memory(best_cmd.command), date_str
            
    # If all else fails, fall back to the most recent raw command
    for s in sorted_sessions:
        if s.commands:
            # Sort commands reverse-chronologically
            sorted_cmds = sorted(s.commands, key=lambda c: c.timestamp, reverse=True)
            if sorted_cmds:
                cmd = sorted_cmds[0]
                date_str = datetime.fromtimestamp(cmd.timestamp).strftime("%b %d")
                return clean_command_to_memory(cmd.command), date_str
                
    return "No activity logged", ""

def format_insights_output(insights: Dict) -> str:
    """Format the developer work patterns insights report as Highlights"""
    days = insights.get("days", 30)
    
    from termstory.config import get_db_path
    from termstory.database import Database
    
    db = Database(get_db_path())
    import sqlite3
    try:
        db.init_db()
    except sqlite3.DatabaseError as e:
        if "malformed" in str(e).lower():
            from rich.console import Console
            import sys
            Console(stderr=True).print(
                "\n[bold red]Database Corrupted[/bold red]\n"
                "Your TermStory database is corrupted. Please run `termstory reset` to fix it."
            )
            sys.exit(1)
        raise
    
    start_ts = int((get_current_time() - timedelta(days=days)).timestamp())
    sessions = db.get_range_sessions(start_ts, int(get_current_time().timestamp()))
    
    if not sessions:
        return f"💡 Highlights (Last {days} Days)\n\nNo activity recorded in the last {days} days."
        
    # Get all projects associated with sessions
    project_ids = list(set(s.project_id for s in sessions if s.project_id is not None))
    projects = db.get_projects_by_ids(project_ids)
    display_names = disambiguate_project_names(projects)
    
    # Group sessions by project name
    project_sessions = defaultdict(list)
    for s in sessions:
        proj_name = "Other"
        if s.project_id is not None and s.project_id in display_names:
            proj_name = display_names[s.project_id]
            if proj_name == "General / No Project":
                proj_name = "Other"
        project_sessions[proj_name].append(s)
        
    # Sort projects by total time DESC
    project_times = {
        proj: sum(s.duration_seconds for s in sessions)
        for proj, sessions in project_sessions.items()
    }
    sorted_projects = sorted(project_sessions.keys(), key=lambda p: project_times[p], reverse=True)
    
    output_lines = [
        f"💡 Highlights (Last {days} Days)",
        ""
    ]
    
    for proj_name in sorted_projects:
        proj_sessions = project_sessions[proj_name]
        total_time = project_times[proj_name]
        
        # Calculate unique days worked
        unique_days_set = set(datetime.fromtimestamp(s.start_time).strftime("%Y-%m-%d") for s in proj_sessions)
        days_worked = len(unique_days_set)
        day_word = "day" if days_worked == 1 else "days"
        
        # Get main achievement
        achievement, ach_date = _get_project_main_achievement(proj_sessions)
        ach_suffix = f" ({ach_date})" if ach_date else ""
        
        output_lines.append(f"[bold cyan]{proj_name}[/] [dim]({format_duration(total_time)})[/]")
        output_lines.append("[dim]────────────────────[/]")
        output_lines.append(f"Active: [bold]{days_worked}[/] {day_word} | Main: {achievement}{ach_suffix}")
        output_lines.append("")
        
    return render_to_string(Text.from_markup("\n".join(output_lines).strip()))


def generate_daily_activity_punch_card(sessions: List[Session]) -> str:
    """Generate a horizontal dynamic terminal activity punch-card strip based on command volume per hour."""
    cmd_counts = [0] * 24
    for s in sessions:
        for c in s.commands:
            dt = datetime.fromtimestamp(c.timestamp)
            cmd_counts[dt.hour] += 1
            
    blocks = []
    for count in cmd_counts:
        if count == 0:
            blocks.append("░")
        elif count <= 10:
            blocks.append("▒")
        elif count <= 30:
            blocks.append("▓")
        else:
            blocks.append("█")
            
    seg1 = "".join(blocks[0:6])
    seg2 = "".join(blocks[6:12])
    seg3 = "".join(blocks[12:18])
    seg4 = "".join(blocks[18:24])
    
    return f"00:00 {seg1} 06:00 {seg2} 12:00 {seg3} 18:00 {seg4} 23:59"


def get_operator_handle() -> str:
    try:
        from termstory.config import load_config
        cfg = load_config()
        stored_user = cfg.get("github_username")
        if stored_user:
            return f"@{stored_user.strip().lstrip('@')}"
    except Exception:
        pass

    import subprocess
    try:
        res = subprocess.run(["git", "config", "github.user"], capture_output=True, text=True, check=False)
        user = res.stdout.strip()
        if user:
            return f"@{user}"
    except Exception:
        pass
    try:
        res = subprocess.run(["git", "config", "remote.origin.url"], capture_output=True, text=True, check=False)
        url = res.stdout.strip()
        if url:
            match = re.search(r'github\.com[:/]([^/]+)/', url)
            if match:
                return f"@{match.group(1)}"
    except Exception:
        pass
    try:
        res = subprocess.run(["git", "config", "user.name"], capture_output=True, text=True, check=False)
        name = res.stdout.strip()
        if name:
            return f"@{name.replace(' ', '-').lower()}"
    except Exception:
        pass
    try:
        import getpass
        return f"@{getpass.getuser()}"
    except Exception:
        return "@developer"


def boxify_terminal_wrapped(text: str) -> str:
    """Format and boxify executive summary cards into a clean terminal outline."""
    raw_lines = text.split("\n")
    cleaned_lines = []
    
    for line in raw_lines:
        stripped = line.strip()
        # Remove markdown stars
        stripped = stripped.replace("*", "")
        # Strip vertical borders
        if stripped.startswith("│"):
            stripped = stripped[1:]
        if stripped.endswith("│"):
            stripped = stripped[:-1]
        stripped = stripped.strip()
        # Ignore horizontal line boundary characters
        if any(c in stripped for c in ["┌", "└", "├", "─", "━", "═"]):
            continue
        # Clean up trailing / leading pipes
        if stripped.startswith("|"):
            stripped = stripped[1:]
        if stripped.endswith("|"):
            stripped = stripped[:-1]
        cleaned_lines.append(stripped.strip())
        
    # Remove leading/trailing empty lines
    while cleaned_lines and not cleaned_lines[0]:
        cleaned_lines.pop(0)
    while cleaned_lines and not cleaned_lines[-1]:
        cleaned_lines.pop()
        
    if not cleaned_lines:
        return text

    is_rpg = any("CHARACTER SHEET" in line.upper() or "TELEMETRY" in line.upper() or "[⚔️" in line or "[🎒" in line for line in cleaned_lines)
    width = 58
    
    import unicodedata
    import re
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    
    def display_len(s: str) -> int:
        s_clean = ansi_escape.sub('', s)
        length = 0
        for char in s_clean:
            if unicodedata.east_asian_width(char) in ('W', 'F'):
                length += 2
            else:
                length += 1
        return length

    box_lines = []
    
    if is_rpg:
        box_lines.append("┌" + "─" * (width + 2) + "┐")
        for line in cleaned_lines:
            if line.startswith("===") or line.startswith("---"):
                box_lines.append("├" + "─" * (width + 2) + "┤")
                continue
            disp = display_len(line)
            if disp > width:
                words = line.split(" ")
                current = ""
                for w in words:
                    test = current + " " + w if current else w
                    if display_len(test) <= width:
                        current = test
                    else:
                        box_lines.append(f"│ {current}{' ' * (width - display_len(current))} │")
                        current = w
                if current:
                    box_lines.append(f"│ {current}{' ' * (width - display_len(current))} │")
            else:
                box_lines.append(f"│ {line}{' ' * (width - disp)} │")
        box_lines.append("└" + "─" * (width + 2) + "┘")
        return "\n".join(box_lines)
        
    # Standard Spotify-Wrapped box
    box_lines.append("┌" + "─" * (width + 2) + "┐")
    
    # First line is the title
    title_line = cleaned_lines[0]
    disp = display_len(title_line)
    box_lines.append(f"│ {title_line}{' ' * max(0, width - disp)} │")
    box_lines.append("├" + "─" * (width + 2) + "┤")
    
    for line in cleaned_lines[1:]:
        if not line:
            box_lines.append(f"│ {' ' * width} │")
            continue
            
        disp = display_len(line)
        if disp > width:
            words = line.split(" ")
            current = ""
            for w in words:
                test = current + " " + w if current else w
                if display_len(test) <= width:
                    current = test
                else:
                    box_lines.append(f"│ {current}{' ' * max(0, width - display_len(current))} │")
                    current = w
            if current:
                box_lines.append(f"│ {current}{' ' * max(0, width - display_len(current))} │")
        else:
            box_lines.append(f"│ {line}{' ' * (width - disp)} │")
            
    box_lines.append("└" + "─" * (width + 2) + "┘")
    return "\n".join(box_lines)


import threading
_avatar_cache = {}
_avatar_fetching = set()
_avatar_lock = threading.Lock()

FALLBACK_AVATAR = [
    " ▄▄▄████▄▄▄ ",
    " ███▀  ▀███ ",
    " █▀      ▀█ ",
    " █ ▄▄  ▄▄ █ ",
    " █ ▀▀  ▀▀ █ ",
    " ██▄    ▄██ ",
    "  ▀██████▀  "
]

def get_fallback_avatar_padded(width: int, height: int) -> List[str]:
    """Pad and center the 12x7 fallback avatar within the requested width and height dimensions."""
    fallback_width = len(FALLBACK_AVATAR[0])
    fallback_height = len(FALLBACK_AVATAR)
    
    pad_top = max(0, (height - fallback_height) // 2)
    pad_bottom = max(0, height - fallback_height - pad_top)
    pad_left = max(0, (width - fallback_width) // 2)
    pad_right = max(0, width - fallback_width - pad_left)
    
    lines = []
    # Top padding
    for _ in range(pad_top):
        lines.append(" " * width)
    # Content
    for f_line in FALLBACK_AVATAR:
        lines.append(" " * pad_left + f_line + " " * pad_right)
    # Bottom padding
    for _ in range(pad_bottom):
        lines.append(" " * width)
        
    # Double check height is exactly correct (trim or pad if rounding issues)
    while len(lines) < height:
        lines.append(" " * width)
    if len(lines) > height:
        lines = lines[:height]
        
    return [line[:width] for line in lines]

def get_github_avatar_ascii(username: str, width: int = 12, height: int = 7, on_resolved=None) -> List[str]:
    """
    Get the GitHub avatar as ASCII art lines.
    If the avatar is not in cache, tries to load it from a local file-based cache.
    If not on disk, fetches it in a background thread and returns the fallback avatar.
    Once fetched, saves to disk and calls on_resolved callback to trigger UI refresh.
    """
    clean_username = username.strip().lstrip('@')
    if not clean_username or clean_username.lower() in ("developer", "other", "general"):
        return get_fallback_avatar_padded(width, height)
        
    cache_key = f"{clean_username}_{width}_{height}"
    with _avatar_lock:
        if cache_key in _avatar_cache:
            return _avatar_cache[cache_key]
        
    # Check disk cache
    import os
    from termstory.config import get_app_dir
    db_dir = get_app_dir("data")
    disk_path = os.path.join(db_dir, f"avatar_braille_{clean_username}_{width}_{height}.txt")
    if os.path.exists(disk_path):
        try:
            with open(disk_path, "r", encoding="utf-8") as f:
                lines = [line.rstrip('\r\n') for line in f.readlines()]
            if len(lines) == height:
                with _avatar_lock:
                    _avatar_cache[cache_key] = lines
                return lines
        except Exception:
            pass
            
    with _avatar_lock:
        if cache_key in _avatar_fetching:
            return get_fallback_avatar_padded(width, height)
        _avatar_fetching.add(cache_key)
        
    # Start background fetch
    def fetch_thread():
        try:
            from PIL import Image, ImageOps
            import urllib.request
            import io
            
            # Fetch the avatar image
            avatar_url = f"https://github.com/{clean_username}.png"
            req = urllib.request.Request(
                avatar_url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
            )
            
            with urllib.request.urlopen(req, timeout=10.0) as response:
                img_data = response.read()
                
            img = Image.open(io.BytesIO(img_data)).convert("RGBA")
            
            # Resize image to match requested text cells
            target_px_width = width * 2
            target_px_height = height * 4
            img = img.resize((target_px_width, target_px_height), Image.Resampling.BILINEAR)
            
            # Process grayscale
            r, g, b, a = img.split()
            black_bg = Image.new("RGB", img.size, (0, 0, 0))
            black_bg.paste(img, mask=a)
            gray_img = ImageOps.grayscale(black_bg)
            gray_img = ImageOps.equalize(gray_img)
            
            # Analyze complexity
            pixels_list = list(gray_img.getdata())
            mean_val = sum(pixels_list) / len(pixels_list)
            variance = sum((x - mean_val) ** 2 for x in pixels_list) / len(pixels_list)
            is_flat_graphic = variance < 3000
            
            pixels = []
            if is_flat_graphic:
                for y in range(height * 4):
                    row = []
                    for x in range(width * 2):
                        val = gray_img.getpixel((x, y))
                        row.append(1 if val >= 128 else 0)
                    pixels.append(row)
            else:
                border_pixels = [gray_img.getpixel((x, 0)) for x in range(target_px_width)] + \
                                [gray_img.getpixel((x, target_px_height - 1)) for x in range(target_px_width)] + \
                                [gray_img.getpixel((0, y)) for y in range(target_px_height)] + \
                                [gray_img.getpixel((target_px_width - 1, y)) for y in range(target_px_height)]
                should_invert = (sum(border_pixels) / len(border_pixels)) > 128
                
                bayer_matrix_8x8 = [
                    [ 0, 48, 12, 60,  3, 51, 15, 63],
                    [32, 16, 44, 28, 35, 19, 47, 31],
                    [ 8, 56,  4, 52, 11, 59,  7, 55],
                    [40, 24, 36, 20, 43, 27, 39, 23],
                    [ 2, 50, 14, 62,  1, 49, 13, 61],
                    [34, 18, 46, 30, 33, 17, 45, 29],
                    [10, 58,  6, 54,  9, 57,  5, 53],
                    [42, 26, 38, 22, 41, 25, 37, 21]
                ]
                for y in range(height * 4):
                    row = []
                    for x in range(width * 2):
                        val = gray_img.getpixel((x, y))
                        if should_invert:
                            val = 255 - val
                        threshold = int((bayer_matrix_8x8[y % 8][x % 8] + 0.5) * 4)
                        row.append(1 if val >= threshold else 0)
                    pixels.append(row)
                
            dot_weights = [
                ((0, 0), 0x01), ((0, 1), 0x02), ((0, 2), 0x04), ((1, 0), 0x08),
                ((1, 1), 0x10), ((1, 2), 0x20), ((0, 3), 0x40), ((1, 3), 0x80)
            ]
            
            lines = []
            for y in range(height):
                line_chars = []
                for x in range(width):
                    code = 0
                    for (dx, dy), weight in dot_weights:
                        px = 2 * x + dx
                        py = 4 * y + dy
                        if pixels[py][px] == 1:
                            code |= weight
                    line_chars.append(" " if code == 0 else chr(0x2800 + code))
                lines.append("".join(line_chars))
                
            with _avatar_lock:
                _avatar_cache[cache_key] = lines
            
            try:
                os.makedirs(db_dir, exist_ok=True)
                with open(disk_path, "w", encoding="utf-8") as f:
                    f.write("\n".join(lines))
            except Exception:
                pass
        except Exception:
            with _avatar_lock:
                _avatar_cache[cache_key] = get_fallback_avatar_padded(width, height)
        finally:
            with _avatar_lock:
                _avatar_fetching.discard(cache_key)
            if on_resolved:
                try:
                    on_resolved()
                except Exception:
                    pass
                    
    threading.Thread(target=fetch_thread, daemon=True).start()
    return get_fallback_avatar_padded(width, height)


def format_stats_output(db) -> str:
    """Format the deep history stats report as a beautiful, high-density dashboard."""
    from termstory.stats import daily_activity_heatmap, project_breakdown, language_detection, peak_hours
    
    # 1. Heatmap
    heatmap_str = daily_activity_heatmap(db, days_limit=30, colored=True)
    
    # 2. Project Breakdown
    breakdown = project_breakdown(db)
    
    # Sort projects by duration DESC
    sorted_projects = sorted(breakdown.items(), key=lambda x: x[1]["total_duration"], reverse=True)
    
    table = Table(box=None, show_header=True, padding=(0, 2))
    table.add_column("Project", style="cyan", header_style="bold cyan")
    table.add_column("Commands", justify="right", style="green")
    table.add_column("Duration", justify="right", style="green")
    table.add_column("Sessions", justify="right", style="green")
    table.add_column("First Active", style="dim")
    table.add_column("Last Active", style="dim")
    
    def format_ts(ts):
        if ts is None:
            return "N/A"
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        
    for name, stats in sorted_projects:
        table.add_row(
            name,
            str(stats["commands_count"]),
            format_duration(stats["total_duration"]),
            str(stats["sessions_count"]),
            format_ts(stats["first_seen"]),
            format_ts(stats["last_seen"]),
        )
        
    # 3. Language Breakdown
    langs = language_detection(db)
    lang_lines = []
    for lang, pct in langs.items():
        bar = make_visual_bar(int(pct), 100, width=15)
        lang_lines.append(f"  • {lang:<20} {pct:>5}%  {bar}")
    lang_output = "\n".join(lang_lines) if lang_lines else "  No languages detected."
    
    # 4. Peak Hours
    hourly = peak_hours(db)
    max_count = max(hourly.values()) if hourly.values() else 0
    blocks = []
    for h in range(24):
        count = hourly[h]
        if count == 0:
            blocks.append("░")
        elif max_count > 0 and count / max_count < 0.33:
            blocks.append("▄")
        elif max_count > 0 and count / max_count < 0.66:
            blocks.append("■")
        else:
            blocks.append("█")
            
    seg1 = "".join(blocks[0:6])
    seg2 = "".join(blocks[6:12])
    seg3 = "".join(blocks[12:18])
    seg4 = "".join(blocks[18:24])
    punch_card = f"00:00 {seg1} 06:00 {seg2} 12:00 {seg3} 18:00 {seg4} 23:59"
    
    top_hours = sorted(hourly.items(), key=lambda x: x[1], reverse=True)[:3]
    top_hours_parts = []
    for h, count in top_hours:
        if count > 0:
            am_pm = "AM" if h < 12 else "PM"
            display_h = h % 12
            if display_h == 0:
                display_h = 12
            top_hours_parts.append(f"{display_h} {am_pm} ({count} cmds)")
    top_hours_str = ", ".join(top_hours_parts) if top_hours_parts else "N/A"
    
    # Build complete report
    output_lines = [
        "📊 [bold]Deep History Statistics & Telemetry[/]",
        "",
        "[bold cyan]Activity Heatmap (Last 30 Days)[/]",
        "[dim]──────────────────────────────[/]",
        f"  {heatmap_str}",
        "",
        "[bold cyan]Peak Hours (Command Distribution)[/]",
        "[dim]────────────────────────────────[/]",
        f"  {punch_card}",
        f"  Top Active Hours: {top_hours_str}",
        "",
        "[bold cyan]Language Distribution[/]",
        "[dim]─────────────────────[/]",
        lang_output,
        "",
        "[bold cyan]Project Breakdown[/]",
        "[dim]─────────────────[/]",
    ]
    
    project_table_str = render_to_string(table)
    # Indent project table rows for cleaner layout
    indented_table = "\n".join("  " + line for line in project_table_str.split("\n"))
    
    output_lines.append(indented_table)
    
    return render_to_string(Text.from_markup("\n".join(output_lines).strip()))


def format_profile_output(db, limit: int = 10) -> str:
    """Format the SQLite database query profiling results into a beautiful, high-density report."""
    query_logs = getattr(db, "query_logs", [])
    
    if not query_logs:
        return "[yellow]No database queries were captured during execution.[/]"
        
    # 1. Slowest Queries
    slowest = sorted(query_logs, key=lambda x: x["duration"], reverse=True)[:limit]
    
    slow_table = Table(box=None, show_header=True, padding=(0, 2))
    slow_table.add_column("Query", style="cyan", header_style="bold cyan")
    slow_table.add_column("Duration", justify="right", style="green", header_style="bold green")
    
    for log in slowest:
        clean_query = " ".join(log["sql"].strip().split())
        if len(clean_query) > 80:
            clean_query = clean_query[:77] + "..."
        duration_ms = log["duration"] * 1000.0
        slow_table.add_row(clean_query, f"{duration_ms:.2f} ms")
        
    # 2. Most Frequent Queries (N+1 patterns)
    groups = defaultdict(list)
    for log in query_logs:
        normalized = " ".join(log["sql"].strip().split())
        groups[normalized].append(log["duration"])
        
    frequent = []
    for sql, durations in groups.items():
        count = len(durations)
        total_dur = sum(durations)
        avg_dur = total_dur / count
        frequent.append({
            "sql": sql,
            "count": count,
            "total_duration": total_dur,
            "avg_duration": avg_dur
        })
        
    # Sort by count DESC, then total_duration DESC
    frequent = sorted(frequent, key=lambda x: (x["count"], x["total_duration"]), reverse=True)[:limit]
    
    freq_table = Table(box=None, show_header=True, padding=(0, 2))
    freq_table.add_column("Query Template", style="cyan", header_style="bold cyan")
    freq_table.add_column("Count", justify="right", style="green", header_style="bold green")
    freq_table.add_column("Total Time", justify="right", style="green", header_style="bold green")
    freq_table.add_column("Avg Time", justify="right", style="green", header_style="bold green")
    
    for item in frequent:
        clean_query = item["sql"]
        if len(clean_query) > 80:
            clean_query = clean_query[:77] + "..."
        total_ms = item["total_duration"] * 1000.0
        avg_ms = item["avg_duration"] * 1000.0
        freq_table.add_row(
            clean_query,
            str(item["count"]),
            f"{total_ms:.2f} ms",
            f"{avg_ms:.2f} ms"
        )
        
    output_lines = [
        "⏱️  [bold]Database Query Profiler[/]",
        f"Captured {len(query_logs)} database queries during operations.",
        "",
        "[bold cyan]Slowest Queries[/]",
        "[dim]────────────────────────────────────────────────────────────────────────────────[/]",
    ]
    
    slow_table_str = render_to_string(slow_table)
    indented_slow = "\n".join("  " + line for line in slow_table_str.split("\n"))
    output_lines.append(indented_slow)
    
    output_lines.extend([
        "",
        "[bold cyan]Most Frequent Queries (Potential N+1 Read Patterns)[/]",
        "[dim]────────────────────────────────────────────────────────────────────────────────[/]",
    ])
    
    freq_table_str = render_to_string(freq_table)
    indented_freq = "\n".join("  " + line for line in freq_table_str.split("\n"))
    output_lines.append(indented_freq)
    
    return render_to_string(Text.from_markup("\n".join(output_lines).strip()))




