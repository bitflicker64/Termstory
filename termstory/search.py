import logging
import sqlite3
from typing import List, Dict, Optional
from termstory.database import Database

logger = logging.getLogger(__name__)

def advanced_search(
    db: Database,
    query: Optional[str] = None,
    project_filter: Optional[str] = None,
    since_ts: Optional[int] = None,
    until_ts: Optional[int] = None,
    tag_filters: Optional[List[str]] = None,
    fts: bool = False,
    limit: Optional[int] = None
) -> List[Dict]:
    """
    Advanced search with query, date range (since_ts, until_ts), project, and tag filters.
    """
    conn = db.get_connection()
    try:
        cursor = conn.cursor()
        
        if fts and query:
            # #467: an FTS index can exist yet be stale/incomplete relative to
            # the authoritative commands table (lost triggers, interrupted
            # migration, restored backup). Searching such an index silently
            # omits records, so verify coverage first and route around it.
            if _commands_fts_is_consistent(conn):
                try:
                    return _search_new_fts5(conn, query, project_filter, since_ts, until_ts, tag_filters, limit)
                except sqlite3.OperationalError:
                    logger.warning("FTS5 advanced search failed, falling back", exc_info=True)
            else:
                logger.warning(
                    "commands_fts missing or incomplete relative to authoritative "
                    "commands; skipping FTS5 advanced search (#467)"
                )

        # Check if FTS5 is enabled
        fts_enabled = False
        try:
            cursor.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='search_index';")
            fts_enabled = cursor.fetchone() is not None
        except Exception:
            pass
            
        if fts_enabled and query:
            # #467: same consistency gate as above, for the manually-synced
            # search_index backend ('command' rows mirror commands one-to-one).
            if _search_index_is_consistent(conn):
                try:
                    return _search_fts5(conn, query, project_filter, since_ts, until_ts, tag_filters, limit)
                except sqlite3.OperationalError:
                    logger.warning("FTS5 search failed, falling back", exc_info=True)
            else:
                logger.warning(
                    "search_index incomplete relative to authoritative commands; "
                    "using standard SQL search (#467)"
                )

        return _search_standard(conn, query, project_filter, since_ts, until_ts, tag_filters, limit)
    finally:
        conn.close()


def _commands_fts_is_consistent(conn: sqlite3.Connection) -> bool:
    """#467: cheap structural check that commands_fts covers every command.

    commands_fts is an EXTERNAL-CONTENT FTS5 table whose rowids mirror
    commands.id exactly (content='commands', content_rowid='id'), maintained by
    triggers. If triggers were lost/dropped or a migration was interrupted, the
    index keeps existing but silently misses rows, making searches return
    incomplete results against the authoritative ``commands`` table.

    NOTE: COUNT(*)/MAX(rowid) asked of ``commands_fts`` itself are served from
    the content table, so they cannot reveal index holes. The ``%_docsize``
    shadow table, however, holds exactly one row per document actually present
    in the index (its ``id`` is the docid, i.e. commands.id), which makes it a
    faithful proxy for index coverage. A healthy index therefore satisfies
    COUNT(commands_fts_docsize) == COUNT(commands) and MAX(ids) equality. This
    inspects only index-vs-table structure — never the query's result count —
    so a healthy index legitimately returning zero matches is never mistaken
    for corruption. We recover by falling back to the standard SQL search
    instead of rebuilding because rebuilding belongs to the init/migration
    lifecycle (_migrate_fts5) and is far too expensive to run per query.

    Any sqlite error (missing shadow tables on exotic builds, absent tables on
    FTS-less builds) yields False so callers skip straight to the next backend
    in the chain.
    """
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                (SELECT COUNT(*) FROM commands),
                (SELECT COUNT(*) FROM commands_fts_docsize),
                (SELECT MAX(id) FROM commands),
                (SELECT MAX(id) FROM commands_fts_docsize)
        """)
        cmd_count, fts_count, max_cmd_id, max_fts_rowid = cursor.fetchone()
        return cmd_count == fts_count and max_cmd_id == max_fts_rowid
    except sqlite3.Error:
        return False


def _search_index_is_consistent(conn: sqlite3.Connection) -> bool:
    """#467: cheap structural check that search_index covers every command.

    search_index rows of type='command' mirror the authoritative commands table
    one-to-one: exactly one row per command whose session_id is NOT NULL (each
    row's ref_id stores that command's session id — see the initial population
    in Database._migrate_fts5 and the per-session resync in Database.save_data).
    Unlike commands_fts, search_index has NO sync triggers, so any command
    written outside save_data leaves the index silently incomplete.

    Compare the indexed-command count against the authoritative count using the
    exact predicate used when indexing (session_id IS NOT NULL). Structural
    check only — see _commands_fts_is_consistent for why zero-result queries
    are unaffected and why recovery falls back instead of rebuilding.
    """
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                (SELECT COUNT(*) FROM commands WHERE session_id IS NOT NULL),
                (SELECT COUNT(*) FROM search_index WHERE type = 'command')
        """)
        authoritative_count, indexed_count = cursor.fetchone()
        return authoritative_count == indexed_count
    except sqlite3.Error:
        return False


