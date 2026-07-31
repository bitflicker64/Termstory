"""
agy.py — AI Pair Programmer Bridge

Launches ``agy -p`` (the AI pair-programmer CLI) from within TermStory,
bridging the developer's recent shell-history context into the live AI
session so the assistant starts warm instead of cold.

Design
------
* **Context gathering** — pulls the most recent N commands, the current
  project name/path, and recent git commit messages from the TermStory
  SQLite database.
* **Privacy-first** — every command and commit message is run through
  :func:`termstory.sanitizer.redact_command` before it leaves the local
  machine.  Sessions containing blacklisted commands (vault logins,
  ``aws configure``, etc.) are dropped entirely via
  :func:`termstory.sanitizer.should_blacklist_command`.
* **Graceful degradation** — if ``agy`` is not on ``PATH``, the command
  prints a friendly installation hint and exits ``1``.  If the TermStory
  database is empty or missing, the bridge still launches ``agy`` but
  with a minimal "no history available" context block.
* **Stdin bridging** — context is written to a temporary file and passed
  to ``agy`` via its ``-p`` flag, so the AI session inherits the
  developer's recent work without any copy-paste.

This module was introduced in v0.4.0 (issue #40).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from typing import List, Optional, Tuple

from rich.console import Console

from termstory.config import get_db_path
from termstory.database import Database
from termstory.sanitizer import redact_command, should_blacklist_command

console = Console()

# ── Constants ────────────────────────────────────────────────────────────────

#: Default number of recent commands to bridge into the agy session.
DEFAULT_CONTEXT_COMMANDS = 50

#: Hard cap to prevent generating a massive prompt.
MAX_CONTEXT_COMMANDS = 500

#: Default number of recent git commit messages to include.
DEFAULT_CONTEXT_COMMITS = 10

#: Exit code used by typer when the CLI tool is missing.
EXIT_AGY_NOT_FOUND = 1

#: Exit code used when the subprocess is interrupted by the user (Ctrl-C).
EXIT_INTERRUPTED = 130


# ── Context gathering ───────────────────────────────────────────────────────


def _gather_recent_commands(db: Database, limit: int) -> List[str]:
    """Return up to *limit* most recent commands, sanitized for AI export.

    Commands are redacted with :func:`redact_command`.  Any command that
    triggers :func:`should_blacklist_command` is skipped entirely so
    that credential-bearing commands never reach the AI session.
    """
    conn = db.get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT command FROM commands ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        )
        rows = cursor.fetchall()
    finally:
        conn.close()

    safe_commands: List[str] = []
    for (raw_cmd,) in rows:
        if not raw_cmd:
            continue
        if should_blacklist_command(raw_cmd):
            continue
        safe_commands.append(redact_command(raw_cmd))
    return safe_commands


def _gather_recent_commits(db: Database, limit: int) -> List[str]:
    """Return up to *limit* most recent commit messages, sanitized."""
    conn = db.get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT cleaned_message FROM commits ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        )
        rows = cursor.fetchall()
    finally:
        conn.close()

    safe_commits: List[str] = []
    for (msg,) in rows:
        if not msg:
            continue
        safe_commits.append(redact_command(msg))
    return safe_commits


def _detect_current_project(db: Database) -> Tuple[Optional[str], Optional[str]]:
    """Return ``(project_name, project_path)`` for the most recent session.

    Falls back to ``(None, None)`` if no sessions or projects exist.
    """
    conn = db.get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT p.name, p.path
            FROM sessions s
            LEFT JOIN projects p ON s.project_id = p.id
            WHERE s.project_id IS NOT NULL
            ORDER BY s.start_time DESC
            LIMIT 1
            """,
        )
        row = cursor.fetchone()
    finally:
        conn.close()

    if not row:
        return None, None
    return row[0], row[1]


def build_context_prompt(
    db: Database,
    *,
    num_commands: int = DEFAULT_CONTEXT_COMMANDS,
    num_commits: int = DEFAULT_CONTEXT_COMMITS,
) -> str:
    """Build the markdown context block passed to ``agy -p``.

    The prompt is structured so an AI pair programmer can immediately
    orient itself: project identity, recent commands, recent commits,
    and the current working directory.
    """
    num_commands = max(1, min(num_commands, MAX_CONTEXT_COMMANDS))
    num_commits = max(0, min(num_commits, 100))

    project_name, project_path = _detect_current_project(db)
    commands = _gather_recent_commands(db, num_commands)
    commits = _gather_recent_commits(db, num_commits)
    cwd = os.getcwd()

    lines: List[str] = []
    lines.append("# TermStory → agy Context Bridge")
    lines.append("")
    lines.append(
        "You are being launched as an AI pair programmer. The context below "
        "was gathered by TermStory from the developer's recent shell history "
        "and is provided to help you orient quickly."
    )
    lines.append("")

    # ── Project identity ───────────────────────────────────────────────
    lines.append("## Active Project")
    if project_name or project_path:
        if project_name:
            lines.append(f"- **Name:** {project_name}")
        if project_path:
            lines.append(f"- **Path:** {project_path}")
    else:
        lines.append("- No active project detected yet.")
    lines.append(f"- **Current working directory:** `{cwd}`")
    lines.append("")

    # ── Recent commands ────────────────────────────────────────────────
    lines.append(f"## Recent Shell Commands ({len(commands)})")
    if commands:
        # Commands are stored oldest→newest after the DESC query reversed them.
        for cmd in reversed(commands):
            lines.append("```bash")
            lines.append(cmd)
            lines.append("```")
    else:
        lines.append("_No commands available yet. Run `termstory` to ingest your history._")
    lines.append("")

    # ── Recent commits ─────────────────────────────────────────────────
    if commits:
        lines.append(f"## Recent Git Commits ({len(commits)})")
        for msg in reversed(commits):
            # Single-line commit messages render cleanly as list items.
            first_line = msg.strip().splitlines()[0] if msg.strip() else ""
            if first_line:
                lines.append(f"- {first_line}")
        lines.append("")

    # ── Footer ─────────────────────────────────────────────────────────
    lines.append("---")
    lines.append(
        "_All commands and commit messages above have been sanitized through "
        "TermStory's privacy redactor before leaving the local machine._"
    )
    lines.append("")

    return "\n".join(lines)


