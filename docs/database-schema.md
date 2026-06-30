#test1
## 9. Database Schema & Thread Safety

`~/.termstory/termstory.db` — SQLite with WAL mode (`PRAGMA journal_mode = WAL`).
Features extensive concurrency safety measures:
- **SQLite Deadlock Fixes**: Uses explicit `BEGIN IMMEDIATE` transactions during bulk data ingestion to eliminate upgrade deadlocks and `INSERT OR IGNORE` to mitigate race conditions during concurrent UI reads. Includes a 30.0s connection timeout for massive batch ingestions.
- **Thread Starvation Guards**: Offloads heavy operations to background threads using Textual's `@work(thread=True)` with `exclusive=True` to prevent thread pool exhaustion. Added a secondary wall-clock threading timeout (`worker_thread.join(timeout + 1.0)`) and circuit breakers in `ai.py` to ensure hung LLM processes cannot freeze the UI.
- **Corrupt DB Fallback**: `safe_init_db` automatically catches `sqlite3.DatabaseError`, moves the corrupted DB to a `.bak` file, and gracefully reinitializes a fresh database without bricking the application.

```sql
CREATE TABLE projects (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    path        TEXT UNIQUE,
    first_seen  INTEGER,
    last_seen   INTEGER,
    created_at  INTEGER DEFAULT (strftime('%s', 'now'))
);

CREATE TABLE sessions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    start_time       INTEGER NOT NULL,
    end_time         INTEGER NOT NULL,
    duration_seconds INTEGER,
    project_id       INTEGER REFERENCES projects(id),
    ai_summary       TEXT,       -- Cached AI narrative; reused across runs
    created_at       INTEGER DEFAULT (strftime('%s', 'now'))
);

CREATE TABLE commands (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       INTEGER NOT NULL,
    command         TEXT NOT NULL,
    exit_code       INTEGER,
    session_id      INTEGER REFERENCES sessions(id),
    project_id      INTEGER REFERENCES projects(id),
    recovery_source TEXT,    -- Chain of Custody attribution string, NULL for real timestamps
    created_at      INTEGER DEFAULT (strftime('%s', 'now'))
);

CREATE TABLE commits (
    hash            TEXT PRIMARY KEY,
    timestamp       INTEGER NOT NULL,
    message         TEXT NOT NULL,
    cleaned_message TEXT NOT NULL,
    project_id      INTEGER REFERENCES projects(id),
    created_at      INTEGER DEFAULT (strftime('%s', 'now'))
);

CREATE TABLE macro_summaries (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    timeframe_id TEXT NOT NULL UNIQUE,  -- e.g. "2026-06-03" or "June 2026" or "overall"
    type         TEXT NOT NULL,         -- "date", "month", or "overall"
    summary      TEXT NOT NULL,
    created_at   INTEGER DEFAULT (strftime('%s', 'now'))
);
```

**Indexes:** `idx_commands_timestamp`, `idx_commands_session_id`, `idx_sessions_start_time`, `idx_sessions_project_id`, `idx_sessions_date_range`, `idx_commits_timestamp`, `idx_commits_project_id`, `idx_sessions_project_date`.

**Deduplication:** Unique constraints on `sessions(start_time)` and `commands(timestamp, command)` prevent re-ingestion duplicates. One-time migration cleans any pre-constraint duplicates on first upgrade.

---
