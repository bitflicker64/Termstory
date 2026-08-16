import csv
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from termstory import date_utils
from typing import List, Dict, Any, Optional
from dateutil import parser as date_parser

from termstory.database import Database
from termstory.date_utils import get_current_time
from termstory.models import Session, Command, Project
from termstory.sanitizer import redact_command, sanitize_session_commands

_FAR_FUTURE_TS: int = 9_999_999_999 # ~year 2286, safely beyond any real session timestamp

# Placeholder rendered in place of commands when a session is fully blacklisted
# (e.g. ``vault`` / ``aws configure`` / ``gh auth`` operations). Reuses the
# semantics documented in CONTRIBUTING.md so that raw security/authentication
# command text can never reach disk via the export paths.
_BLACKLISTED_SESSION_MARKER = "[REDACTED: Security/Authentication Operations]"

def _get_timestamp(dt: datetime) -> int:
    """Safely get Unix timestamp from datetime object on all platforms, including Windows."""
    if dt.tzinfo is None:
        try:
            dt = dt.astimezone()
        except OSError:
            # Wall-clock intentionally: need the real local UTC offset on Windows.
            local_offset = datetime.now().astimezone().utcoffset()
            dt = dt.replace(tzinfo=timezone(local_offset))
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    return int((dt - epoch).total_seconds())

def _safe_fromtimestamp(ts: float) -> datetime:
    """Safely convert a Unix timestamp to a local naive datetime on all platforms, including Windows."""
    utc_dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    try:
        local_dt = utc_dt.astimezone()
    except OSError:
        # Wall-clock intentionally: need the real local UTC offset on Windows.
        local_offset = datetime.now().astimezone().utcoffset()
        local_dt = utc_dt.astimezone(timezone(local_offset))
    return local_dt.replace(tzinfo=None)

def parse_since(since_str: Optional[str]) -> Optional[int]:
    """Parse a since string (either number of days or a date string) into a Unix timestamp."""
    if not since_str:
        return None
    
    since_str = since_str.strip()
    if since_str.isdigit():
        days = int(since_str)
        now = date_utils.get_current_time()
        dt = now - timedelta(days=days)
        # Start of that day
        start_of_day = datetime.combine(dt.date(), datetime.min.time())
        return _get_timestamp(start_of_day)
    
    try:
        dt = date_parser.parse(since_str)
        return _get_timestamp(dt)
    except Exception as e:
        raise ValueError(f"Invalid date or day count format '{since_str}': {e}")

def fetch_export_data(
    db: Database,
    project_filter: Optional[str] = None,
    since_str: Optional[str] = None
) -> List[Session]:
    """Fetch and filter sessions with their commands and commits from the database."""
    start_ts = 0
    if since_str:
        since_ts = parse_since(since_str)
        if since_ts is not None:
            start_ts = since_ts
            
    # Fetch all sessions in the range (up to far in the future)
    sessions = db.get_range_sessions(start_ts, _FAR_FUTURE_TS)
    
    # Get project info to map names/paths
    project_ids = list(set(s.project_id for s in sessions if s.project_id is not None))
    projects = db.get_projects_by_ids(project_ids)
    project_map = {p.id: p for p in projects}
    
    # Filter sessions by project if specified
    if project_filter:
        filter_lower = project_filter.lower()
        filtered_sessions = []
        for s in sessions:
            proj = project_map.get(s.project_id) if s.project_id is not None else None
            if proj:
                if filter_lower in proj.name.lower() or filter_lower in proj.path.lower():
                    filtered_sessions.append(s)
            else:
                if filter_lower in ("other", "general", "no project"):
                    filtered_sessions.append(s)
        sessions = filtered_sessions
        
    return sessions

