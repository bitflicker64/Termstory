import sqlite3
from typing import List, Dict, Optional
from termstory.database import Database

def advanced_search(
    db: Database,
    query: Optional[str] = None,
    project_filter: Optional[str] = None,
    since_ts: Optional[int] = None,
    until_ts: Optional[int] = None,
    tag_filters: Optional[List[str]] = None
) -> List[Dict]:
    """
    Advanced search with query, date range (since_ts, until_ts), project, and tag filters.
    """
    conn = db.get_connection()
    try:
        cursor = conn.cursor()
        
        # Check if FTS5 is enabled
        fts_enabled = False
        try:
            cursor.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='search_index';")
            fts_enabled = cursor.fetchone() is not None
        except Exception:
            pass
            
        if fts_enabled and query:
            try:
                return _search_fts5(conn, query, project_filter, since_ts, until_ts, tag_filters)
            except sqlite3.OperationalError:
                pass
                
        return _search_standard(conn, query, project_filter, since_ts, until_ts, tag_filters)
    finally:
        conn.close()

def _search_fts5(
    conn: sqlite3.Connection,
    query: str,
    project_filter: Optional[str],
    since_ts: Optional[int],
    until_ts: Optional[int],
    tag_filters: Optional[List[str]]
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
            SELECT CAST(session_id AS INTEGER) as s_id, rank FROM commands_fts WHERE commands_fts MATCH ?
            UNION ALL
            SELECT CAST(session_id AS INTEGER) as s_id, rank FROM sessions_fts WHERE sessions_fts MATCH ?
            UNION ALL
            SELECT CAST(session_id AS INTEGER) as s_id, rank FROM ai_summaries_fts WHERE ai_summaries_fts MATCH ?
        )
        SELECT s.id, s.start_time, s.end_time, s.duration_seconds, s.project_id, p.name, p.path, s.ai_summary
        FROM sessions s
        LEFT JOIN projects p ON s.project_id = p.id
        LEFT JOIN fts_matches f ON s.id = f.s_id
        WHERE (f.rank IS NOT NULL OR p.name LIKE ?)
    """
    params = [fts_query, fts_query, fts_query, query_val]
    
    if project_filter:
        sql += " AND p.name LIKE ?"
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
    
    cursor.execute(sql, params)
    rows = cursor.fetchall()
    return _populate_results(cursor, rows, query)

def _search_standard(
    conn: sqlite3.Connection,
    query: Optional[str],
    project_filter: Optional[str],
    since_ts: Optional[int],
    until_ts: Optional[int],
    tag_filters: Optional[List[str]]
) -> List[Dict]:
    cursor = conn.cursor()
    params = []
    
    if query:
        query_val = f"%{query}%"
        sql = """
            SELECT DISTINCT s.id, s.start_time, s.end_time, s.duration_seconds, s.project_id, p.name, p.path, s.ai_summary
            FROM sessions s
            LEFT JOIN projects p ON s.project_id = p.id
            LEFT JOIN commands c ON s.id = c.session_id
            LEFT JOIN commits co ON s.project_id = co.project_id 
                AND co.timestamp >= s.start_time - 300 
                AND co.timestamp <= s.end_time + 600
            WHERE (
                p.name LIKE ?
                OR c.command LIKE ?
                OR co.message LIKE ?
                OR co.cleaned_message LIKE ?
                OR s.ai_summary LIKE ?
            )
        """
        params = [query_val, query_val, query_val, query_val, query_val]
    else:
        sql = """
            SELECT DISTINCT s.id, s.start_time, s.end_time, s.duration_seconds, s.project_id, p.name, p.path, s.ai_summary
            FROM sessions s
            LEFT JOIN projects p ON s.project_id = p.id
            WHERE 1=1
        """
        
    if project_filter:
        sql += " AND p.name LIKE ?"
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
            cursor.execute("""
                SELECT hash, timestamp, message, cleaned_message 
                FROM commits 
                WHERE project_id = ? AND timestamp >= ? AND timestamp <= ?
                ORDER BY timestamp ASC
            """, (p_id, start_time - 300, end_time + 600))
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