# ── agy discovery & launch ──────────────────────────────────────────────────


def find_agy() -> Optional[str]:
    """Return the absolute path to ``agy`` on ``PATH``, or ``None``."""
    return shutil.which("agy")


def _write_temp_context(prompt: str) -> str:
    """Write *prompt* to a temporary file and return its path.

    The file is opened in text mode with ``delete=False`` so the caller
    can pass the path to ``agy`` and then unlink it afterwards.
    """
    fd, path = tempfile.mkstemp(
        prefix="termstory-agy-context-",
        suffix=".md",
        dir=tempfile.gettempdir(),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(prompt)
    except Exception:
        os.unlink(path)
        raise
    return path


def launch_agy(
    *,
    context_prompt: str,
    extra_args: Optional[List[str]] = None,
    agy_path: Optional[str] = None,
) -> int:
    """Launch ``agy -p`` with *context_prompt* bridged in.

    The context is written to a temporary markdown file, and ``agy`` is
    invoked with ``-p <tempfile>``.  Stdin/stdout/stderr are inherited
    so the AI session is fully interactive.

    Args:
        context_prompt: The markdown context block from
            :func:`build_context_prompt`.
        extra_args: Additional arguments to append after ``-p <file>``.
        agy_path: Override the resolved ``agy`` path (used by tests).

    Returns:
        The subprocess exit code.
    """
    resolved = agy_path or find_agy()
    if not resolved:
        console.print(
            "[bold red]Error:[/bold red] 'agy' command not found on PATH.\n"
            "Install it with:  [cyan]npm install -g agy[/cyan]  "
            "or  [cyan]pip install agy[/cyan]\n"
            "See https://github.com/anthropics/agy for details."
        )
        return EXIT_AGY_NOT_FOUND

    context_file = _write_temp_context(context_prompt)
    try:
        cmd = [resolved, "-p", context_file]
        if extra_args:
            cmd.extend(extra_args)

        console.print(
            f"[dim]Launching agy with TermStory context "
            f"({len(context_prompt)} chars)...[/dim]"
        )
        try:
            result = subprocess.run(cmd, check=False)
            return result.returncode
        except KeyboardInterrupt:
            console.print("\n[dim]agy session interrupted.[/dim]")
            return EXIT_INTERRUPTED
    finally:
        try:
            os.unlink(context_file)
        except OSError:
            pass


# ── High-level entry point (called by cli.py) ───────────────────────────────


def run_agy_bridge(
    *,
    num_commands: int = DEFAULT_CONTEXT_COMMANDS,
    num_commits: int = DEFAULT_CONTEXT_COMMITS,
    no_context: bool = False,
    extra_args: Optional[List[str]] = None,
) -> int:
    """Top-level bridge orchestrator invoked by the ``termstory agy`` command.

    1. Locate ``agy`` on ``PATH`` (fail fast with a friendly message).
    2. Initialize the TermStory database (tolerating corruption).
    3. Build the sanitized context prompt.
    4. Launch ``agy -p <context-file>`` with inherited stdio.

    Args:
        num_commands: How many recent commands to bridge.
        num_commits: How many recent commit messages to bridge.
        no_context: If ``True``, launch ``agy -p`` without any TermStory
            context (behaves like the old stub command).
        extra_args: Pass-through arguments for ``agy`` itself.

    Returns:
        Process exit code.
    """
    # Fail fast if agy isn't installed — no point building context we can't use.
    if not find_agy():
        console.print(
            "[bold red]Error:[/bold red] 'agy' command not found on PATH.\n"
            "Install it with:  [cyan]npm install -g agy[/cyan]  "
            "or  [cyan]pip install agy[/cyan]"
        )
        return EXIT_AGY_NOT_FOUND

    if no_context:
        # Legacy mode: just run `agy -p` with no bridged context.
        try:
            result = subprocess.run(["agy", "-p"], check=False)
            return result.returncode
        except KeyboardInterrupt:
            return EXIT_INTERRUPTED

    # Build context from the TermStory database.
    db_path = get_db_path()
    db = Database(db_path)

    # Tolerate a missing/corrupt DB — fall back to a minimal context.
    try:
        db.init_db()
    except Exception as exc:
        console.print(
            f"[yellow]Warning:[/yellow] Could not initialize TermStory database "
            f"({exc}). Launching agy with no history context."
        )
        context_prompt = (
            "# TermStory → agy Context Bridge\n\n"
            "_TermStory database unavailable. No history context bridged._\n"
        )
    else:
        # Run ingestion so the DB has the latest commands before we query it.
        try:
            from termstory.cli import run_ingestion
            run_ingestion(db)
        except Exception:
            # Ingestion failures are non-fatal — we just bridge whatever is
            # already in the DB.
            pass

        context_prompt = build_context_prompt(
            db,
            num_commands=num_commands,
            num_commits=num_commits,
        )

    return launch_agy(
        context_prompt=context_prompt,
        extra_args=extra_args,
    )