def serialize_sessions_to_dict(sessions: List[Session], db: Database) -> List[Dict[str, Any]]:
    """Convert a list of Session objects into a serializable list of dictionaries.

    All command and commit text is routed through the shared sanitizer so that
    secrets are redacted and fully-blacklisted (security/auth) sessions cannot
    leak raw commands. See :mod:`termstory.sanitizer`.
    """
    # Pre-fetch project info
    project_ids = list(set(s.project_id for s in sessions if s.project_id is not None))
    projects = db.get_projects_by_ids(project_ids)
    project_map = {p.id: p for p in projects}

    serialized = []
    for s in sessions:
        proj = project_map.get(s.project_id) if s.project_id is not None else None

        session_dict = {
            "session_id": s.id,
            "start_time": s.start_time,
            "start_time_iso": _safe_fromtimestamp(s.start_time).isoformat(),
            "end_time": s.end_time,
            "end_time_iso": _safe_fromtimestamp(s.end_time).isoformat() if s.end_time is not None else None,
            "duration_seconds": s.duration_seconds,
            "duration_readable": s.duration_readable,
            "project_id": s.project_id,
            "project_name": proj.name if proj else "Other",
            "project_path": proj.path if proj else None,
            "ai_summary": s.ai_summary,
            "is_legacy": s.is_legacy,
            "commands": [],
            "commits": []
        }

        # Session-level blacklist gate + per-command redaction via the shared
        # sanitizer. A fully-blacklisted session (any command matching a
        # security/auth pattern) must never emit raw commands.
        raw_cmd_strings = [cmd.command for cmd in s.commands]
        sanitized_cmds, is_blacklisted = sanitize_session_commands(raw_cmd_strings)
        if is_blacklisted:
            command_texts = [_BLACKLISTED_SESSION_MARKER for _ in s.commands]
        else:
            command_texts = sanitized_cmds or []

        for idx, cmd in enumerate(s.commands):
            session_dict["commands"].append({
                "command_id": cmd.id,
                "timestamp": cmd.timestamp,
                "timestamp_iso": _safe_fromtimestamp(cmd.timestamp).isoformat(),
                "command": command_texts[idx] if idx < len(command_texts) else _BLACKLISTED_SESSION_MARKER,
                "exit_code": cmd.exit_code,
                "is_legacy": cmd.is_legacy,
                "recovery_source": cmd.recovery_source
            })

        for commit in s.commits:
            raw_message = commit.get("message")
            raw_cleaned = commit.get("cleaned_message")
            session_dict["commits"].append({
                "hash": commit.get("hash"),
                "timestamp": commit.get("timestamp"),
                "timestamp_iso": _safe_fromtimestamp(commit.get("timestamp")).isoformat() if commit.get("timestamp") else None,
                "message": redact_command(raw_message) if raw_message else raw_message,
                "cleaned_message": redact_command(raw_cleaned) if raw_cleaned else raw_cleaned
            })

        serialized.append(session_dict)

    return serialized

def export_json(
    sessions: List[Session],
    db: Database,
    output_file: Optional[str] = None
) -> None:
    """Export the list of sessions as a JSON array."""
    data = serialize_sessions_to_dict(sessions, db)
    
    if output_file and output_file != "-":
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    else:
        json.dump(data, sys.stdout, indent=2)
        sys.stdout.write("\n")

