import os
import json
import webbrowser
from typing import Optional
from termstory.insights import analyze_all
from termstory.formatter import _is_noise_command
from termstory.database import Database


def get_web_data(db: Database, start_ts: Optional[int] = None, end_ts: Optional[int] = None) -> dict:
    """Gather stats, project list, timeline, and AI highlights from the database, filtering by date range if provided."""
    import time
    from datetime import datetime, timedelta
    
    # 1. Base Stats
    stats = analyze_all(db)
    
    # Enrich stats with last ingestion time
    stats["last_ingestion_time"] = db.get_last_ingestion_time()

    # 2. Project List
    projects = db.get_all_projects_with_stats()
    projects_data = []
    for p in projects:
        name = p.name
        if not name or name == "General / No Project":
            name = "Other"
        projects_data.append({
            "id": p.id,
            "name": name,
            "path": p.path or "",
            "session_count": p.session_count,
            "total_time": p.total_time,
            "first_seen": p.first_seen,
            "last_seen": p.last_seen
        })
    # Sort projects by total time descending
    projects_data.sort(key=lambda x: x["total_time"], reverse=True)

    # 3. Filtered Sessions
    conn = db.get_connection()
    cursor = conn.cursor()
    
    query = "SELECT id FROM sessions"
    params = []
    conditions = []
    if start_ts is not None:
        conditions.append("start_time >= ?")
        params.append(start_ts)
    if end_ts is not None:
        conditions.append("start_time <= ?")
        params.append(end_ts)
        
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY start_time DESC LIMIT 1000"
    else:
        query += " ORDER BY start_time DESC LIMIT 30"
        
    cursor.execute(query, params)
    session_ids = [row[0] for row in cursor.fetchall()]
    conn.close()

    sessions = db.get_sessions_by_ids(session_ids)
    sessions_data = []
    for s in sessions:
        proj_name = "Other"
        # Find project name
        for p in projects_data:
            if p["id"] == s.project_id:
                proj_name = p["name"]
                break

        # Mapped commits
        commits = []
        for c in s.commits:
            commits.append({
                "hash": c.get("hash", ""),
                "message": c.get("message", ""),
                "cleaned_message": c.get("cleaned_message", "")
            })

        # Mapped commands
        commands = []
        for cmd in s.commands:
            commands.append({
                "command": cmd.command,
                "timestamp": cmd.timestamp,
                "exit_code": cmd.exit_code,
                "is_legacy": cmd.is_legacy,
                "is_noise": _is_noise_command(cmd.command)
            })

        sessions_data.append({
            "id": s.id,
            "start_time": s.start_time,
            "end_time": s.end_time,
            "duration_seconds": s.duration_seconds,
            "project_name": proj_name,
            "ai_summary": s.ai_summary or "",
            "commands": commands,
            "commits": commits,
            "is_legacy": s.is_legacy
        })

    # Override total KPI stats if range is specified
    if start_ts is not None or end_ts is not None:
        stats["total_sessions"] = len(sessions_data)
        stats["total_commands"] = sum(len(s.commands) for s in sessions)
        stats["total_projects"] = len(set(s.project_id for s in sessions if s.project_id is not None))

    # 4. AI Summary Highlights
    ai_sessions = [s for s in sessions_data if s["ai_summary"]]
    if len(ai_sessions) < 15:
        # Fetch more from DB if we don't have enough in the filtered set
        conn = db.get_connection()
        cursor = conn.cursor()
        
        # Build query for general AI highlights
        query_ai = "SELECT id FROM sessions WHERE ai_summary IS NOT NULL AND ai_summary != ''"
        params_ai = []
        conditions_ai = []
        if start_ts is not None:
            conditions_ai.append("start_time >= ?")
            params_ai.append(start_ts)
        if end_ts is not None:
            conditions_ai.append("start_time <= ?")
            params_ai.append(end_ts)
        if conditions_ai:
            query_ai += " AND " + " AND ".join(conditions_ai)
        query_ai += " ORDER BY start_time DESC LIMIT 15"
        
        cursor.execute(query_ai, params_ai)
        extra_ids = [row[0] for row in cursor.fetchall()]
        conn.close()

        extra_ids_to_fetch = [eid for eid in extra_ids if eid not in [s["id"] for s in sessions_data]]
        if extra_ids_to_fetch:
            extra_sessions = db.get_sessions_by_ids(extra_ids_to_fetch)
            for s in extra_sessions:
                proj_name = "Other"
                for p in projects_data:
                    if p["id"] == s.project_id:
                        proj_name = p["name"]
                        break

                ai_sessions.append({
                    "id": s.id,
                    "start_time": s.start_time,
                    "end_time": s.end_time,
                    "duration_seconds": s.duration_seconds,
                    "project_name": proj_name,
                    "ai_summary": s.ai_summary or "",
                    "commands": [],
                    "commits": [],
                    "is_legacy": s.is_legacy
                })

    ai_sessions.sort(key=lambda x: x["start_time"], reverse=True)
    highlights_data = ai_sessions[:15]

    # 5. Calculate daily activity for the last 90 days for the heatmap
    now_dt = datetime.now()
    ninety_days_ago_ts = int(time.time() - 90 * 86400)
    
    daily_activity = {}
    for i in range(90):
        d = now_dt - timedelta(days=i)
        ds = d.strftime("%Y-%m-%d")
        daily_activity[ds] = {"commands": 0, "sessions": 0}
        
    conn = db.get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT timestamp FROM commands WHERE timestamp >= ?", (ninety_days_ago_ts,))
        cmd_ts_rows = cursor.fetchall()
        for (ts,) in cmd_ts_rows:
            try:
                ds = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
                if ds in daily_activity:
                    daily_activity[ds]["commands"] += 1
            except Exception:
                pass
                
        cursor.execute("SELECT start_time FROM sessions WHERE start_time >= ?", (ninety_days_ago_ts,))
        sess_ts_rows = cursor.fetchall()
        for (ts,) in sess_ts_rows:
            try:
                ds = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
                if ds in daily_activity:
                    daily_activity[ds]["sessions"] += 1
            except Exception:
                pass
    except Exception:
        pass
    finally:
        conn.close()

    return {
        "stats": stats,
        "projects": projects_data,
        "sessions": sessions_data,
        "highlights": highlights_data,
        "daily_activity": daily_activity
    }

