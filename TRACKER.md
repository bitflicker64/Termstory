# Project Tracker

## Version
* **Current Version**: `0.6.0` (released on June 17, 2026)
* **Latest Release Details**:
  * **Unified Subcommand Documentation**: Aligned `README.md` and CLI documentation with all recently added subcommands (`rpg-class`, `vampire-index`, `necromancer`, `rage-quit`, `profile`, `fortune-teller`, `anger-translator`, `search --semantic`, `project context`, and `predict`).
  * **Repository Housekeeping**: Completed checking off all batch statuses (batches 5-11) in the tracking system to cleanly close out recent feature expansions.
  * **Version Promotion**: Promoted version to `0.6.0` across the package core files.

## Completed
* **REM Sleep Context Consolidation (Batch 15)**: Added REM Sleep background context consolidation daemon in `termstory/reminder.py` running during idle periods (30+ min gaps). Clusters similar commands using embeddings from `sentence-transformers`, creates context summaries, and stores in the SQLite database. Added CLI commands `termstory sleep --consolidate` and `termstory sleep --show` to trigger and view contexts, escaping LLM outputs with `escape()`.
* **SQLite FTS5 Integration (Batch 14)**: Added SQLite FTS5 virtual tables (`commands_fts`, `sessions_fts`, `ai_summaries_fts`) with triggers on insert/update/delete to keep FTS in sync. Added `--fts` flag to CLI search command to search using the new FTS5 tables.
* **Documentation Updates & Refinement (Batch 13p3)**: Updated README.md with all current CLI commands, updated CHANGELOG.md for v0.6.0, updated WHAT_WORKS.md, WORKFLOW.md, DATA_PRIVACY.md, features.md, and issues.md to ensure all references to removed planning files are cleaned up, and documented batch 13p3 completion.
* **Developer Memory Engine Documentation Overhaul (Batch 13p2)**: Conducted repository-wide code and module analysis of all modules and tests in the repository. Generated a highly comprehensive and detailed `agents.md` mapping core features, class hierarchies, database schemas, test methodologies, and data flow.
* **MCP Time-Machine Snapshots (Batch 13)**: Create `mcp_snapshot.py` to capture active IDE state, Git status, and active terminal working directory. Store snapshots in `mcp_snapshots` SQLite table. Add `termstory replay --mcp <session_id>` CLI subcommand and custom formatters to display captured snapshots.
* **Documentation & Technical Debt Cleanup (Batch 12)**: Align README.md with all new subcommands, update plans/tasks/tracker tracking systems.
* **Project-Specific AI Contexts (Batch 11)**: Added `termstory project context` CLI subcommand for context setting and viewing, wired `project_context` from database to AI prompts in `ai.py` for `generate_ai_summary` and `generate_executive_review`, enhanced `termstory predict` with `--json` and `--days`, and escaped all LLM outputs.
* **Ghost Typer Playback & Web Export (Batch 10)**: Add interactive chronicle playback with `p` keybinding in TUI, `--template` and `--date-range` flags for HTML exports, calendar heatmap filtering in web templates, and escaped LLM outputs.
* **Local RAG Search / Semantic Deep-Dive (Batch 9)**: Local semantic/RAG hybrid search across commits, commands, and project names using TF-IDF/BM25 combined with LLM processing. Expose via `search --semantic`.
* **Cyberpunk TUI/UX Polish (Batch 8)**: Cascading falling green characters Matrix animation in TUI during log ingestion, heatmap hover CSS animations, streak milestone glitch effects, and interactive chronicle playback.
* **Project Necromancy & Rage-Quit Signatures (Batch 7)**: Track Project Necromancer Score (reactivation rate of dormant repositories) and Rage-Quit Signatures (most common final commands prior to 12h+ inactivity). Expose via `necromancer` and `rage-quit` subcommands.
* **RPG Classes & Vampire Coder Metrics (Batch 6)**: Assign RPG developer class (e.g. `Regex Sorcerer`, `Docker Demolitionist`) based on command patterns, with optional AI biography generation. Calculate and display Vampire Coder Index. Expose via `rpg-class` and `vampire-index` subcommands.
* **Anger Translator & Predictive Bug Fortunes (Batch 5)**: Converts frustrated commands into humorous logs and forecasts likely code failures based on command patterns.
* **TUI Dashboard (`termstory ui`)**: Text-based user interface with stats header, streak trackers, command volume heatmap, and details canvas.
* **CLI Command Suite**:
  * `termstory today`: timeline summary per project.
  * `termstory search`: chronological session search with escape fallback.
  * `termstory project`: milestones and memories for specific workspace.
  * `termstory insights`: macro productivity density metrics.
  * `termstory predict`: heuristic CLI command predictive telemetry.
  * `termstory replay`: timeline and command playbacks.
  * `termstory profile`: user profile configuration.
  * `termstory agy`: orchestrated bridge to `agy -p` for live AI pair-programming.
  * `termstory ask`: Q&A over history with TF-IDF and LLMs.
  * `termstory optimize`: reclaims SQLite disk space via `VACUUM`.
  * `termstory --version`: reports current version from `__version__`.
