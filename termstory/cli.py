import os

import typer
from typing import Optional, List
from dateutil import parser as date_parser

# Supported tags for session categorization
TAGS = ["deploy", "debug", "setup", "test", "docs"]

from termstory.config import get_history_files, get_db_path
from termstory.parser import parse_all_histories
from termstory.session import create_sessions
from termstory.project import detect_projects
from termstory.database import Database
from termstory.date_utils import get_current_time, get_today_range
from termstory.formatter import format_search_results, format_today_output, format_project_output, format_insights_output, format_stats_output, format_profile_output, format_necromancer_score, format_rage_quit_signatures
import sqlite3

from rich.console import Console
from rich.table import Table
from rich.text import Text

import sys
import re

# Initialize rich console
console = Console()

def safe_init_db(db: Database) -> None:
    try:
        db.init_db()
    except sqlite3.DatabaseError as e:
        import time
        db_path = db.db_path
        if os.path.exists(db_path):
            backup_path = f"{db_path}.corrupt.{int(time.time())}.bak"
            os.rename(db_path, backup_path)
            Console(stderr=True).print(
                f"\n[bold yellow]Database Corrupted[/bold yellow]\n"
                f"Your TermStory database was corrupted. It has been moved to {backup_path}.\n"
                "Initializing a fresh database..."
            )
            db.init_db()
        else:
            Console(stderr=True).print(
                "\n[bold red]Database Error[/bold red]\n"
                f"Could not initialize database: {e}"
            )
            sys.exit(1)

def intercept_sys_argv():
    """Intercept positional date arguments (e.g. termstory 2026-06-02) and rewrite them
    to option flags so they do not conflict with subcommands in click/typer"""
    if len(sys.argv) > 1:
        # Intercept -reset and rewrite it to --reset
        for i in range(1, len(sys.argv)):
            if sys.argv[i] == "-reset":
                sys.argv[i] = "--reset"

        date_pattern = re.compile(r'^\d{4}-\d{2}-\d{2}$')
        first_arg = sys.argv[1]
        if date_pattern.match(first_arg):
            os.environ["TERMSTORY_DATE_OVERRIDE"] = first_arg
            if len(sys.argv) == 2:
                sys.argv[1] = "today"
            else:
                sys.argv.pop(1)

# Will be executed via main_entry() to intercept arguments before click/typer processes them

app = typer.Typer(
    help="TermStory CLI - Parse local shell history and explore your work patterns",
    no_args_is_help=False,
)

def run_ingestion(db: Database) -> None:
    """Helper to parse active history files and store them in the database"""
    history_files = get_history_files()
    if not history_files:
        # Case A — no history files found at all (truly fresh setup)
        Console(stderr=True).print(
            "\n[bold yellow]⚠️  No shell history files found.[/bold yellow]\n"
            "It looks like this might be a fresh setup. Open your terminal and run a few\n"
            "commands first, then re-run `termstory ui` to see your history.\n"
            "Tip: Add `setopt EXTENDED_HISTORY` to ~/.zshrc now so timestamps are recorded from the start.\n"
        )
        return
        
    # Case B — history file(s) exist but every one is empty (terminal opened,
    # but no commands ever typed)
    if all(os.path.getsize(path) == 0 for path in history_files):
        Console(stderr=True).print(
            "\n[bold yellow]⚠️  Your shell history file exists but is empty.[/bold yellow]\n"
            "Run some commands in your terminal first, then re-run `termstory ui`.\n"
        )
        return
        
    def discover_project_paths():
        import glob
        paths = []
        for root_dir in ["~/Projects", "~/src", "~/Developer", "~/Code", "~/Work", "~"]:
            expanded = os.path.expanduser(root_dir)
            if os.path.isdir(expanded):
                for git_dir in glob.glob(os.path.join(expanded, "*", ".git")):
                    paths.append(os.path.dirname(git_dir))
                if root_dir != "~":
                    for git_dir in glob.glob(os.path.join(expanded, "*", "*", ".git")):
                        paths.append(os.path.dirname(git_dir))
        return sorted(set(paths))

    commands = parse_all_histories(history_files, db=db, project_paths=discover_project_paths)
    if len(commands) == 0:
        Console(stderr=True).print(
            "\n[bold yellow]⚠️  Warning: Shell history parser returned 0 commands.[/bold yellow]\n"
            "Your history file might be empty, unreadable, or permission denied.\n"
            "If you are on macOS, please check and grant Full Disk Access to your Terminal app.\n"
        )
        
    sessions = create_sessions(commands)
    projects = detect_projects(sessions)
    db.save_data(projects, sessions, commands)
    
    # Ingest commits for each project: dynamically adjust search window based on the oldest command parsed
    from termstory.git_integration import get_project_commits
    if commands:
        oldest_ts = commands[0].timestamp
        since_ts = min(oldest_ts - 24 * 3600, int(get_current_time().timestamp()) - 90 * 24 * 3600)
    else:
        since_ts = int(get_current_time().timestamp()) - 90 * 24 * 3600
        
    is_deep_history = since_ts < int(get_current_time().timestamp()) - 90 * 24 * 3600
    git_timeout = 30 if is_deep_history else 10
        
    for p in projects:
        if p.id is not None and p.path:
            commits = get_project_commits(p.path, since_ts, timeout=git_timeout)
            if commits:
                db.save_commits(p.id, commits)
                
    # Auto-tag sessions
    from termstory.tags import auto_tag_all_sessions
    auto_tag_all_sessions(db)

