import logging
import os
import shutil
import sqlite3
import glob
import tempfile
from datetime import datetime
from termstory.config import get_db_path

logger = logging.getLogger(__name__)

# Core TermStory tables whose presence identifies a valid backup.
# Derived from the schema created by ``Database.init_db`` (see also
# ``docs/database-schema.md``).  Only the four fundamental data tables are
# required — supplementary/cache tables such as ``macro_summaries`` may be
# added or removed in future schema revisions without invalidating restores.
REQUIRED_TABLES = ("projects", "sessions", "commands", "commits")


class BackupError(Exception):
    """Raised when a backup file fails validation or cannot be safely restored.

    Unlike ``FileNotFoundError`` (raised when the backup *path* does not exist),
    ``BackupError`` signals that the file exists but is not a usable TermStory
    backup — e.g. it is not a SQLite database, is missing required tables, or
    failed ``PRAGMA integrity_check``.
    """


def _get_backup_dir() -> str:
    """Return the directory where backups are stored. Creates it if missing."""
    db_path = get_db_path()
    backup_dir = os.path.join(os.path.dirname(db_path), "backups")
    os.makedirs(backup_dir, exist_ok=True)
    return backup_dir


def _backup_creation_key(path: str) -> tuple:
    """Return a stable sorting key expressing backup creation order.

    Prefer the filesystem birth/creation timestamp where it is reliably exposed
    (macOS/BSD ``stat_result.st_birthtime``; on Windows ``st_ctime`` is creation
    time). Linux exposes no birth time, so ``st_ctime`` is the fallback. The
    filename is used only to break exact timestamp ties, keeping the overall
    order total and deterministic without trusting the wall-clock name.
    """
    st = os.stat(path)
    creation = getattr(st, "st_birthtime", st.st_ctime)
    return creation, os.path.basename(path)


def backup_db() -> str:
    """Create a timestamped backup of the TermStory database.

    Backup filenames use a microsecond-precision timestamp
    (``termstory_backup_YYYYMMDD_HHMMSS_mmmmmm.db``) so back-to-back backups
    never collide. Rotation picks the oldest backup by filesystem creation
    order rather than by the wall-clock timestamp embedded in the filename,
    because the wall clock can jump backward between backups.

    Returns:
        The absolute path to the created backup file.
    """
    db_path = get_db_path()
    if not os.path.isfile(db_path):
        raise FileNotFoundError(f"TermStory database not found at {db_path}")
    backup_dir = _get_backup_dir()
    # Wall-clock intentionally: backup filenames must be unique on disk.
    # Microsecond precision (%f) keeps consecutive backups distinct while the
    # fixed-width suffix preserves lexicographic = chronological ordering.
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
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
        # Order by filesystem creation time (see _backup_creation_key) rather
        # than by the timestamp embedded in the filename. If the wall clock runs
        # backward (NTP correction, VM snapshot restore), a *newer* backup can
        # receive an earlier name; lexicographic ordering would then rotate away
        # the newest backup instead of the oldest.
        backups = sorted(
            glob.glob(os.path.join(backup_dir, "termstory_backup_*.db")),
            key=_backup_creation_key,
        )
        while len(backups) > 10:
            oldest = backups.pop(0)
            if os.path.exists(oldest):
                os.remove(oldest)
    except OSError:
        # Rotation failure should not crash the backup process, but it must be visible.
        logger.exception("Failed to rotate old backups in %s", backup_dir)

    return backup_path


def _validate_backup(path: str) -> None:
    """Validate that *path* is a readable, structurally sound TermStory backup.

    Three checks are performed in order:

    1. **Readability** — the file can be queried as a SQLite database (the
       first read of ``sqlite_master`` forces header parsing, so non-database
       files are detected even though ``sqlite3.connect`` is lazy).
    2. **Schema** — the core TermStory tables listed in ``REQUIRED_TABLES`` are
       present.
    3. **Integrity** — ``PRAGMA integrity_check`` returns ``"ok"``.

    Raises:
        BackupError: if any check fails.
    """
    conn = sqlite3.connect(path)
    try:
        cursor = conn.cursor()
        # Force real parsing of the SQLite file header.  ``sqlite3.connect``
        # is lazy and will happily open an arbitrary file; the error only
        # surfaces when a query actually touches the database.
        try:
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
            existing_tables = {row[0] for row in cursor.fetchall()}
        except sqlite3.DatabaseError as exc:
            raise BackupError(
                f"Backup file is not a readable SQLite database: {exc}"
            ) from exc

        missing = [t for t in REQUIRED_TABLES if t not in existing_tables]
        if missing:
            raise BackupError(
                f"Backup file is missing required TermStory tables: "
                f"{', '.join(missing)}"
            )

        # ``integrity_check`` can itself raise DatabaseError on a structurally
        # corrupt file, so it must be wrapped separately.
        try:
            integrity = cursor.execute("PRAGMA integrity_check").fetchone()
        except sqlite3.DatabaseError as exc:
            raise BackupError(
                f"Backup file failed integrity check: {exc}"
            ) from exc
        if not integrity or integrity[0] != "ok":
            detail = integrity[0] if integrity else "no result"
            raise BackupError(
                f"Backup file failed integrity check: {detail}"
            )
    finally:
        conn.close()


