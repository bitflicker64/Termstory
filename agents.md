# TermStory Developer Memory Engine — State & Context (agents.md)

This file serves as the active development state, architectural roadmap, and design philosophy context for TermStory to ensure seamless pairing and handoffs.

---

## 1. Core Philosophy: Developer Memory Engine

TermStory is **not** a tracking tool, productivity auditor, or a generic manager dashboard. It is a **personal developer memory engine** designed to trigger recognition.
* **Recognize, don't inspect**: Optimize for recognition ("What did I work on?"). Details ("How do you know?") belong in `--detailed` mode.
* **Density over decoration**: Avoid rounded panels, double borders, or nested boxes. Use clean column alignment, simple tables, and minimal spacing. There is a strict ban on `rich.panel.Panel` in favor of dense text separators to maintain this philosophy.
* **Screenshot-friendly**: Every screen should fit in a single terminal screen/screenshot and tell a compelling story about a developer's day, search, or project.
* **Map General to Other**: "General / No Project" or empty project names are mapped to `"Other"`.
* **Noise Filtering**: Filter out routine navigation, status, and inspection commands (like `cd`, `ls`, `docker ps`, `git status`, `docker logs`, `grep`, etc.) so only creative/memorable work remains.

---

## 2. Technical Component Deep-Dive & Architecture

### A. Parser Engine ([parser.py](file:///Users/himanshuverma/personal/termstory/termstory/parser.py))
Parses shell histories safely to extract Unix timestamps and clean command strings.
* **Zsh Parser**: Extracts Zsh logs written in the extended history format: `: <timestamp>:<duration>;<command>`. Handles multiline commands marked with a trailing backslash `\`. Switches to **Legacy Fallback Mode** if extended history format is disabled/absent, or **Hybrid Mode** if both legacy and timestamped commands coexist.
  - **Hybrid Mode**: Evaluates lines individually. Uses the oldest timestamped command's timestamp minus 1 minute as the `anchor_time` buffer for legacy commands. Skips malformed lines that do not match the expected pattern or start with a colon inside timestamped regions.
  - **Timestamp Locking**: Queries the database on ingestion to retrieve a map of command strings to stored timestamps. Sequentially locks in/reuses synthetic timestamps for legacy commands on subsequent launches to prevent shifting history.
* **Bash Parser**: Reads standard `.bash_history`. If `#<timestamp>` rows exist, it associates commands with their timestamps. If timestamps are missing, it spaces them backward and forward in 10-second intervals based on the file modification time (`mtime`).
* **Fish & PowerShell**: Dynamically discovers active history file formats and reads timestamped blocks or falls back to legacy interpolation when headers are absent.
* **Filtering Limits**: Filters out commands older than 5 years or with future timestamps to avoid database pollution.

### B. Session Builder ([session.py](file:///Users/himanshuverma/personal/termstory/termstory/session.py))
Chains chronological commands into sessions.
* **Threshold**: Groups commands into a single session if the idle time between consecutive commands is under **30 minutes**.
* **Attributes**: Computes session start time, end time, duration in seconds, and active commands.

### C. Project Resolver ([project.py](file:///Users/himanshuverma/personal/termstory/termstory/project.py))
Maps directory paths to logical project names.
* **VCS Root Detection**: Recursively scans parent directories looking for `.git`, `.hg`, or `.svn` root folders.
* **Configuration Inspection**: Reads metadata from build configurations (`pom.xml`, `package.json`, `setup.py`, `Cargo.toml`, etc.) to extract clean project names.
* **Normalization & Fallback**: Maps empty/general workspaces to `"Other"`. If a directory contains no VCS markers or configuration descriptors and does not lie inside a common project workspace folder (e.g. `~/Projects/`), it falls back to the user's home directory so its commands and sessions are cleanly grouped in `"Other"`.

### D. Git Correlator ([git_integration.py](file:///Users/himanshuverma/personal/termstory/termstory/git_integration.py))
Enriches shell activity by fetching corresponding Git commits.
* **Subprocess Spawning**: Runs local `git log` commands filtered by timestamp ranges corresponding to session start and end times.
* **Cleaning Heuristics**: Strips conventional commit tags (e.g. `feat:`, `fix(tui):`), emojis, merge hashes, and branch pointers from commit messages to feed clean content to the database and AI prompts.

