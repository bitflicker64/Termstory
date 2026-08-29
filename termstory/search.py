import logging
import sqlite3
from typing import List, Dict, Optional
from termstory.database import Database, _exactness_tier

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

    Backend selection order:
      1. ``fts=True`` -> _search_new_fts5 (commands_fts / sessions_fts /
         ai_summaries_fts), gated by _commands_fts_is_consistent (#467).
      2. search_index present -> _search_fts5, gated by
         _search_index_is_consistent (#467).
      3. Otherwise the authoritative standard SQL path (_search_standard),
         which is also the recovery route whenever an index is missing,
         broken, or demonstrably incomplete/stale.
    Each FTS attempt additionally keeps its OperationalError fallback, so a
    runtime FTS failure degrades to the next backend exactly as before #467.
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


def _fts_sync_triggers_intact(cursor) -> bool:
    """#471: True when all three commands_fts sync triggers exist.

    Shared precondition for BOTH consistency gates: the triggers ARE the FTS
    synchronization machinery. Their absence certifies that the database has
    been mutated outside the application's write paths (dropped manually,
    interrupted migration, partially restored backup). Neither derived index
    can prove itself current against such out-of-band mutation — commands_fts
    misses the affected rows/text, and search_index (which stores no
    per-command identifier at all) cannot even attempt the proof — so both
    must defer to the authoritative standard SQL search. This is
    self-healing: init_db -> _migrate_fts5 recreates any missing trigger via
    CREATE ... IF NOT EXISTS on the next application start.
    """
    try:
        cursor.execute(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type = 'trigger' "
            "AND name IN ('commands_ai', 'commands_ad', 'commands_au')"
        )
        return cursor.fetchone()[0] == 3
    except sqlite3.Error:
        return False


def _commands_fts_is_consistent(conn: sqlite3.Connection) -> bool:
    """#467/#471: structural check that commands_fts covers every command with
    live synchronization and current identities.

    commands_fts is an EXTERNAL-CONTENT FTS5 table whose rowids mirror
    commands.id exactly (content='commands', content_rowid='id'), kept in sync
    solely by the commands_ai/commands_ad/commands_au triggers. Two failure
    classes must be detected:

    1. Missing/extra indexed identities — lost or bypassed triggers leave the
       index silently behind the authoritative ``commands`` table.
    2. Stale indexed TEXT — every production path that changes ``command``
       text fires ``commands_au`` (``UPDATE OF command, exit_code``); if that
       trigger is absent, an UPDATE can desync index content while IDs and
       counts stay identical. Because external-content tables serve column
       values from the content table at query time, old indexed text is NOT
       readable for comparison — trigger presence is therefore the only sound,
       migration-free signal of content freshness.

    The identity check compares the ``%_docsize`` shadow table (one row per
    document actually in the index; its ``id`` is the docid == commands.id)
    against ``commands`` using exact bidirectional membership equality. Plain
    COUNT/MAX comparisons are insufficient: ID sets such as {1,3} vs {2,3}
    share both count and maximum yet differ in membership.

    Efficiency: one statement — three sqlite_master probes plus two anti-joins
    over integer primary keys. A scan is unavoidable because coverage is
    inherently a set comparison and no marker column may be added (#467
    forbids schema changes); these are the cheapest correct probes available
    and are still orders of magnitude cheaper than rebuilding. Recovery falls
    back to standard SQL instead of rebuilding, which belongs to the
    init/migration lifecycle (_migrate_fts5).

    Any sqlite error (missing shadow tables, absent tables on FTS-less builds)
    yields False so callers skip straight to the next backend in the chain.
    See _fts_sync_triggers_intact for why trigger presence is required.
    """
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                (SELECT COUNT(*) FROM sqlite_master
                  WHERE type = 'trigger'
                    AND name IN ('commands_ai', 'commands_ad', 'commands_au')),
                EXISTS(SELECT 1 FROM commands c
                        WHERE NOT EXISTS (SELECT 1 FROM commands_fts_docsize d
                                           WHERE d.id = c.id)),
                EXISTS(SELECT 1 FROM commands_fts_docsize d
                        WHERE NOT EXISTS (SELECT 1 FROM commands c
                                           WHERE c.id = d.id))
        """)
        triggers_present, missing_ids, extra_ids = cursor.fetchone()
        return triggers_present == 3 and not missing_ids and not extra_ids
    except sqlite3.Error:
        return False


def _search_index_is_consistent(conn: sqlite3.Connection) -> bool:
    """#467/#471: structural check that search_index covers every command.

    search_index rows of type='command' mirror the authoritative commands
    table: one row per command whose session_id is NOT NULL, with ref_id
    storing that command's SESSION id (see the initial population in
    Database._migrate_fts5 and the per-session resync in Database.save_data).
    Unlike commands_fts, search_index has NO sync triggers, so any command
    written outside save_data leaves the index silently incomplete.

    search_index stores no per-command identifier — rows are distinguishable
    only by (ref_id, content) — so exact per-command identity coverage cannot
    be established cheaply (a content-equality probe would scan the whole FTS
    table per candidate row). The strongest reliable structural signal is
    therefore per-session MULTIPLICITY equality: for every session, the
    number of indexed command rows must equal the number of authoritative
    commands, checked in both directions via EXCEPT. This detects missing
    entries, duplicated rows, and offsets between sessions that would fool a
    single global COUNT comparison.

    Residual limitation (documented, accepted): two commands inside the SAME
    session could swap texts undetected, because per-row identity does not
    exist in this legacy index shape and adding one would require a schema
    migration, which #467 forbids. As with commands_fts, this inspects only
    index-vs-table structure — never query result counts — so legitimate
    zero-result queries stay on the FTS path, and recovery routes to the
    authoritative standard SQL search instead of rebuilding.
    """
    try:
        cursor = conn.cursor()
        # Out-of-band mutation (missing sync triggers) invalidates trust in
        # every derived representation — see _fts_sync_triggers_intact.
        if not _fts_sync_triggers_intact(cursor):
            return False
        cursor.execute("""
            SELECT
                EXISTS(
                    SELECT session_id, COUNT(*) AS n
                      FROM commands WHERE session_id IS NOT NULL
                     GROUP BY session_id
                    EXCEPT
                    SELECT CAST(ref_id AS INTEGER), COUNT(*) AS n
                      FROM search_index WHERE type = 'command'
                     GROUP BY ref_id
                )
             OR EXISTS(
                    SELECT CAST(ref_id AS INTEGER), COUNT(*) AS n
                      FROM search_index WHERE type = 'command'
                     GROUP BY ref_id
                    EXCEPT
                    SELECT session_id, COUNT(*) AS n
                      FROM commands WHERE session_id IS NOT NULL
                     GROUP BY session_id
                )
        """)
        mismatch = cursor.fetchone()[0]
        return not mismatch
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
    """Match sessions via commands_fts/sessions_fts/ai_summaries_fts with
    match-type ranking; callers must have verified index consistency (#467)."""
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

    _tier_expr, _tier_params = _exactness_tier(query)
    params.extend(_tier_params)
    sql += f" GROUP BY s.id ORDER BY {_tier_expr} ASC, bm.min_match_type ASC, bm.min_rank ASC, s.start_time DESC, s.id DESC"
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
    """Match sessions via the legacy search_index FTS table (command rows,
    commit rows, and session summaries); callers must have verified index
    consistency (#467)."""
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

    _tier_expr, _tier_params = _exactness_tier(query)
    params.extend(_tier_params)
    sql += f" GROUP BY s.id ORDER BY {_tier_expr} ASC, CASE WHEN MIN(f.rank) IS NOT NULL THEN 0 ELSE 1 END, MIN(f.rank) ASC, s.start_time DESC, s.id DESC"
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
    """Authoritative LIKE-based search over the raw sessions/commands/commits
    tables; used when FTS is unavailable and as the #467 recovery path."""
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

    _tier_expr, _tier_params = _exactness_tier(query)
    if _tier_expr is not None:
        params.extend(_tier_params)
        sql += f" ORDER BY {_tier_expr} ASC, s.start_time DESC, s.id DESC"
    else:
        sql += " ORDER BY s.start_time DESC, s.id DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    
    cursor.execute(sql, params)
    rows = cursor.fetchall()
    return _populate_results(cursor, rows, query)

def _populate_results(cursor, rows, query: Optional[str]) -> List[Dict]:
    """Expand session rows into result dicts with all/matching commands and
    commits, shared by every search backend to keep one result format."""
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
