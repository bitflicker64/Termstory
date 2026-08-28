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
    """Return the SQLite sidecar file paths (``-wal``, ``-shm``, ``-journal``)
    that would be associated with the exact database at *base*.  No filesystem
    glob is performed — the three paths are derived deterministically so that
    only sidecars belonging to this exact database path are ever considered."""
    return [f"{base}{suffix}" for suffix in ("-wal", "-shm", "-journal")]


def _remove_orphaned_sidecars(base: str) -> None:
    """Remove SQLite sidecar files for the database at *base* path.

    This is only safe to call AFTER the replacement at *base* has succeeded,
    at which point any ``-wal``/``-shm``/``-journal`` file still present belongs
    to the *old* (now replaced) database and would otherwise be replayed against
    the new main file, corrupting it.  The new database is a clean single file,
    so it has no sidecars of its own to preserve.

    Raises:
        OSError: if a stale sidecar exists but cannot be removed.  A leftover
            stale sidecar is a correctness hazard (it can corrupt the new DB on
            the next open), so removal failure is surfaced rather than logged
            and silently ignored.
    """
    for sidecar in _sidecar_glob(base):
        if os.path.exists(sidecar):
            os.remove(sidecar)


def _cleanup_temp_db(temp_db_path: str) -> None:
    """Remove a temporary database file and any of its sidecar files.

    Safe to call even if the files do not exist.  Best-effort by design: a
    temporary artifact that cannot be removed is logged, never raised, so that
    the original restore error is preserved.  Never raises.
    """
    if not temp_db_path:
        return
    for path in [temp_db_path] + _sidecar_glob(temp_db_path):
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                logger.warning("Could not remove temporary file %s", path)


class _RestoreLock:
    """A cross-process advisory lock that serializes restore operations.

    Uses the same atomic ``O_CREAT | O_EXCL`` claim as the sleep daemon PID
    file, so concurrent restores (in the same process or across processes)
    cannot run the filesystem replacement at the same time.  It does not gate
    normal Termstory database operations — it only prevents two restores from
    racing each other while swapping the active database pathname.
    """

    def __init__(self, db_path: str):
        self._lock_path = f"{db_path}.restore.lock"
        self._fd = None

    def acquire(self) -> None:
        if self._fd is not None:
            return  # already held
        try:
            self._fd = os.open(
                self._lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666
            )
        except FileExistsError as exc:
            raise BackupError(
                "Another database restore is already in progress."
            ) from exc

    def release(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            finally:
                self._fd = None
        try:
            if os.path.exists(self._lock_path):
                os.remove(self._lock_path)
        except OSError:
            logger.warning("Could not remove restore lock file %s", self._lock_path)

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc_info):
        self.release()
        return False


