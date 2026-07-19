import logging
import os
import shutil
import sqlite3
import glob
from datetime import datetime
from termstory.config import get_db_path

logger = logging.getLogger(__name__)


def _get_backup_dir() -> str:
    """Return the directory where backups are stored. Creates it if missing."""
    db_path = get_db_path()
    backup_dir = os.path.join(os.path.dirname(db_path), "backups")
    os.makedirs(backup_dir, exist_ok=True)
    return backup_dir


def backup_db() -> str:
    """Create a timestamped backup of the TermStory database.

    Returns:
        The absolute path to the created backup file.
    """
    db_path = get_db_path()
    if not os.path.isfile(db_path):
        raise FileNotFoundError(f"TermStory database not found at {db_path}")
    backup_dir = _get_backup_dir()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(backup_dir, f"termstory_backup_{timestamp}.db")

    # Safely backup the SQLite database using backup API
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(backup_path)
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()

    # Rotate backups: keep at most 10 latest backups
    try:
        backups = sorted(glob.glob(os.path.join(backup_dir, "termstory_backup_*.db")))
        while len(backups) > 10:
            oldest = backups.pop(0)
            if os.path.exists(oldest):
                os.remove(oldest)
    except OSError as e:
        logger.warning("Failed to rotate old backups: %s", e)

    return backup_path


def restore_db(backup_path: str) -> None:
    """Restore the TermStory database from a backup file.

    Args:
        backup_path: Absolute path to the backup .db file.
    """
    if not os.path.isfile(backup_path):
        raise FileNotFoundError(f"Backup file not found at {backup_path}")
    db_path = get_db_path()
    # Ensure the destination directory exists
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    # Replace the current database with the backup using backup API
    src = sqlite3.connect(backup_path)
    dst = sqlite3.connect(db_path)
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()