def _search_new_fts5(
    conn: sqlite3.Connection,
    query: str,
    project_filter: Optional[str],
    since_ts: Optional[int],
    until_ts: Optional[int],
    tag_filters: Optional[List[str]],
    limit: Optional[int] = None
) -> List[Dict]:
    cursor = conn.cursor()
    
    terms = query.split()
    sanitized_terms = []
    for term in terms:
        clean_term = term.replace('"', '""')
        if clean_term:
            sanitized_terms.append(f'"{clean_term}"*')
    fts_query = " ".join(sanitized_terms)
    
    if not fts_query:
        return []
        
    sql = """
        WITH matched_session_ids AS (
            -- Matches from commands_fts
            SELECT DISTINCT session_id AS id, 1 AS match_type, NULL as rank
            FROM commands
            WHERE id IN (SELECT rowid FROM commands_fts WHERE commands_fts MATCH ?)
              AND session_id IS NOT NULL

            UNION ALL

            -- Matches from sessions_fts
            SELECT rowid AS id, 2 AS match_type, rank
            FROM sessions_fts
            WHERE sessions_fts MATCH ?

            UNION ALL

            -- Matches from ai_summaries_fts (macro_summaries)
            SELECT s.id, 3 AS match_type, f.rank
            FROM macro_summaries m
            JOIN ai_summaries_fts f ON f.rowid = m.id
            JOIN sessions s ON s.start_time >= CAST(strftime('%s', date(m.created_at, 'unixepoch', 'localtime') || ' 00:00:00', 'utc') AS INTEGER)
                           AND s.start_time <= CAST(strftime('%s', date(m.created_at, 'unixepoch', 'localtime') || ' 23:59:59', 'utc') AS INTEGER)
            WHERE f.ai_summaries_fts MATCH ? AND m.type = 'daily'
        ),
        best_matches AS (
            SELECT id, MIN(match_type) as min_match_type, MIN(rank) as min_rank
            FROM matched_session_ids
            GROUP BY id
        )
        SELECT s.id, s.start_time, s.end_time, s.duration_seconds,
               COALESCE(ep.id, s.project_id),
               COALESCE(ep.name, p.name),
               COALESCE(ep.path, p.path),
               s.ai_summary
        FROM sessions s
        JOIN best_matches bm ON s.id = bm.id
        LEFT JOIN projects p ON s.project_id = p.id
        LEFT JOIN projects ep ON ep.id = (
            -- #457: attribute the result to the project of the earliest
            -- command that actually matched the query and carries explicit
            -- per-command attribution; NULL -> fall back to session project.
            SELECT c.project_id
            FROM commands c
            WHERE c.session_id = s.id
              AND c.id IN (SELECT rowid FROM commands_fts WHERE commands_fts MATCH ?)
              AND c.project_id IS NOT NULL
            ORDER BY c.timestamp ASC, c.id ASC
            LIMIT 1
        )
        LEFT JOIN commands cmd_per_proj ON cmd_per_proj.session_id = s.id
        LEFT JOIN projects p2 ON cmd_per_proj.project_id = p2.id
        WHERE 1=1
    """
    params = [fts_query, fts_query, fts_query, fts_query]

    if project_filter:
        # Match if the session's final project OR any per-command project
        # matches the filter — fixes #339 where a session that switches
        # projects mid-stream was filtered out when its commands ran in
        # a different project than the final cd.
        sql += " AND (p.name LIKE ? OR p2.name LIKE ?)"
        params.append(f"%{project_filter}%")
        params.append(f"%{project_filter}%")

    if since_ts:
        sql += " AND s.start_time >= ?"
        params.append(since_ts)

    if until_ts:
        sql += " AND s.start_time <= ?"
        params.append(until_ts)

    if tag_filters:
        for tag in tag_filters:
            sql += " AND s.tags LIKE ?"
            params.append(f"%{tag}%")

    sql += " GROUP BY s.id ORDER BY bm.min_match_type ASC, bm.min_rank ASC, s.start_time DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    
    cursor.execute(sql, params)
    rows = cursor.fetchall()
    return _populate_results(cursor, rows, query)