def restore_db(backup_path: str) -> None:
    """Restore the TermStory database from a backup file.

    The restore is safe by construction.  The active database is **never**
    touched until the backup has passed every validation step, and it is only
    replaced once the replacement is known to have succeeded:

    1. **Source validation** — *backup_path* is opened and verified to be a
       readable SQLite database.
    2. **Schema validation** — the backup is checked for the core TermStory
       tables (``projects``, ``sessions``, ``commands``, ``commits``).
    3. **Integrity check** — ``PRAGMA integrity_check`` succeeds.
    4. **Temporary restore** — the validated backup is copied into a
       temporary file in the same directory (and filesystem) as the active
       database, then checkpointed so the copy is a self-contained single
       file, so the final swap can use ``os.replace``.
    5. **Validate the restored copy** — the temporary database is re-checked
       (readability, schema, integrity) before it is allowed to replace the
       active database.
    6. **Coordination** — a cross-process advisory lock prevents concurrent
       restores from racing, and any in-flight writer on the active database
       is quiesced before the swap.
    7. **Atomic replace** — ``os.replace`` atomically swaps the validated
       temporary copy in for the active database.
    8. **Post-replace cleanup** — only after the swap has succeeded are the
       *old* database's now-orphaned ``-wal``/``-shm``/``-journal`` sidecars
       removed, so the restored database cannot accidentally consume stale
       state belonging to the previous database.

    If *any* step before a successful ``os.replace`` fails, the active
    database and its WAL/SHM state are left completely untouched and all
    temporary artifacts are cleaned up.  A failed ``os.replace`` likewise
    leaves the original database and its sidecars fully intact.

    Args:
        backup_path: Absolute path to the backup .db file.

    Raises:
        FileNotFoundError: If ``backup_path`` does not exist.
        BackupError: If the backup fails any validation step, if coordination
            cannot be obtained, or if any restore operation fails.
    """
    if not os.path.isfile(backup_path):
        raise FileNotFoundError(f"Backup file not found at {backup_path}")

    db_path = get_db_path()
    db_dir = os.path.dirname(db_path) or "."
    os.makedirs(db_dir, exist_ok=True)

    # --- Steps 1-3: Validate the source backup before touching the active DB ---
    _validate_backup(backup_path)

    # --- Step 4: Restore into a temporary file in the same directory ---
    try:
        fd, temp_db_path = tempfile.mkstemp(
            prefix=".termstory_restore_", suffix=".db", dir=db_dir
        )
    except OSError as exc:
        raise BackupError(
            f"Could not create a temporary restore file in {db_dir}: {exc}"
        ) from exc
    os.close(fd)  # close the raw fd so SQLite can open the file by path

    replaced = False
    try:
        try:
            src_conn = sqlite3.connect(backup_path)
            dst_conn = sqlite3.connect(temp_db_path)
            try:
                src_conn.backup(dst_conn)
            finally:
                dst_conn.close()
                src_conn.close()
        except sqlite3.Error as exc:
            raise BackupError(
                f"Could not copy the backup into the temporary database: {exc}"
            ) from exc

        # Checkpoint the temporary copy so it is a self-contained single file
        # (no WAL/SHM of its own) before it can become the active database.
        try:
            tmp_conn = sqlite3.connect(temp_db_path)
            try:
                tmp_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                tmp_conn.commit()
            finally:
                tmp_conn.close()
        except sqlite3.Error as exc:
            raise BackupError(
                f"Could not finalize the temporary restore copy: {exc}"
            ) from exc

        # --- Step 5: Validate the restored temporary database ---
        _validate_backup(temp_db_path)

        # --- Step 6: Coordinate the replacement ---
        with _RestoreLock(db_path):
            # Quiesce any in-flight writer on the active database: briefly take
            # and release a write lock so every concurrent writer commits before
            # we swap the pathname.  The connection is closed before the
            # replacement, so no transaction is held across the swap.
            if os.path.exists(db_path):
                try:
                    quiesce_conn = sqlite3.connect(db_path, timeout=5.0)
                    try:
                        quiesce_conn.execute("BEGIN IMMEDIATE")
                        quiesce_conn.commit()
                    finally:
                        quiesce_conn.close()
                except sqlite3.OperationalError as exc:
                    raise BackupError(
                        "The database is in active use and could not be "
                        "quiesced for restore."
                    ) from exc

            # --- Step 7: Atomically replace the active database ---
            try:
                os.replace(temp_db_path, db_path)
            except OSError as exc:
                raise BackupError(
                    f"Could not replace the active database at {db_path}: {exc}"
                ) from exc
            replaced = True

            # --- Step 8: Post-replace cleanup of the old database's sidecars ---
            # Only after a successful swap may the old sidecars be removed;
            # before this point they still described the live database and
            # must be preserved so a failed replace leaves it intact.
            _remove_orphaned_sidecars(db_path)
        # temp_db_path no longer exists — it is now db_path.
    except BaseException:
        # On any failure before the swap completed, clean up temporary
        # artifacts.  The active DB and its sidecars were never deleted, so
        # they are untouched.  If replacement succeeded, temp_db_path is gone.
        if not replaced:
            _cleanup_temp_db(temp_db_path)
        raise