### E. Database Layer ([database.py](file:///Users/himanshuverma/personal/termstory/termstory/database.py))
Manages SQLite storage under `~/.termstory/termstory.db`.
* **WAL Mode & Concurrency**: Executes `PRAGMA journal_mode = WAL;` for non-blocking concurrent reads/writes. Uses a `30.0s` timeout in `sqlite3.connect` to support massive batch ingestions. Employs explicit `BEGIN IMMEDIATE` transactions during bulk data ingestion to eliminate SQLite Upgrade Deadlocks, and uses `INSERT OR IGNORE` to mitigate race conditions during concurrent UI reads.
* **Resilience**: Initialization is wrapped in `safe_init_db` which features Corrupt DB fallback logic. If a `DatabaseError` is encountered, it seamlessly renames the corrupted database to a timestamped `.bak` file and initializes a fresh database to prevent application bricking.
* **Index Strategy**: Optimizes search, TUI loads, and timeline scrolling via:
  - `idx_commands_timestamp` on `commands(timestamp)`
  - `idx_commands_session_id` on `commands(session_id)`
  - `idx_sessions_start_time` on `sessions(start_time DESC)`
  - `idx_sessions_project_id` on `sessions(project_id)`
  - `idx_commits_timestamp` on `commits(timestamp DESC)`
  - `idx_commits_project_id` on `commits(project_id)`
* **Cache Architecture**: The `macro_summaries` table stores AI summaries for Months and Dates to bypass repeated API calls. Columns: `timeframe_id` (UNIQUE), `type` (`date`/`month`), `summary`, `created_at`.
* **Session Summaries**: Stored directly in the `sessions.ai_summary` column.

### F. Privacy Sanitizer ([sanitizer.py](file:///Users/himanshuverma/personal/termstory/termstory/sanitizer.py))
A local pipeline that redacts sensitive parameters prior to LLM submission.
* **Blacklist Operations**: Drops sessions containing high-risk keywords (e.g., `vault login`, `aws configure`, `gh auth`, `create secret`) and returns `"Security/Authentication Operations"` directly.
* **Credential Redaction**: Replaces parameters marked with flags (`--password`, `-p`, `--token`, `--api-key`) with `[REDACTED]`.
* **IP / Host Masking**: Redacts IPv4/IPv6 addresses, URL hosts, and FQDNs. Skips common source/config extensions (like `.py`, `.json`, `.db`, `.sh`, `.yml`) to preserve file names.

### G. Zero-Dependency AI Client ([ai.py](file:///Users/himanshuverma/personal/termstory/termstory/ai.py))
Interfaces with LLMs using Python's native `urllib.request`.
* **Endpoint Normalization**: Strips trailing slashes from `api_base_url` and appends `/chat/completions` dynamically.
* **Key Sanitization**: Skips building empty `Authorization` headers to ensure compatibility with local engines (e.g., Ollama).
* **Timeout Protection**: Sets a 15.0s timeout to prevent thread blocks during TUI queries.

### H. Legacy History Interpolation Engine ([timestamp_detective.py](file:///Users/himanshuverma/personal/termstory/termstory/timestamp_detective.py))
Handles Zsh and Bash histories that lack extended timestamps. Forensically recovers timestamps and fills gaps using anchor interpolation.
* **Session-Preserving Burst Clustering**: Groups un-timestamped commands into chunks of 20. Interpolates chunks historically while keeping internal chunk commands precisely 10 seconds apart, ensuring they naturally fuse into coherent TermStory sessions.
* **Circadian Monotonic Snapping**: Synthetic chunks are forced into a 9 AM - 6 PM weekday window. Weekend timestamps are snapped backwards to Friday afternoon. A monotonic tracker artificially offsets colliding chunks to prevent interleaved session destruction.
* **30-Day Metric Buffer**: The synthetic timestamp window strictly ends 30 days prior to the history file's modification time. This creates an impenetrable buffer that guarantees legacy commands cannot pollute `termstory today`.
* **UX Metric Exclusions**: Commands flagged as `is_legacy=True` are explicitly omitted from the TUI Heatmap, Streak counters, and Insights metrics to prevent the illusion of perfect, unbroken coding streaks.

