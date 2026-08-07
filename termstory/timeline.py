"""termstory.timeline

Utility to render a simple ASCII visual timeline of sessions per day.

Provides:
- `render_timeline(db: Database, days: int = 30) -> str`
  Returns a string containing a date column and a bar representing total
  session duration for each day in the last `days` days.

The output is meant for terminal consumption and uses a fixed-width bar.
"""

from datetime import datetime, timedelta
from typing import Dict, List

from termstory.database import Database
from termstory.date_utils import get_current_time
from termstory.models import Session

def _aggregate_sessions_by_day(sessions: List[Session]) -> Dict[str, int]:
    """Aggregate total duration (seconds) per day.

    Returns a mapping from ``YYYY-MM-DD`` string to total duration seconds.
    """
    daily: Dict[str, int] = {}
    for s in sessions:
        if s.is_legacy:
            continue
        day = datetime.fromtimestamp(s.start_time).strftime("%Y-%m-%d")
        daily.setdefault(day, 0)
        dur = s.duration_seconds or 0
        daily[day] += dur
    return daily

def _make_bar(value: int, max_value: int, width: int = 40) -> str:
    """Create a bar of length proportional to *value*.

    *value* is the duration for a single day, *max_value* is the maximum
    duration across all days. The bar uses the ``█`` character. If *max_value*
    is 0 we return an empty bar.
    """
    if max_value == 0:
        return " " * width
    proportion = value / max_value
    filled = int(round(proportion * width))
    return "█" * filled + " " * (width - filled)


def render_timeline(db: Database, days: int = 30) -> str:
    """Render an ASCII timeline of the last *days* days.

    The function queries the database for sessions within the window, aggregates
    total active time per day and prints a simple bar graph. The most recent
    day appears at the bottom (chronological order).
    """
    if days <= 0:
        raise ValueError("days must be greater than 0")
    now = get_current_time()
    start_ts = int((now - timedelta(days=days)).timestamp())
    # Fetch sessions in range
    sessions = db.get_range_sessions(start_ts, int(now.timestamp()))
    daily = _aggregate_sessions_by_day(sessions)
    # Ensure all dates are represented
    all_dates = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days - 1, -1, -1)]
    for d in all_dates:
        daily.setdefault(d, 0)
    max_dur = max(daily.values()) if daily else 0
    lines: List[str] = []
    header = f"{'Date':<12} | Activity"
    separator = "-" * (len(header) + 2)
    lines.append(header)
    lines.append(separator)
    for date in all_dates:
        bar = _make_bar(daily[date], max_dur)
        lines.append(f"{date:<12} | {bar}")
    return "\n".join(lines)