def _search_fts5(
    conn: sqlite3.Connection,
    query: str,
    project_filter: Optional[str],
    since_ts: Optional[int],
    until_ts: Optional[int],
    tag_filters: Optional[List[str]],
    limit: Optional[int] = None
) -> List[Dict]:
    cursor = conn.cursor()

    terms = query.split()
    sanitized_terms = []
    for term in terms:
        clean_term = term.replace('"', '""')
        if clean_term:
            sanitized_terms.append(f'"{clean_term}"*')
    fts_query = " ".join(sanitized_terms)

    if not fts_query:
        return []

    query_val = f"%{query}%"

    sql = """
        WITH fts_matches AS (
            SELECT type, ref_id, project_id, timestamp, rank
            FROM search_index
            WHERE search_index MATCH ?
        )
        SELECT s.id, s.start_time, s.end_time, s.duration_seconds,
               COALESCE(ep.id, s.project_id),
               COALESCE(ep.name, p.name),
               COALESCE(ep.path, p.path),
               s.ai_summary
        FROM sessions s
        LEFT JOIN projects p ON s.project_id = p.id
        LEFT JOIN projects ep ON ep.id = (
            -- #457: attribute the result to the project recorded on the
            -- earliest matching command index entry that carries explicit
            -- per-command attribution; NULL -> fall back to session project.
            SELECT project_id
            FROM search_index
            WHERE type = 'command'
              AND CAST(ref_id AS INTEGER) = s.id
              AND search_index MATCH ?
              AND project_id IS NOT NULL
            ORDER BY CAST(timestamp AS INTEGER) ASC, rowid ASC
            LIMIT 1
        )
        LEFT JOIN commands cmd_per_proj ON cmd_per_proj.session_id = s.id
        LEFT JOIN projects p2 ON cmd_per_proj.project_id = p2.id
        LEFT JOIN fts_matches f ON (
            (f.type = 'session_summary' AND CAST(f.ref_id AS INTEGER) = s.id)
            OR (f.type = 'command' AND CAST(f.ref_id AS INTEGER) = s.id)
            OR (f.type = 'commit' AND s.project_id = CAST(f.project_id AS INTEGER)
                AND CAST(f.timestamp AS INTEGER) >= s.start_time - 300
                AND CAST(f.timestamp AS INTEGER) <= COALESCE(s.end_time, s.start_time) + 600)
        )
        WHERE (f.rank IS NOT NULL OR p.name LIKE ? OR p2.name LIKE ?)
    """
    params = [fts_query, fts_query, query_val, query_val]

    if project_filter:
        # Match if the session's final project OR any per-command project
        # matches the filter — fixes #339 where a session that switches
        # projects mid-stream was filtered out when its commands ran in
        # a different project than the final cd.
        sql += " AND (p.name LIKE ? OR p2.name LIKE ?)"
        params.append(f"%{project_filter}%")
        params.append(f"%{project_filter}%")

    if since_ts:
        sql += " AND s.start_time >= ?"
        params.append(since_ts)

    if until_ts:
        sql += " AND s.start_time <= ?"
        params.append(until_ts)

    if tag_filters:
        for tag in tag_filters:
            sql += " AND s.tags LIKE ?"
            params.append(f"%{tag}%")

    sql += " GROUP BY s.id ORDER BY CASE WHEN MIN(f.rank) IS NOT NULL THEN 0 ELSE 1 END, MIN(f.rank) ASC, s.start_time DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)

    cursor.execute(sql, params)
    rows = cursor.fetchall()
    return _populate_results(cursor, rows, query)