### I. Utilities ([date_utils.py](file:///Users/himanshuverma/personal/termstory/termstory/date_utils.py) & [models.py](file:///Users/himanshuverma/personal/termstory/termstory/models.py))
* **date_utils.py**: Timezone boundary handling and human-readable timestamp formatting.
* **models.py**: Core Python dataclasses representing Command, Session, Project, and Commit objects.

### J. Analytics Engine ([insights.py](file:///Users/himanshuverma/personal/termstory/termstory/insights.py))
Calculates macro-telemetry like developer focus scores, overall time distribution across projects, and daily productivity density metrics.

---

## 3. Command Redesign & Feature Status

### 🔍 `termstory search`
* **Status**: Implemented, tested, and pushed.
* **Features**: Groups by project, collapses multiple daily sessions into a single line per day, prioritizes commits over commands, filters noise commands, maps General to Other, and searches AI-generated session summaries in addition to command text, project names, and git commits.
* **Files**: [formatter.py](file:///Users/himanshuverma/personal/termstory/termstory/formatter.py) (`format_search_results`, `_is_noise_command`, `_get_session_memory`, `_collapse_by_day`), [database.py](file:///Users/himanshuverma/personal/termstory/termstory/database.py) (`search_sessions`).

### 📋 `termstory today`
* **Status**: Implemented, tested, and pushed.
* **Features**: Clean bulleted timeline per project with project-level duration summaries, yesterday comparison support, and noise filtering.
* **Files**: [formatter.py](file:///Users/himanshuverma/personal/termstory/termstory/formatter.py) (`format_today_output`).

### 📁 `termstory project`
* **Status**: Implemented, tested, and pushed.
* **Features**: Replaced cards with a clean, high-density, box-free list of dates and milestone accomplishments/memories per day.
* **Files**: [formatter.py](file:///Users/himanshuverma/personal/termstory/termstory/formatter.py) (`format_project_output`).

### 💡 `termstory insights` / Highlights
* **Status**: Implemented, tested, and pushed.
* **Features**: Overhauled cards, empty charts, and focus score metrics into a compact, clean executive highlights list showing project active days, total duration, and main achievements.
* **Files**: [formatter.py](file:///Users/himanshuverma/personal/termstory/termstory/formatter.py) (`format_insights_output`, `_get_project_main_achievement`).

### 💻 `termstory ui` (Textual TUI Dashboard)
* **Status**: Fully implemented, refined, and verified.
* **Layout Structure**:
  - `StatsHeader` at the top showing active days, streaks, total duration, and a GitHub-style activity heatmap syncing with the timeline's active `days_limit` (defaults to 90 days).
  - Main panel split 30% / 70% between the `HistoryTree` explorer (which hides the root node `Timeline Explorer` to maximize space) and the scrollable `DetailsCanvas`.
  - Simple modal help overlay (`HelpScreen`) toggleable via `?`, dismissible with `Esc`/`q`/Close.
  - Interactive onboarding popup screen (`OnboardingScreen`) triggered when no config is detected, supporting key shortcuts for Groq (`Ctrl+G`), OpenAI (`Ctrl+A`), Ollama (`Ctrl+L`), Custom (`Ctrl+C`), or Disable (`Ctrl+D`).
* **Performance**: Utilizes `@work` async workers to fetch AI summaries on background threads. Implemented session date caching to eliminate rendering lags.
* **Robust OS-Level Copying**: Overrides `copy_to_clipboard` to pipe copy commands directly to local OS utilities (`pbcopy`, `xclip`/`xsel`/`wl-copy`, `clip`) so that copy shortcut `c` writes selections directly to the host operating system's clipboard even when OSC 52 terminal sequences are disabled.
* **Unified Global Search with Scope Escape**: Combined the TUI and CLI search logic on a single SQLite matching engine. TUI filters the pre-loaded 90-day timeline in real-time as you type, and escapes to an all-time deep history search on `Enter`. Pressing `Escape` or clearing the input immediately restores the normal timeline.
* **Files**: [tui.py](file:///Users/himanshuverma/personal/termstory/termstory/tui.py), [test_tui.py](file:///Users/himanshuverma/personal/termstory/tests/test_tui.py), [cli.py](file:///Users/himanshuverma/personal/termstory/termstory/cli.py).

### 🎨 UI Refinement, Timeline Alignment & Rich Narrative Summaries
* **Status**: Implemented, polished, and verified.
* **Features**:
  - **Dynamic Heatmap & Header Alignment**: Synced stats header labels (`Activity (Last N Days):`) and the command volume heatmap to match the timeline limit dynamically.
  - **Narrative AI Prompt Redesign**: Updated prompts in [ai.py](file:///Users/himanshuverma/personal/termstory/termstory/ai.py) to replace marketing paragraphs with high-density, CLI-styled console logs using ASCII tree branches (`├─`, `└─`) or tech bullets (`•`) detailing built targets, flow details, and outcome results in a clean, developer-focused structure.
  - **Tree Explorer Cleanups**: Hid the redundant `"Timeline Explorer"` tree root node via constructor args, zeroed margins and tree padding, and centered the footer shortcuts.
* **Files**: [tui.py](file:///Users/himanshuverma/personal/termstory/termstory/tui.py), [ai.py](file:///Users/himanshuverma/personal/termstory/termstory/ai.py), [test_tui.py](file:///Users/himanshuverma/personal/termstory/tests/test_tui.py), [test_ai.py](file:///Users/himanshuverma/personal/termstory/tests/test_ai.py).

### 📖 Upgraded Daily AI System Prompt & TUI Chronicle Integration
* **Status**: Fully implemented, integrated, and verified.
* **Features**:
  - **Narrative Daily Chronicle**: Embedded the upgraded "Story of You" system prompt in [ai.py](file:///Users/himanshuverma/personal/termstory/termstory/ai.py) utilizing second-person narrative ("You"), dynamic GitHub handle resolution, inferred breaks, and ASCII connection formatting.
  - **Activity Punch-Card**: Integrated a dynamic horizontal activity punch-card visual strip (`00:00 ░░░░░ 06:00 ... 23:59`) based on hourly command intensity counts.
  - **TUI Integration**: Completely unified the Daily Chronicle inside `termstory ui`'s `DetailsCanvas` when date nodes are selected, maintaining standard session detail feeds at the bottom for full visibility and single-session interactions.
* **Files**: [tui.py](file:///Users/himanshuverma/personal/termstory/termstory/tui.py), [ai.py](file:///Users/himanshuverma/personal/termstory/termstory/ai.py), [formatter.py](file:///Users/himanshuverma/personal/termstory/termstory/formatter.py), [cli.py](file:///Users/himanshuverma/personal/termstory/termstory/cli.py).

### 🛡️ Empty State Safeguards & Stricter Project Resolution Fallback
* **Status**: Fully implemented, integrated, and verified.
* **Features**:
  - **TUI Empty State Banner & Guards**: Handled empty shell histories by showing a welcoming troubleshooting/permission guide in `DetailsCanvas`, displaying a warning notification on mount, and guarding/disabling AI summary actions when 0 sessions are ingested.
  - **Project Fallback Strictness**: Overhauled `find_project_root` in [project.py](file:///Users/himanshuverma/personal/termstory/termstory/project.py) to return `home` (instead of the absolute folder path) when a folder is not inside standard project roots or paths, correctly mapping irrelevant folders (like `~/.ssh`, `~/Downloads`) to `"Other"`.
* **Files**: [project.py](file:///Users/himanshuverma/personal/termstory/termstory/project.py), [tui.py](file:///Users/himanshuverma/personal/termstory/termstory/tui.py), [cli.py](file:///Users/himanshuverma/personal/termstory/termstory/cli.py), [test_project.py](file:///Users/himanshuverma/personal/termstory/tests/test_project.py), [test_tui.py](file:///Users/himanshuverma/personal/termstory/tests/test_tui.py).

### 📊 Unified Wrapped View for Overall Timeline
* **Status**: Fully implemented, integrated, and verified.
* **Features**:
  - **Overall Wrapped View**: Modified the root `Timeline` tree node in `termstory ui` to show a beautiful, high-density, aggregated "All-Time / Timeline Wrapped" dashboard.
  - **Comprehensive Telemetry**: Updated `get_month_wrapped_telemetry` to support `timeframe_id == "overall"`, which dynamically calculates additions, deletions, time sinks, and terminal diagnostics over all ingested history without date boundaries.
  - **AI Chronicler Audit**: Integrated `generate_wrapped_summary` for the overall review, allowing the user to generate/regenerate an all-time perceptive roast/behavioral audit.
* **Files**: [tui.py](file:///Users/himanshuverma/personal/termstory/termstory/tui.py), [test_tui.py](file:///Users/himanshuverma/personal/termstory/tests/test_tui.py).

### ⚙️ Interactive Configuration Consent (White Glove Setup)
* **Status**: Fully implemented, integrated, and verified.
* **Features**:
  - **Missing Timestamps Detection**: Parser detects if Zsh history lacks extended history prefixes and sets `TERMSTORY_MISSING_TIMESTAMPS` environment variable.
  - **White Glove Onboarding Consent**: CLI show_ui command blocks startup to prompt users with missing timestamps to automatically append `setopt EXTENDED_HISTORY` to their `~/.zshrc`.
  - **Safe Exit & Fallback**: Exits gracefully with clear restart instructions if user confirms (`Y`), or continues directly to the legacy TUI fallback if user declines (`N`).
* **Files**: [cli.py](file:///Users/himanshuverma/personal/termstory/termstory/cli.py), [parser.py](file:///Users/himanshuverma/personal/termstory/termstory/parser.py), [test_cli_commands.py](file:///Users/himanshuverma/personal/termstory/tests/test_cli_commands.py).

### 🛡️ Advanced Concurrency, Thread-Safety & Resilience
* **Status**: Fully implemented, integrated, and verified.
* **Features**:
  - **SQLite Upgrade Deadlocks**: Fixed database deadlocks and race conditions during concurrent pane reads by using explicit `BEGIN IMMEDIATE` transactions in `save_data` and widespread `INSERT OR IGNORE` queries. Added a `30.0s` connection timeout for massive batch ingestions.
  - **Main-Thread Freezing Fixes**: Offloaded heavy UI operations and API calls to background threads using Textual's `@work(thread=True)` workers, eliminating TUI stutters.
  - **Thread Starvation 2-Factor Guards**: Implemented `exclusive=True` on UI workers to prevent thread pool exhaustion from rapid user inputs. Added a secondary wall-clock threading timeout (`worker_thread.join(timeout + 1.0)`) and circuit breakers in `ai.py` on top of socket timeouts to ensure hung LLM processes cannot consume all threads.
  - **Corrupt DB Fallback Logic**: Hardened `safe_init_db` in `cli.py` to automatically catch `sqlite3.DatabaseError`. If corruption is detected, it moves the corrupted database to a `.bak` file and transparently reinitializes a fresh database without bricking the application.
  - **Schema Integrity**: Migrated the `UNIQUE` constraint in the `projects` table from `name` to `path` to prevent irreversible fusion of identically named project folders.
  - **UI Philosophy Enforcement**: Reaffirmed the strict ban on `rich.panel.Panel` in favor of dense text separators to ensure the "density over decoration" philosophy remains intact.
* **Files**: [database.py](file:///Users/himanshuverma/personal/termstory/termstory/database.py), [tui.py](file:///Users/himanshuverma/personal/termstory/termstory/tui.py), [cli.py](file:///Users/himanshuverma/personal/termstory/termstory/cli.py), [ai.py](file:///Users/himanshuverma/personal/termstory/termstory/ai.py), [formatter.py](file:///Users/himanshuverma/personal/termstory/termstory/formatter.py).

### ⚡ LIFO Debouncing & In-Flight AI Cancellation
* **Status**: Fully implemented, integrated, and verified.
* **Features**:
  - **Cooperative Cancellation**: Centralized check via `_is_current_worker_cancelled()` in `_send_llm_request` that aborts slow API requests and immediately unblocks worker threads when a new request is triggered.
  - **Thread Starvation Mitigation**: Periodic worker cancellation checks in telemetry calculations (`get_month_wrapped_telemetry`) and bulk generators (`bulk_generate_sessions_stories`) prevent CPU/thread starvation when navigating rapidly.
  - **Clean State Recovery**: Safe handling of cancelled workers so session state flags are correctly restored without rendering partial UI states or triggering false fail notifications.
* **Files**: [tui.py](file:///Users/himanshuverma/personal/termstory/termstory/tui.py), [ai.py](file:///Users/himanshuverma/personal/termstory/termstory/ai.py), [test_tui.py](file:///Users/himanshuverma/personal/termstory/tests/test_tui.py).

### 🛠️ Resolved Issues (June 2026 Invalidation Cycle)
* **Status**: Fully resolved, tested, and verified.
* **Resolved Issues**:
  - **Multiplexer PROMPT_COMMAND injection (Zsh/Bash)**: Ensured both the Bash history multiline parser and the Zsh parser reset state cleanly when encountering terminal multiplexer boundary sequences (`_zellij`, `tmux`, `prompt_command`, `kitty +kitten`), preventing command corruption.
  - **NFS/SMB symlink loop protection**: Wrapped `os.listdir` calls inside `find_project_root` with a strict `0.5s` daemon thread timeout. Implemented blacklist/whitelist path prefixes (`/mnt`, `/Volumes/smb`, and UNC `\\` paths) to automatically bypass network mounts without hanging.
  - **Banish rich.panel.Panel**: Audited and confirmed total absence of `rich.panel.Panel` in `formatter.py` and `tui.py` in favor of dense terminal-native line characters.
  - **Banish thick/tall borders**: Replaced all heavy TUI stylesheet border properties (`border: thick`, `border: tall`) with simpler alternatives like `solid` or `none` to conform with density guidelines.
  - **Kitty +kitten state reset**: Confirmed presence of `kitty +kitten` check in both the Zsh and Bash multiline state reset boundary checkers.
* **Files**: [parser.py](file:///Users/himanshuverma/personal/termstory/termstory/parser.py), [project.py](file:///Users/himanshuverma/personal/termstory/termstory/project.py), [tui.py](file:///Users/himanshuverma/personal/termstory/termstory/tui.py), [formatter.py](file:///Users/himanshuverma/personal/termstory/termstory/formatter.py), [test_parser.py](file:///Users/himanshuverma/personal/termstory/tests/test_parser.py), [test_project.py](file:///Users/himanshuverma/personal/termstory/tests/test_project.py).

---

## 4. Running Verification

Always verify changes using:
```bash
python3 -m pytest tests/
```
And manually inspect outputs via:
```bash
python3 -m termstory.cli today
python3 -m termstory.cli project termstory
python3 -m termstory.cli insights
python3 -m termstory.cli ui
```

---

## 5. Current Batch & Roadmap

### Batch 3 — In Progress (June 15)
Implementing the 3 R&D concepts below via agy:
1. **SQLite FTS5 Integration**: Leveraging SQLite's Full-Text Search extension to dramatically speed up deep-history string matching and provide ranked search capabilities across sessions, commands, and AI summaries.
2. **Concurrency Stress Tests & Massive History Simulations**: Hardening the test suite by synthesizing massive, multi-year history logs to simulate and prevent race conditions and lock-ups during worst-case ingestion scenarios.
3. **Project-Specific AI Contexts**: Enriching LLM summaries by seeding prompt configurations with project-specific context descriptors, readmes, or language contexts to yield more accurate, tailored narratives per repository.

### Futuristic Concepts & Long-Term Vision

1. **"REM Sleep" Context Consolidation**: Overnight meta-pattern fusing.
2. **MCP (Model Context Protocol) Time-Machine Snapshots**: Semantic snapshots of external tools.
3. **"Ghost-in-the-Shell" TUI Playback**: `termstory replay` interactive playback.
4. **Pre-Cognitive Workspace**: Branch Prediction for Devs (e.g. Friday context loaded on Monday).
5. **Semantic Deep-Dive via Local RAG**: Zero-Keyword Search using local embeddings.

*Note: For a more comprehensive overview of these long-term vision goals, see `ROADMAP.md`.*