def _sidecar_glob(base: str) -> list:
    """Return existing SQLite sidecar files (``-wal``, ``-shm``, ``-journal``)
    associated with *base* (without following into other databases)."""
    return [f"{base}{suffix}" for suffix in ("-wal", "-shm", "-journal")]


def _remove_sidecars(base: str) -> None:
    """Remove SQLite sidecar files (``-wal`` / ``-shm`` / ``-journal``) for
    the database at *base* path.  Missing files are silently skipped; removal
    failures are logged but never raised."""
    for sidecar in _sidecar_glob(base):
        if os.path.exists(sidecar):
            try:
                os.remove(sidecar)
            except OSError:
                logger.warning("Could not remove stale SQLite sidecar %s", sidecar)


def _cleanup_temp_db(temp_db_path: str) -> None:
    """Remove a temporary database file and any of its sidecar files.

    Safe to call even if the files do not exist.  Never raises.
    """
    if not temp_db_path:
        return
    for path in [temp_db_path] + _sidecar_glob(temp_db_path):
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                logger.warning("Could not remove temporary file %s", path)


def restore_db(backup_path: str) -> None:
    """Restore the TermStory database from a backup file.

    The restore is safe by construction.  The active database is **never**
    touched until the backup has passed every validation step:

    1. **Source validation** — *backup_path* is opened and verified to be a
       readable SQLite database.
    2. **Schema validation** — the backup is checked for the core TermStory
       tables (``projects``, ``sessions``, ``commands``, ``commits``).
    3. **Integrity check** — ``PRAGMA integrity_check`` succeeds.
    4. **Temporary restore** — the validated backup is copied into a
       temporary file in the same directory (and filesystem) as the active
       database, so the final swap can use ``os.replace``.
    5. **Validate the restored copy** — the temporary database is
       re-checked (readability, schema, integrity) before it is allowed to
       replace the active database.
    6. **Atomic replace** — stale WAL/SHM/journal sidecars of the old active
       database are removed and ``os.replace`` atomically swaps in the
       validated copy.

    If *any* step fails, the active database is left completely untouched and
    all temporary artifacts are cleaned up.

    Args:
        backup_path: Absolute path to the backup .db file.

    Raises:
        FileNotFoundError: If ``backup_path`` does not exist.
        BackupError: If the backup fails any validation step or the restore
            into the temporary database fails.
    """
    if not os.path.isfile(backup_path):
        raise FileNotFoundError(f"Backup file not found at {backup_path}")

    db_path = get_db_path()
    db_dir = os.path.dirname(db_path) or "."
    os.makedirs(db_dir, exist_ok=True)

    # --- Steps 1-3: Validate the source backup before touching the active DB ---
    _validate_backup(backup_path)

    # --- Step 4: Restore into a temporary file in the same directory ---
    fd, temp_db_path = tempfile.mkstemp(
        prefix=".termstory_restore_", suffix=".db", dir=db_dir
    )
    os.close(fd)  # close the raw fd so SQLite can open the file by path

    try:
        src_conn = sqlite3.connect(backup_path)
        dst_conn = sqlite3.connect(temp_db_path)
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
            src_conn.close()

        # --- Step 5: Validate the restored temporary database ---
        _validate_backup(temp_db_path)

        # --- Step 6: Atomically replace the active database ---
        # Remove stale WAL/SHM/journal sidecars from the old active DB so they
        # cannot be replayed against the new database after the swap.
        _remove_sidecars(db_path)
        os.replace(temp_db_path, db_path)
        # temp_db_path no longer exists — it is now db_path.
    except BaseException:
        # On any failure: clean up temporary artifacts and re-raise.
        # The active DB was never opened or modified, so it is untouched.
        _cleanup_temp_db(temp_db_path)
        raise
