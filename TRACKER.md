# Project Tracker

## Version
* **Current Version**: `0.5.0` (released on June 14, 2026)
* **Latest Release Details**:
  * **Advanced Search Subcommand (`termstory search`)**: Added multi-filter capability to search sessions, commands, and commits by date range (`--since`, `--until`), project (`--project`), and tags (`--tag`/`-t`), using the `termstory/search.py` module.
  * **Detailed Command Documentation**: Expanded `README.md` with dedicated documentation sections explaining the inner workings, parameters, and examples for all advanced CLI subcommands (`ask`, `predict`, `replay`, `insights`, `web`, `export`, `stats`, `tags`).
  * **Roadmap Updates**: Shifted completed technical milestones (SQLite FTS5, Concurrency tests, agy, predict, replay, CI, search) into the "Shipped" section in `ROADMAP.md`.
  * **Flaky Slowloris Tests**: Modified `tests/stress/test_slowloris.py` and `tests/stress/test_slowloris_tui.py` to bind to dynamic, OS-allocated ports (via port `0`), resolving socket conflicts and "Address already in use" OS errors.

## Completed
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
* **Gamification & UI Polish (Batches 5–7)**:
  * **Anger Translator**: Converts frustrated commands into humorous logs.
  * **Predictive Bug Fortune Teller**: Forecasts likely code failures based on command patterns.
  * **RPG Class Assigner**: Profiles developers into RPG classes (e.g., `Regex Sorcerer`, `Docker Demolitionist`) with ASCII crests.
  * **Vampire Coder Index**: Tracks late-night terminal density (12:00 AM – 5:00 AM).
  * **Project Necromancer Score**: Measures resurrection rate of stale repositories (6+ months old).
  * **Rage-Quit Signatures**: Analyzes command sequences directly preceding long periods of inactivity.
* **Legacy History Interpolation (Timestamp Detective)**:
  * **Session-Preserving Burst Clustering**: Groups un-timestamped commands into chunks of 20 spaced 10 seconds apart.
  * **Circadian Monotonic Snapping**: Snaps synthetic timestamps to 9 AM - 6 PM weekday window, forcing weekend ones to Friday afternoon.
  * **30-Day Metric Buffer**: Prevents legacy commands from polluting recent timelines by placing them 30+ days prior to history `mtime`.
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
* **Project-Specific AI Contexts (Partially Implemented)**:
  * The database schema (`project_context` column) and data structure (`project_context` field) exist.
  * *Active Work*: Wiring `project_context` from database to AI prompts in `ai.py`. Creating configuration CLI subcommand and TUI options.
* **Batch 4 Git Workflow Resolution**:
  * Completing the final Git merge and branch cleanup (`feat/batch-4-v4`).

## Planned
* **Batch 8: Cyberpunk TUI/UX Polish** (Branch: `feat/batch-8-cyberpunk-tui-animations`):
  * **The Matrix Defrag**: Cascading falling green characters animation in TUI during log ingestion.
  * **Heatmap Pulse / Glitch**: Hover CSS animations on heatmap days and streak milestone glitch animations in TUI stats header.
  * **Ghost Typer Playback**: Interactive chronicle playback when pressing `p` on a timeline date or session in TUI.
* **Technical Debt & Cleanup**:
  * **Subcommand Alignment**: Update `README.md` and CLI help descriptions to unify documentation for recent subcommands (`rpg-class`, `vampire-index`, `necromancer`, `rage-quit`, `profile`, `fortune-teller`, `anger-translator`).
  * **Repository Cleanup**: Check off completed batches in `PLAN.md` and `TASKS.md`.

## Backlog
* **Long-Term R&D Concepts**:
  * **"REM Sleep" Context Consolidation**: Background processing of command clusters during idle periods.
  * **Model Context Protocol (MCP) Time-Machine Snapshots**: Capturing external workspace context (IDE state, browser tabs).
  * **Semantic Deep-Dive via Local RAG**: Zero-keyword query searching via locally generated command/commit embeddings.
  * **SQLite FTS5 Integration**: Speeding up deep-history string matching and providing ranked search capabilities across sessions/commands/AI summaries.
  * **Concurrency Stress Tests & Massive History Simulations**: Hardening the test suite by synthesizing massive, multi-year history logs to simulate worst-case ingestion scenarios.

## Known Issues
* **Outstanding Bugs**: **None**. All 15 previously tracked architectural threats (including path resolution glitches, race conditions, environments/multiplexer edge-cases, and symlink loops) have been completely resolved and tested. There are no open bug tickets or failing tests in the suite.

## Batch Status
* **Batch 3 v4 — FTS5 + Stress + AI Contexts**: ✅ MERGED (PR #11)
* **Batch 4 v4 — Release + Profile + Refactor**: 🔄 In Progress (Git workflow, merge, and cleanup pending)
* **Batch 5 — AI-Driven Git Translation & Predictive Bug Fortunes**: ✅ Completed
* **Batch 6 — RPG Classes & Vampire Coder Metrics**: ✅ Completed
* **Batch 7 — Project Necromancy & Rage-Quit Signatures**: ✅ Completed
* **Batch 8 — Cyberpunk TUI/UX Polish**: ✅ MERGED (PR #19)