@app.command("search")
def search_history(
    query: Optional[str] = typer.Argument(None, help="Search term/query across commits, commands, and project names"),
    project: Optional[str] = typer.Option(None, "--project", help="Filter matches by project name"),
    since: Optional[str] = typer.Option(None, "--since", help="Filter matches since date YYYY-MM-DD"),
    until: Optional[str] = typer.Option(None, "--until", help="Filter matches until date YYYY-MM-DD"),
    tag: Optional[List[str]] = typer.Option(None, "--tag", "-t", help="Filter matches by tag(s) (deploy, debug, setup, test, docs)"),
    limit: int = typer.Option(50, "--limit", help="Maximum number of search results to return"),
    detailed: bool = typer.Option(False, "--detailed", help="Show all commands and commits in matched sessions"),
):
    """Search across your work history (commits, commands, and projects) with advanced filters"""
    db_path = get_db_path()
    db = Database(db_path)
    safe_init_db(db)
    
    run_ingestion(db)
    
    since_ts = None
    if since:
        try:
            since_ts = int(date_parser.parse(since).timestamp())
        except Exception:
            Console(stderr=True).print(f"[bold red]Error: Could not parse date '{since}'[/]")
            raise typer.Exit(code=1)
            
    until_ts = None
    if until:
        try:
            until_ts = int(date_parser.parse(until).timestamp())
        except Exception:
            Console(stderr=True).print(f"[bold red]Error: Could not parse date '{until}'[/]")
            raise typer.Exit(code=1)
            
    tag_list = None
    if tag:
        tag_list = []
        for t in tag:
            t_clean = t.strip().lower()
            if t_clean not in TAGS:
                Console(stderr=True).print(f"[bold red]Error: Invalid tag '{t_clean}'.[/bold red] Valid tags: {', '.join(TAGS)}.")
                raise typer.Exit(code=1)
            tag_list.append(t_clean)
            
    from termstory.search import advanced_search
    results = advanced_search(
        db,
        query=query,
        project_filter=project,
        since_ts=since_ts,
        until_ts=until_ts,
        tag_filters=tag_list
    )
    
    # Limit results
    results = results[:limit]
    
    output = format_search_results(query or "", results, detailed=detailed)
    from rich.text import Text
    console.print(Text.from_ansi(output))


@app.command("today")
def show_today(
    compare: bool = typer.Option(True, "--compare/--no-compare", help="Compare with yesterday's metrics")
):
    """Show today's work summary"""
    db_path = get_db_path()
    db = Database(db_path)
    safe_init_db(db)
    
    run_ingestion(db)
    
    sessions = db.get_today_sessions()
    projects = db.get_all_projects_with_stats()
    
    compare_sessions = None
    if compare:
        start_ts, end_ts = get_today_range()
        yesterday_start = start_ts - 24 * 3600
        yesterday_end = end_ts - 24 * 3600
        compare_sessions = db.get_range_sessions(yesterday_start, yesterday_end)
        
    output = format_today_output(sessions, projects, compare_sessions=compare_sessions)
    from rich.text import Text
    console.print(Text.from_ansi(output))

@app.command("project")
def show_project(
    name: str = typer.Argument(..., help="Name or path of the project")
):
    """Show detailed history for a specific project"""
    db_path = get_db_path()
    db = Database(db_path)
    safe_init_db(db)
    
    run_ingestion(db)
    
    projects = db.get_all_projects_with_stats()
    
    target = None
    for p in projects:
        if name.lower() in p.name.lower() or (p.path and name.lower() in p.path.lower()):
            target = p
            break
            
    if not target:
        Console(stderr=True).print(f"[bold red]Error: Could not find project matching '{name}'[/]")
        raise typer.Exit(code=1)
        
    sessions = db.get_project_sessions(target.id, start_ts=0)
    
    output = format_project_output(sessions, target)
    from rich.text import Text
    console.print(Text.from_ansi(output))

@app.command("insights")
def show_insights():
    """Show overall developer insights and activity dashboard"""
    db_path = get_db_path()
    db = Database(db_path)
    safe_init_db(db)
    
    run_ingestion(db)
    
    from termstory.insights import analyze_all
    stats = analyze_all(db)
    
    from termstory.models import format_duration
    
    output_lines = [
        "📊 TermStory Executive Insights",
        "",
        "Metrics",
        "────────────────────────────────────────",
        f"Total Sessions : {stats['total_sessions']}",
        f"Total Commands : {stats['total_commands']}",
        f"Total Projects : {stats['total_projects']}",
        f"Coding Streak  : {stats['streak']} days",
        f"Vampire Index  : {stats['vampire_index']}%",
        f"RPG Class      : {stats['rpg_class']}",
        "",
        "Activity Patterns",
        "────────────────────────────────────────",
        f"Most Active Day  : {stats['most_active_day']}",
        f"Most Active Time : {stats['most_active_time']}",
        "",
        "Project Focus (Most Used)",
        "────────────────────────────────────────"
    ]
    
    top_projects = stats["most_used_projects"][:5]
    for i, (name, duration) in enumerate(top_projects, 1):
        output_lines.append(f"{i}. {name:<25} ({format_duration(duration)})")
        
    if not top_projects:
        output_lines.append("No project data available.")
        
    console.print("\n".join(output_lines))


@app.command("stats")
def show_stats():
    """Show detailed, high-density work statistics and telemetry"""
    db_path = get_db_path()
    db = Database(db_path)
    safe_init_db(db)
    
    run_ingestion(db)
    
    output = format_stats_output(db)
    from rich.text import Text
    console.print(Text.from_ansi(output))



