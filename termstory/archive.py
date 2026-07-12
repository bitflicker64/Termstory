import sqlite3
import re
from datetime import timedelta, date
from typing import Dict
from termstory.database import _safe_rollback_and_reraise
from termstory.database import Database
from termstory.date_utils import get_current_time

# Keep below SQLite's default SQLITE_MAX_VARIABLE_NUMBER (999).
_SQLITE_IN_CHUNK_SIZE = 900

def is_timeframe_older_than(timeframe_id: str, tf_type: str, cutoff_date: date) -> bool:
    """Check if a macro_summary timeframe is older than cutoff_date."""
    if tf_type == 'date':
        # Format: YYYY-MM-DD
        match = re.match(r'^(\d{4})-(\d{2})-(\d{2})$', timeframe_id)
        if match:
            try:
                tf_date = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
                return tf_date < cutoff_date
            except ValueError:
                return False
    elif tf_type == 'month':
        # Format: YYYY-MM
        match = re.match(r'^(\d{4})-(\d{2})$', timeframe_id)
        if match:
            try:
                year, month = int(match.group(1)), int(match.group(2))
                # Start of next month
                if month == 12:
                    next_year = year + 1
                    next_month = 1
                else:
                    next_year = year
                    next_month = month + 1
                next_month_start = date(next_year, next_month, 1)
                return next_month_start <= cutoff_date
            except ValueError:
                return False
    return False

