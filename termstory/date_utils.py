import os
import calendar
from datetime import datetime, timedelta, time
from typing import Tuple
from dateutil import parser as date_parser

def get_current_time() -> datetime:
    """Return the current datetime, checking for TERMSTORY_DATE_OVERRIDE environment variable first"""
    override = os.environ.get("TERMSTORY_DATE_OVERRIDE")
    if override:
        try:
            return date_parser.parse(override)
        except Exception:
            pass
    return datetime.now()

def get_today_range() -> Tuple[int, int]:
    """Return Unix timestamps for the start and end of today"""
    now = get_current_time()
    start_of_today = datetime.combine(now.date(), time.min)
    end_of_today = datetime.combine(now.date(), time.max)
    return int(start_of_today.timestamp()), int(end_of_today.timestamp())

def get_week_range(last: bool = False) -> Tuple[int, int]:
    """Return Unix timestamps for Monday 00:00 to Sunday 23:59 of the current or last week"""
    base_date = get_current_time()
    if last:
        base_date = base_date - timedelta(days=7)
        
    # weekday() returns 0 for Monday, 6 for Sunday
    monday = base_date - timedelta(days=base_date.weekday())
    monday_start = datetime.combine(monday.date(), time.min)
    
    sunday = monday + timedelta(days=6)
    sunday_end = datetime.combine(sunday.date(), time.max)
    
    return int(monday_start.timestamp()), int(sunday_end.timestamp())

def get_month_range(year: int, month: int) -> Tuple[int, int]:
    """Return Unix timestamps for the start of the month 00:00 to the last day of the month 23:59"""
    _, last_day = calendar.monthrange(year, month)
    start_date = datetime(year, month, 1, 0, 0, 0)
    end_date = datetime(year, month, last_day, 23, 59, 59)
    return int(start_date.timestamp()), int(end_date.timestamp())

def format_date_range(start_ts: int, end_ts: int) -> str:
    """Format Unix timestamp range into a human-readable string (e.g. 'May 26 - June 02, 2026')"""
    start_dt = datetime.fromtimestamp(start_ts)
    end_dt = datetime.fromtimestamp(end_ts)
    
    if start_dt.year == end_dt.year:
        if start_dt.month == end_dt.month:
            return f"{start_dt.strftime('%B %d')} - {end_dt.strftime('%d, %Y')}"
        return f"{start_dt.strftime('%B %d')} - {end_dt.strftime('%B %d, %Y')}"
    return f"{start_dt.strftime('%B %d, %Y')} - {end_dt.strftime('%B %d, %Y')}"

def parse_date_range_helper(date_range_str: str) -> Tuple[int, int]:
    """Parse a date range string into (start_timestamp, end_timestamp) using get_current_time()."""
    now = get_current_time()
    dr = date_range_str.strip().lower()
    
    if dr == "today":
        start = datetime.combine(now.date(), time.min)
        end = datetime.combine(now.date(), time.max)
        return int(start.timestamp()), int(end.timestamp())
        
    elif dr == "yesterday":
        yesterday = now - timedelta(days=1)
        start = datetime.combine(yesterday.date(), time.min)
        end = datetime.combine(yesterday.date(), time.max)
        return int(start.timestamp()), int(end.timestamp())
        
    elif dr.endswith("days"):
        try:
            days = int(dr[:-4])
            start = datetime.combine((now - timedelta(days=days)).date(), time.min)
            end = datetime.combine(now.date(), time.max)
            return int(start.timestamp()), int(end.timestamp())
        except ValueError:
            pass
            
    elif ":" in dr:
        parts = dr.split(":", 1)
        try:
            start_dt = date_parser.parse(parts[0].strip())
            end_dt = date_parser.parse(parts[1].strip())
            start = datetime.combine(start_dt.date(), time.min)
            end = datetime.combine(end_dt.date(), time.max)
            return int(start.timestamp()), int(end.timestamp())
        except Exception as e:
            raise ValueError(f"Invalid date range format: {e}")
            
    # Try parsing as a single date
    try:
        dt = date_parser.parse(dr)
        start = datetime.combine(dt.date(), time.min)
        end = datetime.combine(dt.date(), time.max)
        return int(start.timestamp()), int(end.timestamp())
    except Exception:
        raise ValueError(f"Unknown date range format '{date_range_str}'")