@app.command("tags")
def show_tags(
    tag: Optional[str] = typer.Argument(None, help="Filter and list sessions by a specific tag (deploy, debug, setup, test, docs)"),
    rebuild: bool = typer.Option(False, "--rebuild", "-r", help="Force rebuild/re-evaluate tags for all sessions"),
    limit: int = typer.Option(50, "--limit", help="Limit number of listed sessions")
):
    """View a summary of tags or list sessions for a specific tag"""
    db_path = get_db_path()
    db = Database(db_path)
    safe_init_db(db)
    
    if rebuild:
        console.print("Rebuilding tags for all sessions...")
        from termstory.tags import auto_tag_all_sessions
        auto_tag_all_sessions(db)
        console.print("[bold green]✓ Tags successfully rebuilt![/bold green]")
        if not tag:
            # If no tag is given, we will show the updated tag summary
            pass
            
    # Standard ingestion if not rebuild
    if not rebuild:
        run_ingestion(db)
        
    # Get all projects map for listing
    cursor = db.get_connection().cursor()
    cursor.execute("SELECT id, name FROM projects")
    projects_map = {row[0]: row[1] for row in cursor.fetchall()}
    
    if not tag:
        # Show summary of tags
        cursor.execute("SELECT id, tags, duration_seconds FROM sessions")
        rows = cursor.fetchall()
        
        tag_counts = {t: 0 for t in TAGS}
        tag_durations = {t: 0 for t in TAGS}
        
        for s_id, tags_str, duration in rows:
            if tags_str:
                parts = [p.strip() for p in tags_str.split(",") if p.strip()]
                for p in parts:
                    if p in tag_counts:
                        tag_counts[p] += 1
                        tag_durations[p] += (duration or 0)
                        
        from termstory.models import format_duration
        
        output_lines = [
            "🏷️  TermStory Tags Summary",
            "────────────────────────────────────────"
        ]
        for t in TAGS:
            count = tag_counts[t]
            dur = tag_durations[t]
            output_lines.append(f"{t:<8} : {count:>3} sessions ({format_duration(dur)})")
            
        console.print("\n".join(output_lines))
    else:
        # List sessions filtered by tag
        tag = tag.strip().lower()
        if tag not in TAGS:
            console.print(f"[bold red]Error: Invalid tag '{tag}'.[/bold red] Valid tags: {', '.join(TAGS)}.")
            raise typer.Exit(1)
            
        conn = db.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, start_time, end_time, duration_seconds, project_id, ai_summary, tags
            FROM sessions
            WHERE tags LIKE ?
            ORDER BY start_time DESC
            LIMIT ?
        """, (f"%{tag}%", limit))
        session_rows = cursor.fetchall()
        
        if not session_rows:
            console.print(f"No sessions found with tag '{tag}'.")
            return
            
        from termstory.models import format_duration
        import datetime
        
        output_lines = [
            f"🏷️  Sessions tagged with '{tag}' (Showing top {limit})",
            "────────────────────────────────────────────────────────────────────────"
        ]
        
        for row in session_rows:
            s_id, start, end, duration, p_id, ai_sum, tags_str = row
            date_str = datetime.datetime.fromtimestamp(start).strftime("%Y-%m-%d %H:%M")
            proj_name = projects_map.get(p_id, "Other")
            dur_str = format_duration(duration or 0)
            
            # Form summary: priority to AI summary, then first command of session
            summary = ""
            if ai_sum:
                summary = ai_sum.replace("\n", " ").strip()
            else:
                cursor.execute("SELECT command FROM commands WHERE session_id = ? ORDER BY timestamp ASC LIMIT 1", (s_id,))
                cmd_row = cursor.fetchone()
                if cmd_row:
                    summary = cmd_row[0]
            
            if len(summary) > 40:
                summary = summary[:37] + "..."
                
            output_lines.append(f"{date_str}  {proj_name:<15}  {dur_str:<6}  {summary}")
            
        console.print("\n".join(output_lines))



@app.command("web")
def show_web():
    """Generate and open a beautiful HTML report of your work statistics"""
    db_path = get_db_path()
    db = Database(db_path)
    safe_init_db(db)
    
    run_ingestion(db)
    
    from termstory.web import generate_and_open_report
    generate_and_open_report(db)



@app.command("ask")
def ask_cmd(
    query: str = typer.Argument(..., help="Question to ask about your development history")
):
    """Ask natural language questions about your shell history and activity"""
    db_path = get_db_path()
    db = Database(db_path)
    safe_init_db(db)
    
    run_ingestion(db)
    
    from termstory.ask import search_ask, generate_answer
    from termstory.config import load_config
    
    sessions = search_ask(query, db)
    if not sessions:
        console.print("[yellow]No relevant history found for your query.[/yellow]")
        raise typer.Exit()
        
    config = load_config()
    with console.status("[bold green]Analyzing history and generating answer...[/bold green]"):
        answer = generate_answer(query, sessions, config)
        
    if not answer:
        from termstory.ai import get_last_ai_error
        err = get_last_ai_error()
        if err:
            console.print(f"[bold red]AI Error: {err}[/bold red]")
        else:
            console.print("[bold red]Failed to generate an answer.[/bold red]")
        raise typer.Exit(code=1)
        
    console.print(answer)


@app.command("predict")
def predict_cmd(
    top: int = typer.Option(3, "--top", help="Number of top project predictions to show"),
    json_out: bool = typer.Option(False, "--json", help="Output predictions as JSON"),
):
    """Predict what you will likely work on next (Pre-Cognitive Workspace)"""
    db_path = get_db_path()
    db = Database(db_path)
    safe_init_db(db)

    run_ingestion(db)

    from termstory.predict import Predictor, format_predict_output

    predictor = Predictor(db_path)
    result = predictor.predict(top_n=top)

    if json_out:
        import json
        from datetime import datetime

        def _serialise(obj):
            if isinstance(obj, datetime):
                return obj.isoformat()
            raise TypeError(f"Not serialisable: {type(obj)}")

        console.print(json.dumps(result, default=_serialise, indent=2))
        return

    output = format_predict_output(result)
    from rich.text import Text
    console.print(Text.from_ansi(output))


@app.command("anger-translator")
def anger_translator(
    project: Optional[str] = typer.Option(None, "--project", help="Filter matches by project name"),
    limit: int = typer.Option(5, "--limit", help="Maximum number of commits to analyze"),
):
    """
    Analyze recent git commits and their preceding terminal errors to translate them
    into the developer's real emotional states/roasts.
    """
    db_path = get_db_path()
    db = Database(db_path)
    safe_init_db(db)
    
    run_ingestion(db)
    
    conn = db.get_connection()
    try:
        cursor = conn.cursor()
        
        project_id = None
        if project:
            cursor.execute("SELECT id FROM projects WHERE name LIKE ?", (f"%{project}%",))
            row = cursor.fetchone()
            if row:
                project_id = row[0]
            else:
                Console(stderr=True).print(f"[bold red]Error: Project '{project}' not found.[/]")
                raise typer.Exit(code=1)
                
        if project_id is not None:
            cursor.execute("""
                SELECT hash, timestamp, message, cleaned_message, project_id
                FROM commits
                WHERE project_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """, (project_id, limit))
        else:
            cursor.execute("""
                SELECT hash, timestamp, message, cleaned_message, project_id
                FROM commits
                ORDER BY timestamp DESC
                LIMIT ?
            """, (limit,))
            
        commit_rows = cursor.fetchall()
        
        commit_data = []
        for hash_val, ts, msg, clean_msg, p_id in commit_rows:
            cursor.execute("""
                SELECT cmd.command, cmd.exit_code
                FROM commands cmd
                JOIN sessions s ON cmd.session_id = s.id
                WHERE cmd.timestamp >= ? AND cmd.timestamp < ? AND cmd.exit_code != 0 AND s.project_id = ?
                ORDER BY cmd.timestamp DESC
            """, (ts - 1800, ts, p_id))
            err_rows = cursor.fetchall()
            
            preceding_errors = [r[0] for r in err_rows]
            
            # Add sanitizer for privacy
            from termstory.sanitizer import sanitize_session_commands
            sanitized_errors, _ = sanitize_session_commands(preceding_errors)
    
            commit_data.append({
                "hash": hash_val,
                "timestamp": ts,
                "message": msg,
                "cleaned_message": clean_msg,
                "preceding_errors": sanitized_errors
            })
            
    finally:
        conn.close()
        
    if not commit_data:
        console.print("No git commits found to analyze.")
        return
        
    from termstory.config import load_config
    config = load_config()
    ai_enabled = config.get("ai_enabled", False)
    provider = config.get("ai_provider", "disabled")
    
    if ai_enabled and provider != "disabled":
        from termstory.ai import translate_git_anger
        api_key = config.get(f"providers.{provider}.api_key", config.get(f"{provider}_api_key", ""))
        api_base_url = config.get(f"providers.{provider}.api_base_url", config.get(f"{provider}_api_base_url", ""))
        model_name = config.get(f"providers.{provider}.model_name", config.get(f"{provider}_model_name", ""))
        
        if not api_base_url:
            if provider == "groq":
                api_base_url = "https://api.groq.com/openai/v1"
            elif provider == "openai":
                api_base_url = "https://api.openai.com/v1"
            elif provider == "ollama":
                api_base_url = "http://localhost:11434/v1"
                
        console.print("[bold yellow]Translating developer's git blame and terminal frustration...[/]")
        translation = translate_git_anger(
            commit_data,
            api_key=api_key,
            api_base_url=api_base_url,
            model_name=model_name,
            provider=provider
        )
        if translation:
            from termstory.formatter import format_anger_translation
            formatted = format_anger_translation(translation)
            console.print(Text.from_ansi(formatted))
            return
            
    from termstory.formatter import format_anger_translation_heuristics
    formatted = format_anger_translation_heuristics(commit_data)
    console.print(Text.from_ansi(formatted))


@app.command("fortune-teller")
def fortune_teller(
    limit: int = typer.Option(5, "--limit", help="Maximum number of chaotic sessions to analyze"),
):
    """
    Detect late-night chaotic sessions and generate bug predictions.
    """
    db_path = get_db_path()
    db = Database(db_path)
    safe_init_db(db)
    
    run_ingestion(db)
    
    from termstory.insights import detect_late_night_chaotic_sessions
    sessions = detect_late_night_chaotic_sessions(db)
    sessions = sessions[:limit]
    
    if not sessions:
        console.print("No late-night chaotic sessions detected.")
        return
        
    from termstory.config import load_config
    config = load_config()
    ai_enabled = config.get("ai_enabled", False)
    provider = config.get("ai_provider", "disabled")
    
    if ai_enabled and provider != "disabled":
        from termstory.ai import predict_bugs_from_sessions
        api_key = config.get(f"providers.{provider}.api_key", config.get(f"{provider}_api_key", ""))
        api_base_url = config.get(f"providers.{provider}.api_base_url", config.get(f"{provider}_api_base_url", ""))
        model_name = config.get(f"providers.{provider}.model_name", config.get(f"{provider}_model_name", ""))
        
        if not api_base_url:
            if provider == "groq":
                api_base_url = "https://api.groq.com/openai/v1"
            elif provider == "openai":
                api_base_url = "https://api.openai.com/v1"
            elif provider == "ollama":
                api_base_url = "http://localhost:11434/v1"
                
        console.print("[bold yellow]Foretelling potential bugs from late-night chaotic session telemetry...[/]")
        predictions = predict_bugs_from_sessions(
            sessions,
            api_key=api_key,
            api_base_url=api_base_url,
            model_name=model_name,
            provider=provider
        )
        if predictions:
            from termstory.formatter import format_bug_predictions
            formatted = format_bug_predictions(predictions)
            console.print(Text.from_ansi(formatted))
            return
            
    from termstory.formatter import format_bug_predictions_heuristics
    formatted = format_bug_predictions_heuristics(sessions)
    console.print(Text.from_ansi(formatted))



def cleanup_shell_marker():
    """Remove TermStory injection block and old injections from shell rc files"""
    rc_files = ["~/.zshrc", "~/.bashrc", "~/.bash_profile"]
    
    block_pattern = re.compile(
        r'\n?# >>> TermStory Shell History Timestamp Support >>>.*?'
        r'# <<< TermStory Shell History Timestamp Support <<<\n?',
        re.DOTALL
    )
    
    old_block_pattern_zsh = re.compile(r'\n?# TermStory Timekeeping\nsetopt EXTENDED_HISTORY\n?')
    old_block_pattern_bash = re.compile(r'\n?# TermStory Timekeeping\nexport HISTTIMEFORMAT="%F %T "\n?')
    
    for rc in rc_files:
        path = os.path.expanduser(rc)
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    content = f.read()
                
                new_content = block_pattern.sub('\n', content)
                new_content = old_block_pattern_zsh.sub('\n', new_content)
                new_content = old_block_pattern_bash.sub('\n', new_content)
                
                if new_content != content:
                    with open(path, "w") as f:
                        f.write(new_content.rstrip() + '\n')
            except Exception:
                pass

def perform_reset():
    """Reset all TermStory state, configuration, and database files on disk"""
    import shutil
    import os
    from termstory.config import get_app_dir

    dirs_to_clean = set()

    # 1. Legacy directory
    dirs_to_clean.add(os.path.expanduser("~/.termstory"))

    # 2. Currently resolved app directories
    dirs_to_clean.add(get_app_dir("config"))
    dirs_to_clean.add(get_app_dir("data"))

    # 3. All potential XDG Base directories
    if os.name != "nt":
        if os.environ.get("XDG_CONFIG_HOME"):
            dirs_to_clean.add(os.path.join(os.environ["XDG_CONFIG_HOME"], "termstory"))
        dirs_to_clean.add(os.path.expanduser("~/.config/termstory"))
        
        if os.environ.get("XDG_DATA_HOME"):
            dirs_to_clean.add(os.path.join(os.environ["XDG_DATA_HOME"], "termstory"))
        dirs_to_clean.add(os.path.expanduser("~/.local/share/termstory"))

    for db_dir in dirs_to_clean:
        if os.path.exists(db_dir):
            try:
                if os.path.islink(db_dir) or os.path.isfile(db_dir):
                    os.unlink(db_dir)
                elif os.path.isdir(db_dir):
                    shutil.rmtree(db_dir)
            except Exception:
                pass
                
    # 4. Remove global ignore file
    ignore_file = os.path.expanduser("~/.termstoryignore")
    if os.path.exists(ignore_file):
        try:
            os.unlink(ignore_file)
        except Exception:
            pass

    cleanup_shell_marker()
    console.print("\n[bold green]✨ TermStory state, configuration, and database have been successfully reset![/]")

@app.command("ui")
def show_ui(
    days: int = typer.Option(90, "--days", help="Number of days of history to display"),
    all_history: bool = typer.Option(False, "--all", help="Display all recorded history"),
):
    """Launch the interactive terminal dashboard user interface"""
    db_path = get_db_path()
    db = Database(db_path)
    safe_init_db(db)
    
    run_ingestion(db)
    
    # White-Glove Onboarding Prompt:
    # If the parser flags that shell history timestamps are missing, pause the standard
    # boot sequence to offer automatic configuration injection into the user's shell
    # config file. We detect the user's default shell (bash vs zsh) and write the
    # appropriate timekeeping directive. Never perform this without explicit user consent ('Y').
    missing_ts = os.environ.get("TERMSTORY_MISSING_TIMESTAMPS") == "1"
    _cfg = {}
    if missing_ts:
        from termstory.config import load_config, save_config
        _cfg = load_config()
    if missing_ts and not _cfg.get("has_seen_timestamp_prompt", False):
        # Detect default shell from $SHELL (e.g. /bin/bash, /usr/bin/zsh)
        shell_path = os.environ.get("SHELL", "")
        is_bash = "bash" in os.path.basename(shell_path).lower()
        
        if is_bash:
            # On macOS bash login shells read ~/.bash_profile; elsewhere ~/.bashrc
            if sys.platform == "darwin" and os.path.exists(os.path.expanduser("~/.bash_profile")):
                config_path = os.path.expanduser("~/.bash_profile")
            else:
                config_path = os.path.expanduser("~/.bashrc")
            config_directive = '\n# >>> TermStory Shell History Timestamp Support >>>\nexport HISTTIMEFORMAT="%F %T "\n# <<< TermStory Shell History Timestamp Support <<<\n'
        else:
            config_path = os.path.expanduser("~/.zshrc")
            config_directive = "\n# >>> TermStory Shell History Timestamp Support >>>\nsetopt EXTENDED_HISTORY\n# <<< TermStory Shell History Timestamp Support <<<\n"
        config_display = config_path.replace(os.path.expanduser("~"), "~", 1)
        
        console.print("\n[bold yellow]⚠️  TermStory needs your shell to record timestamps to build your timeline accurately.[/bold yellow]")
        try:
            response = input(
                "Would you like TermStory to automatically enable history timestamps in your shell config file (`~/.zshrc` or `~/.bashrc`)? [Y/n] "
            ).strip().lower()
            if response == "":
                response = "y"
        except (KeyboardInterrupt, EOFError):
            console.print()
            response = "n"
            
        if response in ("y", "yes"):
            try:
                already_exists = False
                if os.path.exists(config_path):
                    with open(config_path, "r") as f:
                        content = f.read()
                    if (is_bash and "HISTTIMEFORMAT" in content) or (not is_bash and "EXTENDED_HISTORY" in content):
                        already_exists = True

                if not already_exists:
                    with open(config_path, "a") as f:
                        f.write(config_directive)
                
                _cfg["has_seen_timestamp_prompt"] = True
                save_config(_cfg)
                
                if not already_exists:
                    console.print(
                        f"\n[bold green]✅ Done! Please restart your terminal or run `source {config_display}` "
                        f"for the changes to take effect, then run `termstory ui` again.[/bold green]\n"
                    )
                    sys.exit(0)
                else:
                    console.print(
                        "\n[yellow]⚡ Shell configuration already has timestamps enabled! Proceeding to TUI...[/yellow]\n"
                    )
            except Exception as e:
                console.print(f"[bold red]Error modifying {config_display}: {e}[/bold red]")
                console.print("Continuing with legacy history fallback...")
        elif response in ("n", "no"):
            _cfg["has_seen_timestamp_prompt"] = True
            save_config(_cfg)
            console.print("Continuing with legacy history fallback...")
        else:
            console.print("Invalid response. Continuing with legacy history fallback...")
            
    from termstory.tui import TermStoryWorkspace
    app_tui = TermStoryWorkspace(db, days_limit=None if all_history else days)
    app_tui.run()
    
    if getattr(app_tui, "was_reset", False):
        perform_reset()
    else:
        try:
            from termstory.config import load_config, save_config
            _cfg = load_config()
            if (
                not _cfg.get("has_seen_onboarding_reminder", False)
                and not _cfg.get("has_seen_onboarding", False)
                and _cfg.get("active_provider", "disabled") == "disabled"
            ):
                console.print("\n[bold yellow]💡 Hint: TermStory works best with AI summaries enabled![/bold yellow]")
                console.print("To configure a local or cloud AI provider (Groq, OpenAI, Ollama), run:")
                console.print("  [cyan]termstory config set active_provider groq[/cyan] (or [cyan]openai[/cyan] / [cyan]ollama[/cyan])")
                console.print("  [cyan]termstory config set providers.groq.api_key <your_api_key>[/cyan]")
                console.print("Alternatively, press [bold]? [/bold]inside the TUI to open the onboarding settings anytime.\n")
                _cfg["has_seen_onboarding_reminder"] = True
                save_config(_cfg)
        except Exception as e:
            Console(stderr=True).print(f"[dim]Note: failed to persist onboarding reminder flag: {e}[/dim]")

@app.command("reset")
def reset_cmd():
    """Reset all TermStory state, configuration, and database"""
    perform_reset()


@app.command("optimize")
def optimize_cmd():
    """Run VACUUM on the database to defragment it and reclaim disk space"""
    db_path = get_db_path()
    db = Database(db_path)
    safe_init_db(db)
    
    console.print("Running database optimization (VACUUM)...")
    db.optimize()
    console.print("[bold green]✅ Database optimized successfully![/]")


@app.command("agy")
def run_agy():
    """Launch 'agy -p' for quick analysis (requires 'agy' command to be installed)"""
    import shutil
    import subprocess
    agy_path = shutil.which("agy")
    if not agy_path:
        Console(stderr=True).print("[bold red]Error: 'agy' command not found on PATH.[/]")
        raise typer.Exit(code=1)
    
    try:
        subprocess.run(["agy", "-p"], check=True)
    except subprocess.CalledProcessError as e:
        Console(stderr=True).print(f"[bold red]Error running 'agy -p': {e}[/]")
        raise typer.Exit(code=e.returncode)
    except KeyboardInterrupt:
        raise typer.Exit(code=130)


@app.command("replay")
def replay_cmd(
    session_id: Optional[int] = typer.Argument(None, help="ID of the session to replay. If not provided, the most recent session is used."),
    speed: float = typer.Option(1.0, "--speed", "-s", help="Playback speed multiplier (e.g. 2.0 for fast, 0.5 for slow)"),
    list_sessions: bool = typer.Option(False, "--list", "-l", help="List recent sessions to choose from")
):
    """Replay a selected terminal session in fast or slow motion"""
    db_path = get_db_path()
    db = Database(db_path)
    safe_init_db(db)
    
    run_ingestion(db)
    
    from termstory.replay import run_replay
    run_replay(db, session_id=session_id, speed=speed, list_sessions=list_sessions)


@app.command("export")
def export_cmd(
    format: str = typer.Option("json", "--format", "-f", help="Export format: 'json' or 'csv'"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Output file path (prints to stdout if omitted)"),
    project: Optional[str] = typer.Option(None, "--project", "-p", help="Filter by project name or path"),
    since: Optional[str] = typer.Option(None, "--since", "-s", help="Filter by since duration (e.g. '7' for 7 days, or YYYY-MM-DD)")
):
    """Export history sessions and commands as JSON or CSV"""
    db_path = get_db_path()
    db = Database(db_path)
    safe_init_db(db)
    
    run_ingestion(db)
    
    from termstory.exporter import fetch_export_data, export_json, export_csv
    
    try:
        sessions = fetch_export_data(db, project_filter=project, since_str=since)
    except ValueError as e:
        Console(stderr=True).print(f"[bold red]Error:[/] {e}")
        raise typer.Exit(code=1)
        
    if not sessions:
        Console(stderr=True).print("[yellow]No sessions found matching filters.[/yellow]")
        raise typer.Exit(code=0)
        
    fmt = format.lower().strip()
    if fmt == "json":
        export_json(sessions, db, output_file=output)
    elif fmt == "csv":
        export_csv(sessions, db, output_file=output)
    else:
        Console(stderr=True).print(f"[bold red]Error: Unsupported format '{format}'. Use 'json' or 'csv'.[/]")
        raise typer.Exit(code=1)

@app.command("archive")
def archive_cmd(
    days: int = typer.Option(90, "--days", "-d", help="Archive sessions older than N days"),
    archive_db: Optional[str] = typer.Option(None, "--archive-db", "-a", help="Path to the archive SQLite database file (defaults to archive.db next to the main database)"),
):
    """Archive old sessions and associated data (older than N days) to a separate database."""
    db_path = get_db_path()
    db = Database(db_path)
    safe_init_db(db)
    
    # Run ingestion first to ensure all recent history is parsed/saved
    run_ingestion(db)
    
    if not archive_db:
        archive_db = os.path.join(os.path.dirname(db_path), "archive.db")
    else:
        archive_db = os.path.realpath(os.path.abspath(os.path.expanduser(archive_db)))
        
    console.print(f"Archiving data older than [bold]{days}[/] days...")
    console.print(f"Main Database: [bold]{db_path}[/]")
    console.print(f"Archive Database: [bold]{archive_db}[/]")
    
    from termstory.archive import archive_old_data
    try:
        stats = archive_old_data(db_path, archive_db, days)
        console.print("[bold green]✅ Archiving completed successfully![/]")
        console.print(f"  Sessions archived: [bold]{stats['sessions']}[/]")
        console.print(f"  Commands archived: [bold]{stats['commands']}[/]")
        console.print(f"  Commits archived: [bold]{stats['commits']}[/]")
        console.print(f"  Macro summaries archived: [bold]{stats['macro_summaries']}[/]")
    except Exception as e:
        console.print(f"[bold red]Error during archiving: {e}[/]")
        raise typer.Exit(code=1)

@app.command("backup")
def backup_cmd():
    """Create a timestamped backup of the TermStory database."""
    from termstory.backup import backup_db
    backup_path = backup_db()
    console.print(f"[bold green]✅ Backup created at {backup_path}[/]")

@app.command("restore")
def restore_cmd(backup_path: str = typer.Argument(..., help="Path to the backup .db file to restore")):
    """Restore the TermStory database from a backup file."""
    from termstory.backup import restore_db
    try:
        restore_db(backup_path)
        console.print(f"[bold green]✅ Database restored from {backup_path}[/]")
    except FileNotFoundError as e:
        console.print(f"[bold red]Error: {e}[/]")
        raise typer.Exit(code=1)




# ==========================================
# TIMELINE COMMAND
@app.command("timeline")
def timeline_cmd(
    days: int = typer.Option(30, "--days", help="Number of days to include in the timeline")
) -> None:
    """Render an ASCII visual timeline of activity over recent days"""
    db_path = get_db_path()
    db = Database(db_path)
    safe_init_db(db)
    run_ingestion(db)
    from termstory.timeline import render_timeline
    output = render_timeline(db, days=days)
    typer.echo(output)

@app.command("notebook")
def notebook_cmd(
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Output markdown file path (prints to stdout if omitted)"),
    project: Optional[str] = typer.Option(None, "--project", "-p", help="Filter by project name or path"),
    since: Optional[str] = typer.Option(None, "--since", "-s", help="Filter by since duration (e.g. '7' for 7 days, or YYYY-MM-DD)"),
    all_commands: bool = typer.Option(False, "--all-commands", help="Include all commands (including navigation/utility noise)"),
    reverse: bool = typer.Option(False, "--reverse", help="Sort days reverse-chronologically (latest first)"),
):
    """Export history sessions as a Markdown notebook/journal grouped by day"""
    db_path = get_db_path()
    db = Database(db_path)
    safe_init_db(db)
    
    run_ingestion(db)
    
    from termstory.exporter import fetch_export_data
    from termstory.notebook import generate_notebook
    
    try:
        sessions = fetch_export_data(db, project_filter=project, since_str=since)
    except ValueError as e:
        Console(stderr=True).print(f"[bold red]Error:[/] {e}")
        raise typer.Exit(code=1)
        
    if not sessions:
        Console(stderr=True).print("[yellow]No sessions found matching filters.[/yellow]")
        raise typer.Exit(code=0)
        
    markdown_content = generate_notebook(sessions, db, all_commands=all_commands, reverse=reverse)
    
    if output and output != "-":
        with open(output, "w", encoding="utf-8") as f:
            f.write(markdown_content)
        console.print(f"[bold green]✅ Notebook successfully exported to {output}[/]")
    else:
        sys.stdout.write(markdown_content)

# CONFIG SUBCOMMANDS
# ==========================================

config_app = typer.Typer(help="Manage TermStory configuration settings")

@config_app.command("set")
def config_set(key: str, value: str):
    """Set a configuration value (supports nested dot notation, e.g. providers.openai.api_key)"""
    from termstory.config import load_config, save_config, set_config_value, get_config_value
    config = load_config()
    
    current_val = get_config_value(config, key)
    if isinstance(current_val, bool):
        converted_value = value.lower() in ("true", "1", "yes")
    elif isinstance(current_val, int):
        try:
            converted_value = int(value)
        except ValueError:
            converted_value = value
    elif isinstance(current_val, float):
        try:
            converted_value = float(value)
        except ValueError:
            converted_value = value
    else:
        if key in ("ai_enabled", "has_seen_onboarding") or key.endswith(".ai_enabled") or key.endswith(".has_seen_onboarding"):
            converted_value = value.lower() in ("true", "1", "yes")
        elif "api_key" in key or "token" in key or "password" in key:
            converted_value = value
        else:
            try:
                if "." in value:
                    converted_value = float(value)
                elif value.isdigit() and value.startswith("0") and len(value) > 1:
                    converted_value = value
                else:
                    converted_value = int(value)
            except ValueError:
                converted_value = value
        
    set_config_value(config, key, converted_value)
    save_config(config)
    
    set_val = get_config_value(config, key)
    console.print(f"[green]Set config key '{key}' to '{set_val}'[/]")

@config_app.command("get")
def config_get(key: str):
    """Get a configuration value (supports nested dot notation)"""
    from termstory.config import load_config, get_config_value
    config = load_config()
    val = get_config_value(config, key)
    if val is not None:
        console.print(f"{val}")
    else:
        Console(stderr=True).print(f"[bold red]Error: Config key '{key}' not found[/]")
        raise typer.Exit(code=1)

@config_app.command("list")
def config_list():
    """List all current configuration values, flattening nested paths"""
    from termstory.config import load_config
    config = load_config()
    
    def flatten_dict(d: dict, prefix: str = "") -> list:
        items = []
        for k, v in d.items():
            new_key = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                items.extend(flatten_dict(v, new_key))
            else:
                items.append((new_key, v))
        return items

    flat_config = flatten_dict(config)
    
    from rich.box import SIMPLE
    table = Table(title="🔧 TermStory Configuration", box=SIMPLE, border_style="cyan")
    table.add_column("Key", style="cyan bold")
    table.add_column("Value", style="green")
    
    for k, v in sorted(flat_config):
        val_str = str(v)
        # Mask keys that represent an API key
        if ("api_key" in k.lower() or "api-key" in k.lower()) and v:
            val_str = v[:6] + "..." + v[-4:] if len(v) > 10 else "[SET]"
        table.add_row(k, val_str)
        
    console.print(table)

@app.command("obs")
def obs():
    """Toggle DeepWiki observability for Hermes (.env and config.yaml)"""
    from termstory.hermes_obs import run_toggle
    run_toggle()

@app.command("remind")
def remind_cmd(
    text: Optional[str] = typer.Argument(
        None,
        help="Reminder message/phrase (e.g. 'remind me about coding in 2 days' or 'fixing bug')"
    ),
    days: Optional[int] = typer.Option(
        None, "--days", "-d",
        help="Number of days until the reminder is due (override or default if phrase doesn't specify)"
    ),
    list_all: bool = typer.Option(
        False, "--list", "-l",
        help="List all active/pending reminders"
    ),
    complete: Optional[int] = typer.Option(
        None, "--complete", "-c",
        help="Complete a reminder by its ID"
    ),
    show_completed: bool = typer.Option(
        False, "--show-completed", "-a",
        help="Show completed reminders when listing"
    )
):
    """Set, list, or complete reminders based on sessions."""
    from datetime import datetime
    import time
    from termstory.reminder import load_reminders, add_reminder, complete_reminder
    
    # Complete a reminder
    if complete is not None:
        success = complete_reminder(complete)
        if success:
            console.print(f"[bold green]✅ Marked reminder #{complete} as completed![/]")
        else:
            console.print(f"[bold red]Error: Reminder #{complete} not found.[/]")
            raise typer.Exit(code=1)
        raise typer.Exit()
        
    # Set a reminder (if text is provided, or if days is provided)
    if text is not None:
        db_path = get_db_path()
        db = Database(db_path)
        safe_init_db(db)
        
        try:
            rem = add_reminder(text, days=days, db=db)
            due_date = datetime.fromtimestamp(rem["due_at"]).strftime("%Y-%m-%d")
            console.print(
                f"[bold green]🔔 Reminder set successfully![/]\n"
                f"ID: [cyan]#{rem['id']}[/]\n"
                f"About: '{rem['about']}'\n"
                f"Project: [magenta]{rem['project_name']}[/]\n"
                f"Due in: {rem['days']} days (on {due_date})"
            )
        except ValueError as e:
            console.print(f"[bold red]Error:[/] {e}")
            raise typer.Exit(code=1)
        raise typer.Exit()
        
    # Otherwise list reminders (if list_all is true, or if no args are passed at all)
    reminders = load_reminders()
    if not reminders:
        console.print("[yellow]No reminders found.[/yellow]")
        raise typer.Exit()
        
    # Filter reminders
    filtered = reminders if show_completed else [r for r in reminders if r.get("status") == "pending"]
    
    if not filtered:
        console.print("[yellow]No pending reminders found.[/yellow]")
        raise typer.Exit()
        
    from rich.box import SIMPLE
    table = Table(title="⏰ TermStory Reminders", box=SIMPLE, border_style="cyan")
    table.add_column("ID", style="cyan bold")
    table.add_column("Status", style="bold")
    table.add_column("Project", style="magenta")
    table.add_column("Task / About", style="white")
    table.add_column("Due Date / Time Left", style="green")
    
    now = time.time()
    for r in filtered:
        r_id = f"#{r['id']}"
        status_val = r.get("status", "pending")
        if status_val == "completed":
            status_str = "[green]Completed[/green]"
        else:
            status_str = "[yellow]Pending[/yellow]"
            
        proj = r.get("project_name", "Other")
        about = r.get("about", "")
        
        due_at = r.get("due_at", 0)
        due_str = datetime.fromtimestamp(due_at).strftime("%Y-%m-%d")
        
        # Calculate time difference
        diff = due_at - now
        if status_val == "completed":
            time_left = "Completed"
        elif diff < 0:
            days_overdue = int(abs(diff) // 86400)
            if days_overdue == 0:
                time_left = "[red]Overdue today[/red]"
            else:
                time_left = f"[red]Overdue by {days_overdue}d[/red]"
        else:
            days_left = int(diff // 86400)
            if days_left == 0:
                time_left = "Due today"
            else:
                time_left = f"Due in {days_left}d"
                
        table.add_row(r_id, status_str, proj, about, f"{due_str} ({time_left})")
        
    console.print(table)


@app.command("profile")
def show_profile(
    limit: int = typer.Option(10, "--limit", "-l", help="Maximum number of queries to display in each category")
):
    """Profile database query execution times and identify N+1 read patterns"""
    db_path = get_db_path()
    db = Database(db_path)
    safe_init_db(db)
    
    run_ingestion(db)
    
    output = format_profile_output(db, limit)
    from rich.text import Text
    console.print(Text.from_ansi(output))


app.add_typer(config_app, name="config")


def version_callback(value: bool):
    if value:
        from termstory import __version__
        typer.echo(f"termstory version {__version__}")
        raise typer.Exit()

@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    date: Optional[str] = typer.Option(None, "--date", help="Date override (YYYY-MM-DD) for commands"),
    reset: bool = typer.Option(False, "--reset", help="Reset all TermStory state, configuration, and database"),
    version: Optional[bool] = typer.Option(
        None,
        "--version",
        callback=version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
):
    """TermStory - local shell history parsing and session summaries"""
    if reset:
        perform_reset()
        raise typer.Exit()

    if date:
        try:
            date_parser.parse(date)
            os.environ["TERMSTORY_DATE_OVERRIDE"] = date
        except Exception:
            Console(stderr=True).print(f"[bold red]Error: Invalid date format '{date}'[/]")
            raise typer.Exit(code=1)
            
    if ctx.invoked_subcommand is None:
        # No subcommand, fallback to today's report
        show_ui()


@app.command("rpg-class")
def show_rpg_class():
    """Assign and display your daily RPG class alter ego based on command patterns"""
    db_path = get_db_path()
    db = Database(db_path)
    safe_init_db(db)
    
    run_ingestion(db)
    
    from termstory.insights import analyze_all
    stats = analyze_all(db)
    rpg_info = stats.get("rpg_info", {})
    
    # Retrieve all commands to pass to AI if enabled
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT command FROM commands")
    commands = [r[0] for r in cursor.fetchall()]
    conn.close()
    
    # AI biography generation if enabled
    bio = None
    from termstory.config import load_config
    config = load_config()
    ai_enabled = config.get("ai_enabled", False)
    provider = config.get("ai_provider", "disabled")
    
    if ai_enabled and provider != "disabled":
        from termstory.ai import generate_rpg_bio
        api_key = config.get(f"providers.{provider}.api_key", config.get(f"{provider}_api_key", ""))
        api_base_url = config.get(f"providers.{provider}.api_base_url", config.get(f"{provider}_api_base_url", ""))
        model_name = config.get(f"providers.{provider}.model_name", config.get(f"{provider}_model_name", ""))
        
        if not api_base_url:
            if provider == "groq":
                api_base_url = "https://api.groq.com/openai/v1"
            elif provider == "openai":
                api_base_url = "https://api.openai.com/v1"
            elif provider == "ollama":
                api_base_url = "http://localhost:11434/v1"
                
        console.print("[bold yellow]Querying LLM to generate your custom RPG developer biography...[/]")
        bio = generate_rpg_bio(
            rpg_info.get("class_name", "Terminal Nomad"),
            commands,
            api_key=api_key,
            api_base_url=api_base_url,
            model_name=model_name,
            provider=provider
        )
        
    from termstory.formatter import format_rpg_class
    output = format_rpg_class(rpg_info, bio)
    console.print(Text.from_ansi(output))


@app.command("vampire-index")
def show_vampire_index():
    """Calculate and display your Vampire Coder Index (late-night coding intensity)"""
    db_path = get_db_path()
    db = Database(db_path)
    safe_init_db(db)
    
    run_ingestion(db)
    
    from termstory.insights import analyze_all
    stats = analyze_all(db)
    metrics = stats.get("vampire_metrics", {})
    
    from termstory.formatter import format_vampire_index
    output = format_vampire_index(metrics)
    console.print(Text.from_ansi(output))


@app.command("necromancer")
def show_necromancer():
    """Calculate and display your Project Necromancer Score (resurrected projects)"""
    db_path = get_db_path()
    db = Database(db_path)
    safe_init_db(db)
    
    run_ingestion(db)
    
    from termstory.insights import analyze_all
    stats = analyze_all(db)
    necromancer_info = stats.get("necromancer_info", {"score": 0, "resurrections": []})
    
    output = format_necromancer_score(necromancer_info)
    console.print(Text.from_ansi(output))


@app.command("rage-quit")
def show_rage_quit():
    """Identify and display your Rage-Quit Signatures (final command before 12h+ inactivity)"""
    db_path = get_db_path()
    db = Database(db_path)
    safe_init_db(db)
    
    run_ingestion(db)
    
    from termstory.insights import analyze_all
    stats = analyze_all(db)
    rage_quit_info = stats.get("rage_quit_info", {"signatures": [], "events": [], "total_events": 0})
    
    output = format_rage_quit_signatures(rage_quit_info)
    console.print(Text.from_ansi(output))




def main_entry():
    intercept_sys_argv()
    app()

def cli():
    main_entry()

if __name__ == "__main__":
    main_entry()
