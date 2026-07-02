import csv
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional
from dateutil import parser as date_parser

from termstory.database import Database
from termstory.models import Session, Command, Project

_FAR_FUTURE_TS: int = 9_999_999_999 # ~year 2286, safely beyond any real session timestamp

def _get_timestamp(dt: datetime) -> int:
    """Safely get Unix timestamp from datetime object on all platforms, including Windows."""
    if dt.tzinfo is None:
        try:
            dt = dt.astimezone()
        except OSError:
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
        now = datetime.now()
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
    """Convert a list of Session objects into a serializable list of dictionaries."""
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
        
        for cmd in s.commands:
            session_dict["commands"].append({
                "command_id": cmd.id,
                "timestamp": cmd.timestamp,
                "timestamp_iso": _safe_fromtimestamp(cmd.timestamp).isoformat(),
                "command": cmd.command,
                "exit_code": cmd.exit_code,
                "is_legacy": cmd.is_legacy,
                "recovery_source": cmd.recovery_source
            })
            
        for commit in s.commits:
            session_dict["commits"].append({
                "hash": commit.get("hash"),
                "timestamp": commit.get("timestamp"),
                "timestamp_iso": _safe_fromtimestamp(commit.get("timestamp")).isoformat() if commit.get("timestamp") else None,
                "message": commit.get("message"),
                "cleaned_message": commit.get("cleaned_message")
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
    """Export the list of sessions as CSV, with one row per command."""
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
            
            # Serialize commits to a semicolon-separated string
            commits_str = "; ".join(
                f"{c.get('hash', '')[:7]}: {c.get('cleaned_message', '')}"
                for c in s.commits
            )
            
            # Since every session must have at least one command, we iterate commands.
            # In case a session is somehow empty, we still write it.
            commands = s.commands if s.commands else [None]
            
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
                        "command_text": cmd.command,
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
