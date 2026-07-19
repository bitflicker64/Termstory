import sys
from datetime import datetime
from typing import List, Dict, Any, Optional

from termstory.database import Database
from termstory.models import Session, Command, Project, format_duration
from termstory.formatter import _is_noise_command

def generate_notebook(
    sessions: List[Session],
    db: Database,
    all_commands: bool = False,
    reverse: bool = False
) -> str:
    """Generate a markdown notebook representation of sessions grouped by day.

    Args:
        sessions: List of Session objects.
        db: Database instance for retrieving project names/paths if not loaded.
        all_commands: If True, include noise commands in the output.
        reverse: If True, sort days reverse-chronologically (latest first).

    Returns:
        A formatted markdown string.
    """
    # 1. Pre-fetch project info to avoid querying in a loop
    project_ids = list(set(s.project_id for s in sessions if s.project_id is not None))
    projects = db.get_projects_by_ids(project_ids)
    project_map = {p.id: p for p in projects}

    # 2. Group sessions by date
    sessions_by_date: Dict[str, List[Session]] = {}
    for s in sessions:
        date_str = s.date_str
        if date_str not in sessions_by_date:
            sessions_by_date[date_str] = []
        sessions_by_date[date_str].append(s)

    # 3. Sort dates
    sorted_dates = sorted(sessions_by_date.keys(), reverse=reverse)

    lines = []
    
    def format_time(ts: Optional[int]) -> str:
        if ts is None:
            return "In progress"
        t_str = datetime.fromtimestamp(ts).strftime("%I:%M %p")
        if t_str.startswith("0"):
            t_str = t_str[1:]
        return t_str

    for date in sorted_dates:
        day_sessions = sessions_by_date[date]
        
        # Date Header
        lines.append(f"# {date}\n")

        # Group sessions by project for this day
        proj_sessions: Dict[str, List[Session]] = {}
        for s in day_sessions:
            proj_id = s.project_id
            proj = project_map.get(proj_id) if proj_id is not None else None
            proj_name = proj.name if proj else "Other"
            if not proj_name or proj_name.strip() == "":
                proj_name = "Other"
            proj_sessions.setdefault(proj_name, []).append(s)

        # Calculate project durations for this day
        proj_durations = {}
        for name, p_sess in proj_sessions.items():
            proj_durations[name] = sum(s.duration_seconds for s in p_sess)

        # Sort projects by duration descending to show most active project first
        sorted_proj_names = sorted(proj_durations.keys(), key=lambda k: proj_durations[k], reverse=True)

        # Projects section
        lines.append("## Projects")
        for name in sorted_proj_names:
            duration_str = format_duration(proj_durations[name])
            lines.append(f"- **{name}** ({duration_str})")
        lines.append("")

        # Timeline & AI Summaries section
        lines.append("## Timeline & AI Summaries")

        for name in sorted_proj_names:
            lines.append(f"\n### {name}")
            
            # Sort sessions of this project chronologically on this day
            sorted_s = sorted(proj_sessions[name], key=lambda s: s.start_time)

            for s in sorted_s:
                # Format time range
                start_formatted = format_time(s.start_time)
                end_formatted = format_time(s.end_time)
                
                if s.start_time == s.end_time:
                    time_range = f"{start_formatted} (1s)"
                else:
                    time_range = f"{start_formatted} - {end_formatted} ({s.duration_readable})"

                lines.append(f"\n* **{time_range}**")

                # AI Summary
                if s.ai_summary:
                    summary_lines = s.ai_summary.strip().split("\n")
                    lines.append("  - **AI Summary**:")
                    for sl in summary_lines:
                        lines.append(f"    {sl}")

                # Commits
                if s.commits:
                    lines.append("  - **Commits**:")
                    for commit in s.commits:
                        short_hash = commit.get("hash", "")[:7]
                        msg = commit.get("cleaned_message") or commit.get("message") or ""
                        msg = msg.split("\n")[0].strip()
                        lines.append(f"    - `{short_hash}`: {msg}")

                # Commands
                commands = s.commands
                if not all_commands:
                    commands = [cmd for cmd in s.commands if not _is_noise_command(cmd.command)]

                if commands:
                    lines.append("  - **Commands**:")
                    lines.append("    ```bash")
                    for cmd in commands:
                        lines.append(f"    {cmd.command}")
                    lines.append("    ```")

        # Add empty line separator between days
        lines.append("")

    return "\n".join(lines)