def export_csv(
    sessions: List[Session],
    db: Database,
    output_file: Optional[str] = None
) -> None:
    """Export the list of sessions as CSV, with one row per command.

    All command and commit text is routed through the shared sanitizer so that
    secrets are redacted and fully-blacklisted (security/auth) sessions cannot
    leak raw commands.
    """
    # Pre-fetch project info
    project_ids = list(set(s.project_id for s in sessions if s.project_id is not None))
    projects = db.get_projects_by_ids(project_ids)
    project_map = {p.id: p for p in projects}

    fieldnames = [
        "session_id",
        "session_start_time",
        "session_end_time",
        "session_duration_seconds",
        "project_name",
        "project_path",
        "session_ai_summary",
        "session_is_legacy",
        "command_id",
        "command_timestamp",
        "command_text",
        "command_exit_code",
        "command_is_legacy",
        "session_commits"
    ]

    # Write helper
    def write_rows(f):
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for s in sessions:
            proj = project_map.get(s.project_id) if s.project_id is not None else None

            # Serialize commits to a semicolon-separated string (redacted)
            commits_str = "; ".join(
                f"{c.get('hash', '')[:7]}: {redact_command(c.get('cleaned_message') or '')}"
                for c in s.commits
            )

            # Since every session must have at least one command, we iterate commands.
            # In case a session is somehow empty, we still write it.
            commands = s.commands if s.commands else [None]

            # Session-level blacklist gate + per-command redaction via the shared
            # sanitizer. A fully-blacklisted session must never emit raw commands.
            raw_cmd_strings = [cmd.command for cmd in commands if cmd is not None]
            sanitized_cmds, is_blacklisted = sanitize_session_commands(raw_cmd_strings)
            if is_blacklisted:
                command_texts = [_BLACKLISTED_SESSION_MARKER for _ in raw_cmd_strings]
            else:
                command_texts = sanitized_cmds or []

            text_iter = iter(command_texts)
            for cmd in commands:
                row = {
                    "session_id": s.id,
                    "session_start_time": _safe_fromtimestamp(s.start_time).isoformat(),
                    "session_end_time": _safe_fromtimestamp(s.end_time).isoformat() if s.end_time is not None else "",
                    "session_duration_seconds": s.duration_seconds,
                    "project_name": proj.name if proj else "Other",
                    "project_path": proj.path if proj else "",
                    "session_ai_summary": s.ai_summary or "",
                    "session_is_legacy": s.is_legacy,
                    "session_commits": commits_str
                }

                if cmd:
                    row.update({
                        "command_id": cmd.id,
                        "command_timestamp": _safe_fromtimestamp(cmd.timestamp).isoformat(),
                        "command_text": next(text_iter, _BLACKLISTED_SESSION_MARKER),
                        "command_exit_code": cmd.exit_code,
                        "command_is_legacy": cmd.is_legacy
                    })
                else:
                    row.update({
                        "command_id": "",
                        "command_timestamp": "",
                        "command_text": "",
                        "command_exit_code": "",
                        "command_is_legacy": ""
                    })

                writer.writerow(row)

    if output_file and output_file != "-":
        with open(output_file, "w", encoding="utf-8", newline="") as f:
            write_rows(f)
    else:
        write_rows(sys.stdout)

def _escape_md_table_cell(value: Any) -> str:
    """Escape a value so it is safe to embed inside a Markdown table cell.

    Pipe characters (``|``) and newlines would otherwise break the table
    structure; backslashes are escaped first so the pipe escaping is not
    undone. ``None`` renders as an empty string.
    """
    if value is None:
        return ""
    text = str(value)
    text = text.replace("\\", "\\\\")
    text = text.replace("|", "\\|")
    # Newlines are not legal inside a table cell — collapse to spaces.
    text = text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    return text

def _escape_md_text(value: Any) -> str:
    """Escape a value for inline Markdown rendering (headings / prose).

    Collapses every line break to a single space so a value can never spawn
    additional Markdown blocks, then backslash-escapes the Markdown
    punctuation that could otherwise alter document structure — headings
    (``#``), emphasis (``*``/``_``), links (``[]()``), inline code
    (backticks), tables (``|``) and HTML (``<``/``>``). Ordinary readable
    text is preserved verbatim. ``None`` renders as an empty string.
    """
    if value is None:
        return ""
    text = str(value)
    # Normalize all line endings, then collapse them so the value stays on a
    # single line and cannot spawn additional Markdown blocks.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = " ".join(text.splitlines())
    # Escape backslashes first so the escaping below cannot be undone.
    text = text.replace("\\", "\\\\")
    for ch in "`*_[]()#|<>":
        text = text.replace(ch, "\\" + ch)
    return text