def generate_and_open_report(
    db: Database,
    template: Optional[str] = None,
    start_ts: Optional[int] = None,
    end_ts: Optional[int] = None
) -> None:
    """Generate the HTML report, save it to ~/.termstory/report.html, and open it in the default browser."""
    data = get_web_data(db, start_ts=start_ts, end_ts=end_ts)
    safe_data_str = json.dumps(data).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    
    # Check if a custom template file path is provided and exists
    if template and os.path.isfile(template):
        with open(template, "r", encoding="utf-8") as f:
            custom_template_content = f.read()
        if "{{report_data}}" in custom_template_content:
            html_content = custom_template_content.replace("{{report_data}}", safe_data_str)
        elif "const reportData = " in custom_template_content:
            import re
            html_content = re.sub(
                r"const reportData\s*=\s*.*?;",
                f"const reportData = {safe_data_str};",
                custom_template_content
            )
        else:
            html_content = custom_template_content.replace(
                "</head>",
                f"<script>const reportData = {safe_data_str};</script></head>"
            )
    else:
        # Resolve predefined theme variations
        theme = template or "default"
        css_variables = {
            "default": """
                --bg-color: #0b0d10;
                --panel-bg: rgba(20, 24, 30, 0.7);
                --panel-border: rgba(255, 255, 255, 0.05);
                --text-primary: #f0f3f6;
                --text-secondary: #8b949e;
                --accent-color: #58a6ff;
                --accent-glow: rgba(88, 166, 255, 0.15);
                --success-color: #3fb950;
                --success-glow: rgba(63, 185, 80, 0.15);
                --warning-color: #d29922;
                --error-color: #f85149;
                --font-sans: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
                --font-title: 'Outfit', sans-serif;
            """,
            "dark": """
                --bg-color: #0d1117;
                --panel-bg: #161b22;
                --panel-border: #30363d;
                --text-primary: #c9d1d9;
                --text-secondary: #8b949e;
                --accent-color: #58a6ff;
                --accent-glow: rgba(88, 166, 255, 0.2);
                --success-color: #238636;
                --success-glow: rgba(35, 134, 54, 0.2);
                --warning-color: #d29922;
                --error-color: #da3633;
                --font-sans: 'Inter', sans-serif;
                --font-title: 'Outfit', sans-serif;
            """,
            "light": """
                --bg-color: #f6f8fa;
                --panel-bg: #ffffff;
                --panel-border: rgba(27, 31, 35, 0.15);
                --text-primary: #24292f;
                --text-secondary: #57606a;
                --accent-color: #0969da;
                --accent-glow: rgba(9, 105, 218, 0.15);
                --success-color: #1a7f37;
                --success-glow: rgba(26, 127, 55, 0.15);
                --warning-color: #9a6700;
                --error-color: #cf222e;
                --font-sans: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
                --font-title: 'Outfit', sans-serif;
            """,
            "retro": """
                --bg-color: #000000;
                --panel-bg: #050505;
                --panel-border: #00ff00;
                --text-primary: #00ff00;
                --text-secondary: #88ff88;
                --accent-color: #00ff00;
                --accent-glow: rgba(0, 255, 0, 0.2);
                --success-color: #00ff00;
                --success-glow: rgba(0, 255, 0, 0.2);
                --warning-color: #ffff00;
                --error-color: #ff0000;
                --font-sans: 'Courier New', Courier, monospace;
                --font-title: 'Courier New', Courier, monospace;
            """
        }
        
        body_style = {
            "default": """
                background-color: var(--bg-color);
                color: var(--text-primary);
                font-family: var(--font-sans);
                margin: 0;
                padding: 0;
                min-height: 100vh;
                background-image: 
                    radial-gradient(at 0% 0%, rgba(99, 102, 241, 0.1) 0px, transparent 50%),
                    radial-gradient(at 100% 100%, rgba(219, 39, 119, 0.05) 0px, transparent 50%),
                    radial-gradient(at 50% 0%, rgba(56, 189, 248, 0.05) 0px, transparent 50%);
                background-attachment: fixed;
                -webkit-font-smoothing: antialiased;
            """,
            "dark": """
                background-color: var(--bg-color);
                color: var(--text-primary);
                font-family: var(--font-sans);
                margin: 0;
                padding: 0;
                min-height: 100vh;
                background-image: none;
                -webkit-font-smoothing: antialiased;
            """,
            "light": """
                background-color: var(--bg-color);
                color: var(--text-primary);
                font-family: var(--font-sans);
                margin: 0;
                padding: 0;
                min-height: 100vh;
                background-image: 
                    radial-gradient(at 0% 0%, rgba(9, 105, 218, 0.05) 0px, transparent 50%),
                    radial-gradient(at 100% 100%, rgba(26, 127, 55, 0.03) 0px, transparent 50%);
                background-attachment: fixed;
                -webkit-font-smoothing: antialiased;
            """,
            "retro": """
                background-color: var(--bg-color);
                color: var(--text-primary);
                font-family: var(--font-sans);
                margin: 0;
                padding: 0;
                min-height: 100vh;
                background-image: none;
                border: 2px solid #00ff00;
                box-sizing: border-box;
            """
        }
        
        selected_theme = theme.lower()
        if selected_theme not in css_variables:
            selected_theme = "default"
            
        theme_vars = css_variables[selected_theme]
        selected_body = body_style[selected_theme]

        html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>TermStory Web Report</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=Outfit:wght@500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {{
            {theme_vars}
        }}

        body {{
            {selected_body}
        }}

        /* Scrollbar */
        ::-webkit-scrollbar {{
            width: 10px;
            height: 10px;
        }}
        ::-webkit-scrollbar-track {{
            background: var(--bg-color);
        }}
        ::-webkit-scrollbar-thumb {{
            background: rgba(255, 255, 255, 0.1);
            border-radius: 5px;
        }}
        ::-webkit-scrollbar-thumb:hover {{
            background: rgba(255, 255, 255, 0.2);
        }}

        .container {{
            max-width: 1400px;
            margin: 0 auto;
            padding: 40px 20px;
        }}

        /* Header style */
        header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 40px;
            flex-wrap: wrap;
            gap: 20px;
        }}
        header h1 {{
            font-family: var(--font-title);
            font-size: 2.2rem;
            font-weight: 800;
            margin: 0;
            background: linear-gradient(135deg, #fff 30%, var(--accent-color) 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            display: flex;
            align-items: center;
            gap: 12px;
        }}
        .header-meta {{
            text-align: right;
            color: var(--text-secondary);
            font-size: 0.9rem;
        }}

        /* Glassmorphism panel */
        .panel {{
            background: var(--panel-bg);
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
            border: 1px solid var(--panel-border);
            border-radius: 16px;
            padding: 24px;
            box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.4);
            transition: transform 0.3s cubic-bezier(0.16, 1, 0.3, 1), border-color 0.3s ease;
        }}
        .panel:hover {{
            border-color: rgba(88, 166, 255, 0.2);
        }}

        /* Heatmap Styles */
        .heatmap-cell {{
            width: 12px;
            height: 12px;
            border-radius: 2px;
            cursor: pointer;
            transition: transform 0.1s ease;
        }}
        .heatmap-cell:hover {{
            transform: scale(1.3);
            z-index: 10;
            outline: 1px solid var(--text-primary);
        }}
        .heatmap-cell.selected {{
            outline: 2px solid var(--accent-color);
            transform: scale(1.1);
        }}
        .level-0 {{ background-color: var(--panel-border); opacity: 0.3; }}
        .level-1 {{ background-color: var(--accent-color); opacity: 0.25; }}
        .level-2 {{ background-color: var(--accent-color); opacity: 0.5; }}
        .level-3 {{ background-color: var(--accent-color); opacity: 0.75; }}
        .level-4 {{ background-color: var(--accent-color); opacity: 1.0; }}

        /* KPI grid */
        .kpi-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 20px;
            margin-bottom: 40px;
        }}
        .kpi-card {{
            position: relative;
            overflow: hidden;
        }}
        .kpi-card::before {{
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 4px;
            background: linear-gradient(90deg, var(--accent-color), transparent);
        }}
        .kpi-value {{
            font-family: var(--font-title);
            font-size: 2.2rem;
            font-weight: 800;
            margin-top: 8px;
            color: var(--text-primary);
        }}
        .kpi-label {{
            color: var(--text-secondary);
            font-size: 0.85rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }}

        /* Main Grid Layout */
        .main-grid {{
            display: grid;
            grid-template-columns: 1fr 1.6fr;
            gap: 30px;
        }}
        @media (max-width: 1024px) {{
            .main-grid {{
                grid-template-columns: 1fr;
            }}
            header {{
                flex-direction: column;
                align-items: flex-start;
            }}
            .header-meta {{
                text-align: left;
            }}
        }}

        /* Projects */
        .project-row {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 12px 0;
            border-bottom: 1px solid rgba(255, 255, 255, 0.03);
        }}
        .project-row:last-child {{
            border-bottom: none;
        }}
        .project-info {{
            flex: 1;
        }}
        .project-name-text {{
            font-weight: 600;
            color: #fff;
            margin-bottom: 4px;
        }}
        .project-path-text {{
            font-size: 0.75rem;
            color: var(--text-secondary);
            word-break: break-all;
        }}
        .project-stats {{
            text-align: right;
            margin-left: 20px;
        }}
        .project-duration-text {{
            font-weight: 600;
            color: var(--accent-color);
        }}
        .project-sessions-text {{
            font-size: 0.75rem;
            color: var(--text-secondary);
            margin-top: 2px;
        }}

        /* Highlights */
        .highlight-item {{
            margin-bottom: 24px;
            padding-bottom: 16px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.03);
        }}
        .highlight-item:last-child {{
            margin-bottom: 0;
            padding-bottom: 0;
            border-bottom: none;
        }}
        .highlight-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 8px;
        }}
        .highlight-project-pill {{
            font-size: 0.75rem;
            font-weight: 700;
            padding: 2px 8px;
            border-radius: 4px;
            color: #fff;
        }}
        .highlight-date {{
            font-size: 0.75rem;
            color: var(--text-secondary);
        }}
        .highlight-body {{
            font-size: 0.9rem;
            line-height: 1.5;
            color: var(--text-primary);
        }}

        /* Search input & Toggle */
        .search-container {{
            display: flex;
            gap: 12px;
            margin-bottom: 24px;
        }}
        .search-input {{
            flex: 1;
            background: rgba(0, 0, 0, 0.2);
            border: 1px solid var(--panel-border);
            border-radius: 8px;
            padding: 10px 16px;
            color: var(--text-primary);
            font-family: var(--font-sans);
            font-size: 0.9rem;
            outline: none;
            transition: border-color 0.2s ease;
        }}
        .search-input:focus {{
            border-color: var(--accent-color);
        }}
        .filter-btn {{
            background: rgba(255, 255, 255, 0.03);
            border: 1px solid var(--panel-border);
            border-radius: 8px;
            padding: 0 16px;
            color: var(--text-primary);
            font-family: var(--font-sans);
            font-size: 0.85rem;
            cursor: pointer;
            transition: all 0.2s ease;
            white-space: nowrap;
        }}
        .filter-btn:hover {{
            background: rgba(255, 255, 255, 0.06);
            border-color: var(--accent-color);
        }}
        .filter-btn.active {{
            background: var(--accent-glow);
            border-color: var(--accent-color);
            color: var(--accent-color);
        }}

        /* Timeline structure */
        .timeline {{
            position: relative;
            padding-left: 24px;
            margin-left: 8px;
        }}
        .timeline::before {{
            content: '';
            position: absolute;
            top: 0;
            bottom: 0;
            left: 0;
            width: 2px;
            background: var(--panel-border);
        }}
        .timeline-item {{
            position: relative;
            margin-bottom: 30px;
        }}
        .timeline-item:last-child {{
            margin-bottom: 0;
        }}
        .timeline-dot {{
            position: absolute;
            left: -29px;
            top: 18px;
            width: 10px;
            height: 10px;
            border-radius: 50%;
        }}
        .session-card {{
            padding: 20px;
        }}
        .session-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 10px;
            margin-bottom: 12px;
        }}
        .session-meta-left {{
            display: flex;
            align-items: center;
            gap: 12px;
            flex-wrap: wrap;
        }}
        .session-project-pill {{
            font-size: 0.75rem;
            font-weight: 700;
            padding: 3px 10px;
            border-radius: 4px;
            color: #fff;
        }}
        .session-time-range {{
            font-size: 0.8rem;
            color: var(--text-secondary);
        }}
        .session-duration {{
            font-family: var(--font-title);
            font-size: 1.1rem;
            font-weight: 700;
            color: var(--accent-color);
        }}

        /* Commits in Session */
        .session-commits {{
            background: rgba(0, 0, 0, 0.15);
            border-radius: 8px;
            padding: 12px;
            margin-top: 12px;
            font-size: 0.85rem;
            border: 1px solid rgba(255, 255, 255, 0.02);
        }}
        .commit-row {{
            display: flex;
            gap: 12px;
            margin-bottom: 6px;
            line-height: 1.4;
        }}
        .commit-row:last-child {{
            margin-bottom: 0;
        }}
        .commit-hash {{
            font-family: monospace;
            color: var(--success-color);
            font-weight: bold;
        }}

        /* Commands List */
        .commands-toggle {{
            background: none;
            border: none;
            color: var(--text-secondary);
            font-family: var(--font-sans);
            font-size: 0.8rem;
            font-weight: 600;
            cursor: pointer;
            padding: 0;
            margin-top: 12px;
            display: flex;
            align-items: center;
            gap: 4px;
            outline: none;
        }}
        .commands-toggle:hover {{
            color: var(--text-primary);
        }}
        .commands-toggle svg {{
            transition: transform 0.2s ease;
        }}
        .commands-toggle.open svg {{
            transform: rotate(180deg);
        }}
        .commands-list {{
            max-height: 0;
            overflow: hidden;
            transition: max-height 0.2s ease-out;
            margin-top: 8px;
            background: rgba(0, 0, 0, 0.25);
            border-radius: 8px;
            font-family: monospace;
            font-size: 0.8rem;
            display: flex;
            flex-direction: column;
        }}
        .commands-list.open {{
            max-height: 400px;
            overflow-y: auto;
            border: 1px solid rgba(255, 255, 255, 0.02);
        }}
        .command-item {{
            padding: 8px 12px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.02);
            color: #d1d5db;
            word-break: break-all;
            white-space: pre-wrap;
        }}
        .command-item:last-child {{
            border-bottom: none;
        }}
        .command-item.error {{
            color: var(--error-color);
            border-left: 2px solid var(--error-color);
        }}
        .command-item.legacy {{
            color: var(--text-secondary);
            font-style: italic;
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div>
                <h1>⚡ TermStory Web Report</h1>
                <div style="color: var(--text-secondary); margin-top: 4px;">Developer Memory Engine & Activity Timeline</div>
            </div>
            <div class="header-meta">
                <div>Generated on <span id="generated-date">-</span></div>
                <div style="margin-top: 2px;">Last Ingested: <span id="last-ingested-date">-</span></div>
            </div>
        </header>
        
        <div class="kpi-grid">
            <div class="panel kpi-card">
                <div class="kpi-label">Total Sessions</div>
                <div class="kpi-value" id="kpi-sessions">-</div>
            </div>
            <div class="panel kpi-card">
                <div class="kpi-label">Total Commands</div>
                <div class="kpi-value" id="kpi-commands">-</div>
            </div>
            <div class="panel kpi-card">
                <div class="kpi-label">Total Projects</div>
                <div class="kpi-value" id="kpi-projects">-</div>
            </div>
            <div class="panel kpi-card">
                <div class="kpi-label">Coding Streak</div>
                <div class="kpi-value" id="kpi-streak">-</div>
            </div>
        </div>

        <!-- Heatmap Section -->
        <div class="panel" style="margin-bottom: 24px; padding: 20px;">
            <h3 style="font-family: var(--font-title); font-size: 1.1rem; margin-top: 0; margin-bottom: 12px; display: flex; align-items: center; gap: 8px;">
                <svg style="width: 18px; height: 18px; color: var(--accent-color)" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"></path></svg>
                Activity Heatmap (Last 90 Days)
            </h3>
            <div id="heatmap-grid" style="display: grid; grid-template-rows: repeat(7, 12px); grid-auto-flow: column; grid-auto-columns: 12px; gap: 3px; overflow-x: auto; padding-bottom: 10px;"></div>
            <div class="heatmap-legend" style="display: flex; align-items: center; font-size: 0.75rem; color: var(--text-secondary); margin-top: 10px; gap: 6px;">
                <span>Less</span>
                <div class="legend-cell level-0" style="width: 12px; height: 12px; border-radius: 2px;"></div>
                <div class="legend-cell level-1" style="width: 12px; height: 12px; border-radius: 2px;"></div>
                <div class="legend-cell level-2" style="width: 12px; height: 12px; border-radius: 2px;"></div>
                <div class="legend-cell level-3" style="width: 12px; height: 12px; border-radius: 2px;"></div>
                <div class="legend-cell level-4" style="width: 12px; height: 12px; border-radius: 2px;"></div>
                <span>More</span>
                <span id="heatmap-filter-info" style="margin-left: 15px; font-weight: bold; color: var(--accent-color);"></span>
                <button id="reset-date-filter" class="filter-btn" style="margin-left: 10px; display: none; padding: 2px 8px; font-size: 0.7rem;">Reset Date Filter</button>
            </div>
        </div>
        
        <div class="main-grid">
            <div>
                <!-- Left side: Active Projects & AI highlights -->
                <div class="panel" style="margin-bottom: 30px;">
                    <h2 style="font-family: var(--font-title); font-size: 1.3rem; margin-top: 0; margin-bottom: 20px; display: flex; align-items: center; gap: 8px;">
                        <svg style="width: 20px; height: 20px; color: var(--accent-color)" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10"></path></svg>
                        Active Projects
                    </h2>
                    <div id="projects-container"></div>
                </div>
                
                <div class="panel">
                    <h2 style="font-family: var(--font-title); font-size: 1.3rem; margin-top: 0; margin-bottom: 20px; display: flex; align-items: center; gap: 8px;">
                        <svg style="width: 20px; height: 20px; color: var(--success-color)" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"></path></svg>
                        AI Summary Highlights
                    </h2>
                    <div id="highlights-container"></div>
                </div>
            </div>
            
            <div>
                <!-- Right side: Recent Sessions Timeline -->
                <div class="panel" style="margin-bottom: 24px;">
                    <div class="search-container">
                        <input type="text" id="search-bar" class="search-input" placeholder="Search sessions, commands, commits, summaries...">
                        <button id="noise-toggle" class="filter-btn">Show Noise Cmds</button>
                    </div>
                    
                    <h2 style="font-family: var(--font-title); font-size: 1.3rem; margin-top: 0; margin-bottom: 24px;">Recent Work Timeline</h2>
                    <div class="timeline" id="timeline-container"></div>
                </div>
            </div>
        </div>
    </div>

    <script>
        const reportData = {safe_data_str};
        let showNoise = false;
        let searchQuery = "";
        let selectedDateFilter = null;

        function getProjectColor(name) {{
            let hash = 0;
            for (let i = 0; i < name.length; i++) {{
                hash = name.charCodeAt(i) + ((hash << 5) - hash);
            }}
            const hue = Math.abs(hash % 360);
            return `hsl(${{hue}}, 65%, 55%)`;
        }}

        function formatDuration(seconds) {{
            if (seconds <= 0) return "0s";
            if (seconds < 60) return `${{seconds}}s`;
            const hours = Math.floor(seconds / 3600);
            const mins = Math.floor((seconds % 3600) / 60);
            const secs = seconds % 60;
            if (hours > 0) {{
                return `${{hours}}h ${{mins}}m`;
            }}
            return `${{mins}}m ${{secs}}s`;
        }}

        function formatDate(timestamp) {{
            const date = new Date(timestamp * 1000);
            return date.toLocaleDateString(undefined, {{ month: 'short', day: 'numeric', year: 'numeric' }});
        }}

        function formatTime(timestamp) {{
            const date = new Date(timestamp * 1000);
            return date.toLocaleTimeString(undefined, {{ hour: '2-digit', minute: '2-digit' }});
        }}

        function escapeHtml(str) {{
            if (str === null || str === undefined) return "";
            return String(str)
                .replace(/&/g, "&amp;")
                .replace(/</g, "&lt;")
                .replace(/>/g, "&gt;")
                .replace(/"/g, "&quot;")
                .replace(/'/g, "&#039;");
        }}

        function renderProjects() {{
            const container = document.getElementById('projects-container');
            container.innerHTML = "";
            
            if (reportData.projects.length === 0) {{
                container.innerHTML = `<div style="color: var(--text-secondary); text-align: center; padding: 20px 0;">No active projects recorded.</div>`;
                return;
            }}
            
            reportData.projects.forEach(p => {{
                const color = getProjectColor(p.name);
                const row = document.createElement('div');
                row.className = 'project-row';
                row.innerHTML = `
                    <div class="project-info">
                        <div class="project-name-text" style="color: ${{color}}">${{escapeHtml(p.name)}}</div>
                        <div class="project-path-text">${{escapeHtml(p.path)}}</div>
                    </div>
                    <div class="project-stats">
                        <div class="project-duration-text">${{formatDuration(p.total_time)}}</div>
                        <div class="project-sessions-text">${{p.session_count}} sessions</div>
                    </div>
                `;
                container.appendChild(row);
            }});
        }}

        function renderHighlights() {{
            const container = document.getElementById('highlights-container');
            container.innerHTML = "";
            
            if (reportData.highlights.length === 0) {{
                container.innerHTML = `<div style="color: var(--text-secondary); text-align: center; padding: 20px 0;">No AI summary highlights available.</div>`;
                return;
            }}
            
            reportData.highlights.forEach(h => {{
                const color = getProjectColor(h.project_name);
                const item = document.createElement('div');
                item.className = 'highlight-item';
                item.innerHTML = `
                    <div class="highlight-header">
                        <span class="highlight-project-pill" style="background-color: ${{color}}">${{escapeHtml(h.project_name)}}</span>
                        <span class="highlight-date">${{formatDate(h.start_time)}}</span>
                    </div>
                    <div class="highlight-body">${{escapeHtml(h.ai_summary)}}</div>
                `;
                container.appendChild(item);
            }});
        }}

        function toggleCommands(sessionId) {{
            const el = document.getElementById(`commands-list-${{sessionId}}`);
            const btn = el.previousElementSibling;
            
            el.classList.toggle('open');
            btn.classList.toggle('open');
            
            if (el.classList.contains('open')) {{
                el.style.maxHeight = el.scrollHeight + "px";
            }} else {{
                el.style.maxHeight = 0;
            }}
        }}

        function renderTimeline() {{
            const container = document.getElementById('timeline-container');
            container.innerHTML = "";
            
            const filtered = reportData.sessions.filter(s => {{
                const matchesSearch = !searchQuery || 
                    s.project_name.toLowerCase().includes(searchQuery.toLowerCase()) ||
                    s.ai_summary.toLowerCase().includes(searchQuery.toLowerCase()) ||
                    s.commands.some(c => c.command.toLowerCase().includes(searchQuery.toLowerCase())) ||
                    s.commits.some(c => c.message.toLowerCase().includes(searchQuery.toLowerCase()));
                    
                let matchesDate = true;
                if (selectedDateFilter) {{
                    const sDate = new Date(s.start_time * 1000).toISOString().split('T')[0];
                    matchesDate = (sDate === selectedDateFilter);
                }}
                
                return matchesSearch && matchesDate;
            }});
            
            if (filtered.length === 0) {{
                container.innerHTML = `<div style="color: var(--text-secondary); text-align: center; padding: 40px 0;">No matching sessions found.</div>`;
                return;
            }}
            
            filtered.forEach(s => {{
                const color = getProjectColor(s.project_name);
                const cmdsToShow = s.commands.filter(c => showNoise || !c.is_noise);
                
                const item = document.createElement('div');
                item.className = 'timeline-item';
                
                let commitsHtml = "";
                if (s.commits && s.commits.length > 0) {{
                    commitsHtml = `
                        <div class="session-commits">
                            ${{s.commits.map(c => `
                                <div class="commit-row">
                                    <span class="commit-hash">${{escapeHtml(c.hash.substring(0, 7))}}</span>
                                    <span>${{escapeHtml(c.cleaned_message || c.message)}}</span>
                                </div>
                            `).join('')}}
                        </div>
                    `;
                }}
                
                let commandsHtml = "";
                if (cmdsToShow.length > 0) {{
                    commandsHtml = `
                        <button class="commands-toggle" onclick="toggleCommands(${{s.id}})">
                            <svg style="width: 14px; height: 14px;" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path></svg>
                            Show Commands (${{cmdsToShow.length}})
                        </button>
                        <div class="commands-list" id="commands-list-${{s.id}}">
                            ${{cmdsToShow.map(c => {{
                                let cl = 'command-item';
                                if (c.exit_code !== 0) cl += ' error';
                                if (c.is_legacy) cl += ' legacy';
                                return `<div class="${{cl}}">${{escapeHtml(c.command)}}</div>`;
                            }}).join('')}}
                        </div>
                    `;
                }}
                
                item.innerHTML = `
                    <div class="timeline-dot" style="background-color: ${{color}}"></div>
                    <div class="panel session-card">
                        <div class="session-header">
                            <div class="session-meta-left">
                                <span class="session-project-pill" style="background-color: ${{color}}">${{escapeHtml(s.project_name)}}</span>
                                <span class="session-time-range">${{formatDate(s.start_time)}} at ${{formatTime(s.start_time)}} - ${{formatTime(s.end_time)}}</span>
                            </div>
                            <span class="session-duration">${{formatDuration(s.duration_seconds)}}</span>
                        </div>
                        ${{s.ai_summary ? `<div class="highlight-body" style="font-style: italic; margin-bottom: 12px;">${{escapeHtml(s.ai_summary)}}</div>` : ''}}
                        ${{commitsHtml}}
                        ${{commandsHtml}}
                    </div>
                `;
                container.appendChild(item);
            }});
        }}

        function renderHeatmap() {{
            const grid = document.getElementById('heatmap-grid');
            grid.innerHTML = "";
            const dailyAct = reportData.daily_activity || {{}};
            const dates = Object.keys(dailyAct).sort();
            
            dates.forEach(dateStr => {{
                const act = dailyAct[dateStr];
                const cmdCount = act.commands;
                const sCount = act.sessions;
                
                let level = 0;
                if (cmdCount > 0) {{
                    if (cmdCount <= 5) level = 1;
                    else if (cmdCount <= 15) level = 2;
                    else if (cmdCount <= 30) level = 3;
                    else level = 4;
                }}
                
                const cell = document.createElement('div');
                cell.className = `heatmap-cell level-${{level}}`;
                if (selectedDateFilter === dateStr) cell.classList.add('selected');
                cell.title = `${{dateStr}}: ${{cmdCount}} commands, ${{sCount}} sessions`;
                cell.dataset.date = dateStr;
                
                cell.addEventListener('click', () => {{
                    if (selectedDateFilter === dateStr) {{
                        selectedDateFilter = null;
                        document.getElementById('reset-date-filter').style.display = 'none';
                        document.getElementById('heatmap-filter-info').textContent = "";
                    }} else {{
                        const prev = grid.querySelector('.heatmap-cell.selected');
                        if (prev) prev.classList.remove('selected');
                        
                        selectedDateFilter = dateStr;
                        cell.classList.add('selected');
                        document.getElementById('reset-date-filter').style.display = 'inline-block';
                        document.getElementById('heatmap-filter-info').textContent = `Filtering by: ${{dateStr}}`;
                    }}
                    renderTimeline();
                }});
                
                grid.appendChild(cell);
            }});
        }}

        document.addEventListener('DOMContentLoaded', () => {{
            document.getElementById('generated-date').textContent = new Date().toLocaleString();
            
            const lastIngested = reportData.stats.last_ingestion_time || 0;
            document.getElementById('last-ingested-date').textContent = lastIngested ? new Date(lastIngested * 1000).toLocaleString() : 'N/A';
            
            document.getElementById('kpi-sessions').textContent = reportData.stats.total_sessions;
            document.getElementById('kpi-commands').textContent = reportData.stats.total_commands;
            document.getElementById('kpi-projects').textContent = reportData.stats.total_projects;
            document.getElementById('kpi-streak').textContent = `${{reportData.stats.streak}} Days`;
            
            const searchInput = document.getElementById('search-bar');
            searchInput.addEventListener('input', (e) => {{
                searchQuery = e.target.value;
                renderTimeline();
            }});
            
            const noiseToggle = document.getElementById('noise-toggle');
            noiseToggle.addEventListener('click', () => {{
                showNoise = !showNoise;
                if (showNoise) {{
                    noiseToggle.textContent = "Hide Noise Cmds";
                    noiseToggle.classList.add('active');
                }} else {{
                    noiseToggle.textContent = "Show Noise Cmds";
                    noiseToggle.classList.remove('active');
                }}
                renderTimeline();
            }});
            
            document.getElementById('reset-date-filter').addEventListener('click', () => {{
                const grid = document.getElementById('heatmap-grid');
                const prev = grid.querySelector('.heatmap-cell.selected');
                if (prev) prev.classList.remove('selected');
                selectedDateFilter = null;
                document.getElementById('reset-date-filter').style.display = 'none';
                document.getElementById('heatmap-filter-info').textContent = "";
                renderTimeline();
            }});
            
            renderProjects();
            renderHighlights();
            renderHeatmap();
            renderTimeline();
        }});
    </script>
</body>
</html>
"""


    report_path = os.path.expanduser("~/.termstory/report.html")
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(html_content)
        
    print(f"Web report saved to: {report_path}")
    webbrowser.open(f"file://{os.path.abspath(report_path)}")
