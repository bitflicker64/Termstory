import sys
import time
from typing import Optional
from datetime import datetime
from rich.console import Console
from rich.table import Table

from termstory.database import Database
from termstory.models import Session, Command, Project, format_duration

console = Console()

MAX_REPLAY_GAP_SECONDS = 2.0
INITIAL_REPLAY_PAUSE_SECONDS = 0.5


def format_relative_time(seconds: int) -> str:
    sign = "+"
    if seconds < 0:
        sign = "-"
        seconds = abs(seconds)
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h > 0:
        return f"{sign}{h:02d}:{m:02d}:{s:02d}"
    return f"{sign}{m:02d}:{s:02d}"

def list_recent_sessions(db: Database) -> None:
    conn = db.get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT s.id, s.start_time, s.duration_seconds, p.name, s.ai_summary
            FROM sessions s
            LEFT JOIN projects p ON s.project_id = p.id
            ORDER BY s.start_time DESC
            LIMIT 20
        """)
        rows = cursor.fetchall()
    finally:
        conn.close()

    if not rows:
        console.print("[yellow]No sessions found in the database.[/yellow]")
        return

    table = Table(title="🎬 Recent TermStory Sessions", box=None)
    table.add_column("ID", style="cyan", justify="right")
    table.add_column("Start Time", style="green")
    table.add_column("Project", style="magenta")
    table.add_column("Duration", style="yellow")
    table.add_column("Summary", style="white")

    for row in rows:
        s_id, start_time, duration, proj_name, ai_summary = row
        dt_str = datetime.fromtimestamp(start_time).strftime("%Y-%m-%d %H:%M:%S")
        duration_str = format_duration(duration) if duration else "0s"
        p_name = proj_name or "Other"
        summary_str = ai_summary or ""
        if len(summary_str) > 50:
            summary_str = summary_str[:47] + "..."
        table.add_row(str(s_id), dt_str, p_name, duration_str, summary_str)

    console.print(table)

def run_replay(db: Database, session_id: Optional[int] = None, speed: float = 1.0, list_sessions: bool = False) -> None:
    if speed <= 0:
        console.print("[bold red]Error: Playback speed must be greater than 0.[/bold red]")
        sys.exit(1)

    if list_sessions:
        list_recent_sessions(db)
        return

    # Find target session
    if session_id is None:
        # Get most recent session ID
        conn = db.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM sessions ORDER BY start_time DESC LIMIT 1")
            row = cursor.fetchone()
            if not row:
                console.print("[bold yellow]No sessions found in the database. Run some commands first to populate your timeline.[/bold yellow]")
                return
            session_id = row[0]
        finally:
            conn.close()

    # Retrieve session details
    sessions = db.get_sessions_by_ids([session_id])
    if not sessions:
        console.print(f"[bold red]Error: Session #{session_id} not found.[/bold red]")
        sys.exit(1)
        
    session = sessions[0]
    
    # Retrieve project name
    project_name = "Other"
    if session.project_id is not None:
        projects = db.get_projects_by_ids([session.project_id])
        if projects:
            project_name = projects[0].name

    commands = sorted(session.commands, key=lambda c: c.timestamp)
    if not commands:
        console.print(f"[yellow]Session #{session_id} has no commands to replay.[/yellow]")
        return

    # Header
    date_str = datetime.fromtimestamp(session.start_time).strftime("%Y-%m-%d")
    start_time_str = datetime.fromtimestamp(session.start_time).strftime("%H:%M:%S")
    
    console.print()
    console.print(f"[bold cyan]🎬 Replaying Session #{session.id}[/bold cyan]")
    console.print(f"[bold]📂 Project:[/]  {project_name}")
    console.print(f"[bold]📅 Date:[/]     {date_str}")
    console.print(f"[bold]⏱️ Start:[/][dim]    {start_time_str} (Relative timeline starts at +00:00)[/]")
    console.print(f"[bold]⌛ Duration:[/] {session.duration_readable}")
    console.print(f"[dim]{'-' * 60}[/]")
    console.print()

    # Playback loop
    try:
        prev_timestamp = None
        for i, cmd in enumerate(commands):
            # 1. Idle delay before next command (except the first command)
            if prev_timestamp is not None:
                dt = cmd.timestamp - prev_timestamp
                # Calculate sleep duration, scale by speed, and cap at max delay
                wait_time = max(0.0, float(dt)) / speed
                wait_time = min(wait_time, MAX_REPLAY_GAP_SECONDS)
                if wait_time > 0:
                    time.sleep(wait_time)
            else:
                # Small initial pause
                time.sleep(min(INITIAL_REPLAY_PAUSE_SECONDS / speed, MAX_REPLAY_GAP_SECONDS))

            # Calculate relative offset
            offset_sec = cmd.timestamp - session.start_time
            offset_str = format_relative_time(offset_sec)
            
            # Print the prompt prefix
            console.print(f"[bold green][{offset_str}][/bold green] [bold cyan]$[/bold cyan] ", end="")
            
            # 2. Type out command string
            char_delay = 0.04 / speed  # slightly faster than 0.05 for snappier typing
            for char in cmd.command:
                sys.stdout.write(char)
                sys.stdout.flush()
                time.sleep(char_delay)

            # Wait briefly after typing before executing
            time.sleep(0.15 / speed)
            sys.stdout.write("\n")
            sys.stdout.flush()

            # 3. Print execution output indicator (exit code)
            if cmd.exit_code != 0:
                console.print(f"  [bold red]✘ (exit: {cmd.exit_code})[/bold red]")
            else:
                console.print("  [bold green]✔[/bold green]")
            
            prev_timestamp = cmd.timestamp
            
        console.print()
        console.print("[bold green]🏁 Playback finished.[/bold green]")
        console.print()
    except KeyboardInterrupt:
        console.print()
        console.print("\n[bold yellow]⏹️ Playback interrupted by user.[/bold yellow]")
        console.print()