def _md_code_fence(content: Any, lang: str = "text") -> str:
    """Render arbitrary content inside a fenced Markdown code block.

    The fence length is derived from the longest run of backticks found in
    the content (minimum 3), so command text that itself contains backticks
    can never prematurely close the block. Content is emitted verbatim — it
    is *not* Markdown-rendered — which preserves commands, empty lines and
    Markdown-special characters exactly as sanitized.
    """
    text = "" if content is None else str(content)
    max_run = 0
    run = 0
    for ch in text:
        if ch == "`":
            run += 1
            if run > max_run:
                max_run = run
        else:
            run = 0
    fence_len = max(3, max_run + 1)
    fence = "`" * fence_len
    return f"{fence}{lang}\n{text}\n{fence}"

def _render_session_markdown(sdict: Dict[str, Any]) -> List[str]:
    """Render a single serialized session dict as Markdown lines."""
    session_id = _escape_md_table_cell(sdict.get("session_id", ""))
    project_name = _escape_md_text(sdict.get("project_name") or "Other")
    lines: List[str] = [f"## Session #{session_id} — {project_name}", ""]

    start_iso = sdict.get("start_time_iso") or "—"
    end_iso = sdict.get("end_time_iso") or "—"  # None/empty => "in progress"
    duration_readable = sdict.get("duration_readable") or "—"
    project_path = sdict.get("project_path")
    is_legacy = sdict.get("is_legacy", False)

    duration_display = duration_readable

    # Session / project metadata as an escaped table.
    lines.append("| Field | Value |")
    lines.append("| --- | --- |")
    lines.append(f"| Start | {_escape_md_table_cell(start_iso)} |")
    lines.append(f"| End | {_escape_md_table_cell(end_iso)} |")
    lines.append(f"| Duration | {_escape_md_table_cell(duration_display)} |")
    lines.append(f"| Project path | {_escape_md_table_cell(project_path if project_path is not None else '—')} |")
    lines.append(f"| Legacy session | {'Yes' if is_legacy else 'No'} |")
    lines.append("")

    # AI Summary (already sanitized). Rendered as escaped prose; only emitted
    # when present. It is not escaped as a table cell — it is prose, so the
    # Markdown structure chars are escaped instead.
    ai_summary = sdict.get("ai_summary")
    if ai_summary:
        lines.append("### AI Summary")
        lines.append("")
        lines.append(_escape_md_text(ai_summary))
        lines.append("")

    # Commands — always fenced so multiline / Markdown-special content is safe.
    commands = sdict.get("commands") or []
    lines.append("### Commands")
    lines.append("")
    if commands:
        cmd_texts = [c.get("command") or "" for c in commands]
        lines.append(_md_code_fence("\n".join(cmd_texts)))
        lines.append("")
    else:
        lines.append("_(no commands recorded)_")
        lines.append("")

    # Commits — rendered as an escaped table.
    commits = sdict.get("commits") or []
    lines.append("### Commits")
    lines.append("")
    if commits:
        lines.append("| Hash | Message |")
        lines.append("| --- | --- |")
        for c in commits:
            short_hash = (c.get("hash") or "")[:7]
            msg = c.get("cleaned_message") or c.get("message") or ""
            lines.append(
                f"| {_escape_md_table_cell(short_hash)} | {_escape_md_table_cell(msg)} |"
            )
        lines.append("")
    else:
        lines.append("_(no commits)_")
        lines.append("")

    return lines

def export_markdown(
    sessions: List[Session],
    db: Database,
    output_file: Optional[str] = None
) -> None:
    """Export the list of sessions as a Markdown document.

    Reuses :func:`serialize_sessions_to_dict`, which routes all command and
    commit text through the shared sanitizer so secrets are redacted and
    fully-blacklisted (security/auth) sessions render as the redaction marker
    — exactly as in the JSON and CSV exports. Only the *rendering* differs.
    """
    data = serialize_sessions_to_dict(sessions, db)

    lines: List[str] = ["# Termstory Export", ""]

    if not data:
        lines.append("_No sessions to export._")
        lines.append("")
    else:
        for sdict in data:
            lines.extend(_render_session_markdown(sdict))

    output = "\n".join(lines)
    if not output.endswith("\n"):
        output += "\n"

    if output_file and output_file != "-":
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(output)
    else:
        sys.stdout.write(output)
