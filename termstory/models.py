from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Dict

def format_duration(seconds: int) -> str:
    """Format duration in seconds to human readable form like 2h 15m, 15m, or 45s"""
    if seconds <= 0:
        return "0s"
    if seconds < 60:
        return f"{seconds}s"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    
    parts = []
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0 or not parts:
        parts.append(f"{minutes}m")
        
    return " ".join(parts)

@dataclass
class Command:
    timestamp: int              # Unix timestamp
    command: str                # Full command text
    exit_code: int = 0          # 0 = success, 1+ = error
    duration: Optional[int] = None # Seconds (if available, else None)
    session_id: Optional[int] = None   # FK to sessions table
    project_id: Optional[int] = None   # FK to projects table
    id: Optional[int] = None    # Primary key in DB (if stored)
    is_legacy: bool = False     # True = synthetic timestamp (no real timestamp in history file)
    recovery_source: Optional[str] = None
    # Human-readable string describing HOW the Timestamp Detective recovered this command's
    # timestamp.  Examples:
    #   "git log: myapp@a3f9b2c"              → matched a git commit message
    #   "stat: package.json mtime"            → file system stat on a created file
    #   "brew Cellar/jq"                      → Homebrew Cellar directory mtime
    #   "docker image inspect: my-app"        → docker image creation timestamp
    #   "Interpolated (between X and Y)"      → mathematically placed between two anchors
    # None for all commands that had real EXTENDED_HISTORY timestamps.

    
    @property
    def readable_time(self) -> str:
        """Return human-readable timestamp"""
        return datetime.fromtimestamp(self.timestamp).strftime("%H:%M:%S")

@dataclass
class Session:
    id: Optional[int]
    start_time: int             # Unix timestamp
    end_time: int               # Unix timestamp
    duration_seconds: int       # Calculated: end_time - start_time
    project_id: Optional[int]   # FK to projects
    commands: List[Command] = field(default_factory=list) # Commands in this session
    commits: List[Dict] = field(default_factory=list)     # Commits in this session
    ai_summary: Optional[str] = None
    is_generating_story: bool = False
    recent_generation: Optional[str] = None
    _cached_date_str: Optional[str] = None
    _cached_start_time_formatted: Optional[str] = None
    is_legacy: bool = False     # True = every command in this session has a synthetic timestamp
    tags: Optional[str] = None # Added for session tags as comma-separated list

    
    @property
    def duration_readable(self) -> str:
        """Return '2h 15m' format"""
        return format_duration(self.duration_seconds)

    @property
    def date_str(self) -> str:
        if not hasattr(self, "_cached_date_str") or self._cached_date_str is None:
            self._cached_date_str = datetime.fromtimestamp(self.start_time).strftime("%Y-%m-%d")
        return self._cached_date_str

    @property
    def start_time_formatted(self) -> str:
        if not hasattr(self, "_cached_start_time_formatted") or self._cached_start_time_formatted is None:
            self._cached_start_time_formatted = datetime.fromtimestamp(self.start_time).strftime("%I:%M %p")
        return self._cached_start_time_formatted

@dataclass
class Project:
    id: Optional[int]
    name: str                   # e.g., "Apache HugeGraph"
    path: str                   # e.g., "~/Project/incubator-hugegraph"
    first_seen: int             # Unix timestamp
    last_seen: int              # Unix timestamp
    session_count: int          # Number of sessions in this project
    total_time: int             # Total seconds worked on project
    project_context: Optional[str] = None  # New field for AI context per project

@dataclass
class DaysSummary:
    date: str                   # "Tuesday, June 02, 2026"
    total_time: int             # Seconds
    sessions: List[Session]     # Today's sessions
    projects: List[Project]     # Projects worked on today
    command_counts: Dict[str, int]  # {"git": 14, "docker": 12, "maven": 7}