def archive_old_data(main_db_path: str, archive_db_path: str, days: int) -> Dict[str, int]:
    """
    Archives sessions, commands, commits, and macro_summaries older than N days from main_db to archive_db.
    Deletes the archived records from the main database.
    """
    # Initialize both databases to ensure they have the correct schema
    archive_db = Database(archive_db_path)
    archive_db.init_db()

    main_db = Database(main_db_path)
    main_db.init_db()

    current_time = get_current_time()
    cutoff_time = current_time - timedelta(days=days)
    cutoff_timestamp = int(cutoff_time.timestamp())
    cutoff_date = cutoff_time.date()

    stats = {
        "sessions": 0,
        "commands": 0,
        "commits": 0,
        "macro_summaries": 0
    }

    conn = main_db.get_connection()
    try:
        # Enable WAL mode for better concurrency and speed
        conn.execute("PRAGMA journal_mode = WAL;")
        
        # Attach the target archive database
        conn.execute("ATTACH DATABASE ? AS archive", (archive_db_path,))
        cursor = conn.cursor()

        # Execute inside an exclusive transaction
        conn.execute("BEGIN IMMEDIATE;")

        # Find sessions to archive
        cursor.execute("SELECT id FROM main.sessions WHERE start_time < ?", (cutoff_timestamp,))
        session_ids = [row[0] for row in cursor.fetchall()]

        # Identify commits older than cutoff
        cursor.execute("SELECT hash FROM main.commits WHERE timestamp < ?", (cutoff_timestamp,))
        commit_hashes = [row[0] for row in cursor.fetchall()]

        if not session_ids and not commit_hashes:
            # Check if there are macro_summaries to move even if no sessions/commits
            cursor.execute("SELECT timeframe_id, type FROM main.macro_summaries")
            all_macro = cursor.fetchall()
            macro_to_delete = []
            for tf_id, tf_type in all_macro:
                if is_timeframe_older_than(tf_id, tf_type, cutoff_date):
                    macro_to_delete.append((tf_id, tf_type))
            
            if not macro_to_delete:
                conn.commit()
                return stats

        # Build project ID mapping
        # We query projects referenced by sessions, commands, or commits to archive
        # Ensure we only fetch unique project IDs that are NOT NULL
        proj_ids = set()
        if session_ids:
            cursor.execute("SELECT DISTINCT project_id FROM main.sessions WHERE start_time < ? AND project_id IS NOT NULL", (cutoff_timestamp,))
            proj_ids.update(r[0] for r in cursor.fetchall())
            
            cursor.execute("SELECT DISTINCT project_id FROM main.commands WHERE session_id IN (SELECT id FROM main.sessions WHERE start_time < ?) AND project_id IS NOT NULL", (cutoff_timestamp,))
            proj_ids.update(r[0] for r in cursor.fetchall())

        cursor.execute("SELECT DISTINCT project_id FROM main.commits WHERE timestamp < ? AND project_id IS NOT NULL", (cutoff_timestamp,))
        proj_ids.update(r[0] for r in cursor.fetchall())

        project_id_map = {}
        if proj_ids:
            projects_to_archive = []
            proj_ids_list = list(proj_ids)
            for i in range(0, len(proj_ids_list), _SQLITE_IN_CHUNK_SIZE):
                chunk = proj_ids_list[i:i + _SQLITE_IN_CHUNK_SIZE]
                proj_placeholders = ",".join("?" for _ in chunk)
                cursor.execute(f"SELECT id, name, path, first_seen, last_seen, project_context, created_at FROM main.projects WHERE id IN ({proj_placeholders})", chunk)
                projects_to_archive.extend(cursor.fetchall())

            for p_id, p_name, p_path, p_fs, p_ls, p_ctx, p_ca in projects_to_archive:
                # Check if this project path already exists in archive
                cursor.execute("SELECT id FROM archive.projects WHERE path = ?", (p_path,))
                row = cursor.fetchone()
                if row:
                    project_id_map[p_id] = row[0]
                else:
                    # Check if the ID is free in archive
                    cursor.execute("SELECT 1 FROM archive.projects WHERE id = ?", (p_id,))
                    if not cursor.fetchone():
                        cursor.execute("""
                            INSERT INTO archive.projects (id, name, path, first_seen, last_seen, project_context, created_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                        """, (p_id, p_name, p_path, p_fs, p_ls, p_ctx, p_ca))
                        project_id_map[p_id] = p_id
                    else:
                        # ID is taken, let SQLite autoincrement
                        cursor.execute("""
                            INSERT INTO archive.projects (name, path, first_seen, last_seen, project_context, created_at)
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, (p_name, p_path, p_fs, p_ls, p_ctx, p_ca))
                        project_id_map[p_id] = cursor.lastrowid

        # Determine FTS5 search index presence
        cursor.execute("SELECT 1 FROM archive.sqlite_master WHERE type='table' AND name='search_index';")
        has_archive_fts5 = cursor.fetchone() is not None
        cursor.execute("SELECT 1 FROM main.sqlite_master WHERE type='table' AND name='search_index';")
        has_main_fts5 = cursor.fetchone() is not None

        # Archive Sessions & Commands one by one to prevent ID collisions and maintain FK mapping
        for old_sess_id in session_ids:
            cursor.execute("""
                SELECT start_time, end_time, duration_seconds, project_id, created_at, tags, ai_summary
                FROM main.sessions WHERE id = ?
            """, (old_sess_id,))
            session_row = cursor.fetchone()
            if session_row is None:
                # Defense-in-depth: within the BEGIN IMMEDIATE transaction
                # (line 73) no concurrent writer can delete the row between
                # inventory and copy, so this guard is structurally unreachable.
                # Present as a safety net against future transaction refactors
                # or pre-existing data corruption.
                print(f"  Skipping session id={old_sess_id} (not found)")
                continue
            start_time, end_time, duration_seconds, old_proj_id, created_at, tags, ai_summary = session_row

            new_proj_id = project_id_map.get(old_proj_id)

            cursor.execute("""
                INSERT INTO archive.sessions (start_time, end_time, duration_seconds, project_id, created_at, tags, ai_summary)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (start_time, end_time, duration_seconds, new_proj_id, created_at, tags, ai_summary))
            new_sess_id = cursor.lastrowid
            stats["sessions"] += 1

            if has_archive_fts5 and ai_summary:
                cursor.execute("""
                    INSERT INTO archive.search_index (content, type, ref_id, project_id, timestamp)
                    VALUES (?, 'session_summary', ?, ?, ?)
                """, (ai_summary, str(new_sess_id), new_proj_id, start_time))

            # Fetch and insert commands
            cursor.execute("""
                SELECT timestamp, command, exit_code, project_id, created_at, recovery_source, is_legacy
                FROM main.commands WHERE session_id = ?
            """, (old_sess_id,))
            commands = cursor.fetchall()
            for cmd_ts, cmd_str, exit_code, old_cmd_proj_id, cmd_ca, rec_src, is_leg in commands:
                new_cmd_proj_id = project_id_map.get(old_cmd_proj_id)
                cursor.execute("""
                    INSERT INTO archive.commands (timestamp, command, exit_code, session_id, project_id, created_at, recovery_source, is_legacy)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (cmd_ts, cmd_str, exit_code, new_sess_id, new_cmd_proj_id, cmd_ca, rec_src, is_leg))
                stats["commands"] += 1

                if has_archive_fts5:
                    cursor.execute("""
                        INSERT INTO archive.search_index (content, type, ref_id, project_id, timestamp)
                        VALUES (?, 'command', ?, ?, ?)
                    """, (cmd_str, str(new_sess_id), new_cmd_proj_id, cmd_ts))

        # Archive Commits
        for c_hash in commit_hashes:
            cursor.execute("""
                SELECT timestamp, message, cleaned_message, project_id, created_at
                FROM main.commits WHERE hash = ?
            """, (c_hash,))
            commit_row = cursor.fetchone()
            if commit_row is None:
                print(f"  Skipping commit hash={c_hash} (not found, may have been deleted)")
                continue
            c_ts, c_msg, c_cl_msg, old_c_proj_id, c_ca = commit_row
            new_c_proj_id = project_id_map.get(old_c_proj_id)
            cursor.execute("""
                INSERT OR IGNORE INTO archive.commits (hash, timestamp, message, cleaned_message, project_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (c_hash, c_ts, c_msg, c_cl_msg, new_c_proj_id, c_ca))
            if cursor.rowcount > 0:
                stats["commits"] += 1

            if has_archive_fts5:
                # Check if it already exists in search_index
                cursor.execute("SELECT 1 FROM archive.search_index WHERE type='commit' AND ref_id=?", (c_hash,))
                if not cursor.fetchone():
                    cursor.execute("""
                        INSERT INTO archive.search_index (content, type, ref_id, project_id, timestamp)
                        VALUES (?, 'commit', ?, ?, ?)
                    """, (c_cl_msg, c_hash, new_c_proj_id, c_ts))

        # Archive macro_summaries
        cursor.execute("SELECT timeframe_id, type, summary, created_at FROM main.macro_summaries")
        all_macro = cursor.fetchall()
        macro_to_delete = []
        for tf_id, tf_type, summary, created_at in all_macro:
            if is_timeframe_older_than(tf_id, tf_type, cutoff_date):
                cursor.execute("""
                    INSERT OR REPLACE INTO archive.macro_summaries (timeframe_id, type, summary, created_at)
                    VALUES (?, ?, ?, ?)
                """, (tf_id, tf_type, summary, created_at))
                macro_to_delete.append(tf_id)
                stats["macro_summaries"] += 1

        # Delete archived records from the main database
        # FTS5 updates
        if has_main_fts5:
            if session_ids:
                cursor.execute("""
                    DELETE FROM main.search_index 
                    WHERE type = 'command' 
                      AND ref_id IN (SELECT CAST(id AS TEXT) FROM main.sessions WHERE start_time < ?)
                """, (cutoff_timestamp,))
                cursor.execute("""
                    DELETE FROM main.search_index 
                    WHERE type = 'session_summary' 
                      AND ref_id IN (SELECT CAST(id AS TEXT) FROM main.sessions WHERE start_time < ?)
                """, (cutoff_timestamp,))
            if commit_hashes:
                cursor.execute("""
                    DELETE FROM main.search_index 
                    WHERE type = 'commit' 
                      AND ref_id IN (SELECT hash FROM main.commits WHERE timestamp < ?)
                """, (cutoff_timestamp,))

        # Commands (child rows)
        if session_ids:
            cursor.execute("DELETE FROM main.commands WHERE session_id IN (SELECT id FROM main.sessions WHERE start_time < ?)", (cutoff_timestamp,))
        # Delete any orphan commands older than cutoff
        cursor.execute("DELETE FROM main.commands WHERE session_id IS NULL AND timestamp < ?", (cutoff_timestamp,))

        # Sessions
        if session_ids:
            cursor.execute("DELETE FROM main.sessions WHERE start_time < ?", (cutoff_timestamp,))

        # Commits
        if commit_hashes:
            cursor.execute("DELETE FROM main.commits WHERE timestamp < ?", (cutoff_timestamp,))

        # Macro summaries
        for tf_id in macro_to_delete:
            cursor.execute("DELETE FROM main.macro_summaries WHERE timeframe_id = ?", (tf_id,))

        conn.commit()
    except Exception as e:
        _safe_rollback_and_reraise(conn, e)
    finally:
        try:
            conn.execute("DETACH DATABASE archive")
        except Exception:
            pass
        conn.close()

    # Reclaim disk space via VACUUM
    try:
        conn_main = sqlite3.connect(main_db_path)
        conn_main.execute("VACUUM;")
        conn_main.close()
    except Exception:
        pass

    try:
        conn_arch = sqlite3.connect(archive_db_path)
        conn_arch.execute("VACUUM;")
        conn_arch.close()
    except Exception:
        pass

    return stats