* **Legacy History Interpolation (Timestamp Detective)**:
  * **Session-Preserving Burst Clustering**: Groups un-timestamped commands into chunks of 20 spaced 10 seconds apart.
  * **Circadian Monotonic Snapping**: Snaps synthetic timestamps to 9 AM - 6 PM weekday window, forcing weekend ones to Friday afternoon.
  * **30-Day Buffer Bounds**: Prevents legacy commands from polluting recent timelines by placing them 30+ days prior to history `mtime`.
  * **UX Metric Exclusions**: Legacy commands are omitted from TUI heatmap, streak counters, and insights to avoid artificial inflation.
  * **Legacy Badging**: Synthetic chunks explicitly labeled `[Legacy Archive]` in TUI/CLI.
* **Robust UI Refinements & Performance**:
  * **Concurrency & Safety**: WAL mode, 30s connection timeout, explicit `BEGIN IMMEDIATE` transactions, corrupt DB auto-rotation.
  * **No Main-Thread Freezes**: Background `@work(thread=True)` workers with two-factor thread starvation guards and cooperative worker cancellation.
  * **Aesthetic Discipline**: Strict adherence to "density over decoration" philosophy (no `rich.panel.Panel`, minimal borders).
  * **Unified Global Search**: SQLite matching engine for timeline real-time filtering and all-time escape on `Enter`.
  * **Upgraded Daily AI System Prompt**: Second-person narrative daily chronicle with horizontal activity punch-card visual strip.
  * **Overall Wrapped View**: "All-Time / Timeline Wrapped" dashboard telemetry and AI Roast/Audit generator.
  * **White Glove Setup**: Automatic detection of missing Zsh timestamps and interactive prompt to enable `EXTENDED_HISTORY`.
* **CI/CD Workflow**: GitHub Actions running `pytest` across Python 3.9–3.12 and automated PyPI releases.

## In Progress
* **None**

## Planned
* **None**

## Backlog
* **Long-Term R&D Concepts**:
  * **Semantic Deep-Dive via Local RAG**: Zero-keyword query searching via locally generated command/commit embeddings.
  * **Concurrency Stress Tests & Massive History Simulations**: Hardening the test suite by synthesizing massive, multi-year history logs to simulate worst-case ingestion scenarios.

## Known Issues
* **Outstanding Bugs**: **None**. All 15 previously tracked architectural threats have been resolved.

## Batch Status
* **Batch 3 v4 — FTS5 + Stress + AI Contexts**: ✅ MERGED (PR #11)
* **Batch 4 v4 — Release + Profile + Refactor**: ✅ MERGED
* **Batch 5 — AI-Driven Git Translation & Predictive Bug Fortunes**: ✅ Completed
* **Batch 6 — RPG Classes & Vampire Coder Metrics**: ✅ Completed
* **Batch 7 — Project Necromancy & Rage-Quit Signatures**: ✅ Completed
* **Batch 8 — Cyberpunk TUI/UX Polish**: ✅ MERGED (PR #19)
* **Batch 9 — Local RAG Search / Semantic Deep-Dive**: ✅ Completed
* **Batch 10 — Ghost Typer Playback & Web Export improvements**: ✅ Completed
* **Batch 11 — Project-Specific AI Contexts**: ✅ Completed
* **Batch 12 — Technical Debt & Cleanup**: ✅ Completed (v0.6.0 Release Prep)
* **Batch 13 — MCP Time-Machine Snapshots**: ✅ Completed
* **Batch 13p2 — Developer Memory Engine Documentation Overhaul**: ✅ Completed
* **Batch 13p3 — Documentation Updates & Refinement**: ✅ Completed
* **Batch 14 — SQLite FTS5 Integration**: ✅ Completed
* **Batch 15 — REM Sleep Context Consolidation**: ✅ Completed & Verified