def _search_standard(
    conn: sqlite3.Connection,
    query: Optional[str],
    project_filter: Optional[str],
    since_ts: Optional[int],
    until_ts: Optional[int],
    tag_filters: Optional[List[str]],
    limit: Optional[int] = None
) -> List[Dict]:
    cursor = conn.cursor()
    params = []
    
    if query:
        query_val = f"%{query}%"
        sql = """
            SELECT DISTINCT s.id, s.start_time, s.end_time, s.duration_seconds,
                   COALESCE(ep.id, s.project_id),
                   COALESCE(ep.name, p.name),
                   COALESCE(ep.path, p.path),
                   s.ai_summary
            FROM sessions s
            LEFT JOIN projects p ON s.project_id = p.id
            LEFT JOIN projects ep ON ep.id = (
                -- #457: attribute the result to the project of the earliest
                -- command that actually matched the query and carries explicit
                -- per-command attribution; NULL -> fall back to session project.
                SELECT c.project_id
                FROM commands c
                WHERE c.session_id = s.id
                  AND c.command LIKE ?
                  AND c.project_id IS NOT NULL
                ORDER BY c.timestamp ASC, c.id ASC
                LIMIT 1
            )
            LEFT JOIN commands c ON s.id = c.session_id
            LEFT JOIN projects p2 ON c.project_id = p2.id
            LEFT JOIN commits co ON s.project_id = co.project_id
                AND co.timestamp >= s.start_time - 300
                AND co.timestamp <= COALESCE(s.end_time, s.start_time) + 600
            WHERE (
                p.name LIKE ?
                OR p2.name LIKE ?
                OR c.command LIKE ?
                OR co.message LIKE ?
                OR co.cleaned_message LIKE ?
                OR s.ai_summary LIKE ?
            )
        """
        params = [query_val, query_val, query_val, query_val, query_val, query_val, query_val]
    else:
        sql = """
            SELECT DISTINCT s.id, s.start_time, s.end_time, s.duration_seconds, s.project_id, p.name, p.path, s.ai_summary
            FROM sessions s
            LEFT JOIN projects p ON s.project_id = p.id
            LEFT JOIN commands c ON s.id = c.session_id
            LEFT JOIN projects p2 ON c.project_id = p2.id
            WHERE 1=1
        """

    if project_filter:
        # Match if the session's final project OR any per-command project
        # matches the filter — fixes #339 where a session that switches
        # projects mid-stream was filtered out when its commands ran in
        # a different project than the final cd.
        sql += " AND (p.name LIKE ? OR p2.name LIKE ?)"
        params.append(f"%{project_filter}%")
        params.append(f"%{project_filter}%")

    if since_ts:
        sql += " AND s.start_time >= ?"
        params.append(since_ts)

    if until_ts:
        sql += " AND s.start_time <= ?"
        params.append(until_ts)

    if tag_filters:
        for tag in tag_filters:
            sql += " AND s.tags LIKE ?"
            params.append(f"%{tag}%")

    sql += " ORDER BY s.start_time DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    
    cursor.execute(sql, params)
    rows = cursor.fetchall()
    return _populate_results(cursor, rows, query)

def _populate_results(cursor, rows, query: Optional[str]) -> List[Dict]:
    results = []
    query_val = f"%{query}%" if query else None
    
    for row in rows:
        s_id, start_time, end_time, duration, p_id, p_name, p_path, ai_sum = row
        
        # Fetch all commands in this session
        cursor.execute("""
            SELECT command FROM commands WHERE session_id = ? ORDER BY timestamp ASC
        """, (s_id,))
        all_cmds = [r[0] for r in cursor.fetchall()]
        
        # Fetch matching commands in this session
        matching_cmds = []
        if query_val:
            cursor.execute("""
                SELECT command FROM commands WHERE session_id = ? AND command LIKE ? ORDER BY timestamp ASC
            """, (s_id, query_val))
            matching_cmds = [r[0] for r in cursor.fetchall()]
            
        # Fetch all commits in this session (using buffer)
        all_commits = []
        matching_commits = []
        if p_id is not None:
            effective_end = end_time if end_time is not None else start_time
            cursor.execute("""
                SELECT hash, timestamp, message, cleaned_message 
                FROM commits 
                WHERE project_id = ? AND timestamp >= ? AND timestamp <= ?
                ORDER BY timestamp ASC
            """, (p_id, start_time - 300, effective_end + 600))
            for c_row in cursor.fetchall():
                c_dict = {
                    "hash": c_row[0],
                    "timestamp": c_row[1],
                    "message": c_row[2],
                    "cleaned_message": c_row[3]
                }
                all_commits.append(c_dict)
                # Check if commit matches query
                if query and (query.lower() in c_row[2].lower() or query.lower() in c_row[3].lower()):
                    matching_commits.append(c_dict)
                    
        results.append({
            "session_id": s_id,
            "start_time": start_time,
            "end_time": end_time,
            "duration_seconds": duration,
            "project_id": p_id,
            "project_name": p_name or "General / No Project",
            "project_path": p_path or "",
            "ai_summary": ai_sum,
            "all_commands": all_cmds,
            "matching_commands": matching_cmds,
            "all_commits": all_commits,
            "matching_commits": matching_commits
        })
        
    return results
