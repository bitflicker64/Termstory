# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.6.0] - 2026-06-17
### Added
- **RPG Classes Alter Ego (`termstory rpg-class`)**: Assigns and displays daily RPG class alter egos (e.g., *Regex Sorcerer*, *Docker Demolitionist*) based on shell history command distribution, including a custom LLM-generated biography.
- **Vampire Coder Index (`termstory vampire-index`)**: Analyzes late-night coding intensity by tracking terminal density and percentage of commands executed between midnight and 5:00 AM.
- **Project Necromancer Score (`termstory necromancer`)**: Measures project resurrection rates for dormant projects that sudden exhibit active sessions after 6+ months of inactivity.
- **Rage-Quit Signatures (`termstory rage-quit`)**: Automatically identifies final commands executed in terminal sessions directly preceding long periods of inactivity (12+ hours).
- **Interactive Project Context (`termstory project context`)**: Allows setting, displaying, and managing goals and context descriptions for specific workspaces, and feeds this context directly into AI summaries.
- **Semantic / RAG Search (`termstory search --semantic`)**: Integrates local hybrid BM25 and semantic search using LLM context mapping to query shell history.
- **Pre-Cognitive Predict Command (`termstory predict`)**: Enhanced prediction model with `--json` outputs and variable history `--days` filtering to forecast upcoming workspace transitions.
- **Performance Profiling (`termstory profile`)**: Tracks database execution latency and identifies N+1 queries to prevent performance bottlenecks.
- **TUI Cyberpunk Aesthetics & Playbacks**: Custom Matrix-style falling code defrag animation on log ingestion, heatmap hover CSS pulses, streak glitches, and interactive ghost typewriter playbacks.

### Changed
- **Version Bump**: Promoted version to `0.6.0` across `__init__.py`, `setup.py`, and `pyproject.toml`.

### Fixed
- **Onboarding Timestamps Consent**: Safe detection of missing Zsh timestamps with automated onboarding to enable `EXTENDED_HISTORY`.

## [0.5.0] - 2026-06-14
### Added
- **Advanced Search Subcommand (`termstory search`)**: Added multi-filter capability to search sessions, commands, and commits by date range (`--since`, `--until`), project (`--project`), and tags (`--tag`/`-t`), using the newly introduced `termstory/search.py` module.
- **Detailed Command Documentation**: Expanded `README.md` with dedicated documentation sections explaining the inner workings, parameters, and examples for all advanced CLI subcommands (`ask`, `predict`, `replay`, `insights`, `web`, `export`, `stats`, `tags`).
- **Roadmap Updates**: Shifted completed technical milestones (SQLite FTS5, Concurrency tests, agy, predict, replay, CI, search) into the completed milestones in `TRACKER.md`.

### Changed
- **Version Bump**: Promoted version to `0.5.0` across `__init__.py`, `setup.py`, and `pyproject.toml`.

### Fixed
- **Flaky Slowloris Tests**: Modified `tests/stress/test_slowloris.py` and `tests/stress/test_slowloris_tui.py` to bind to dynamic, OS-allocated ports (via port `0`), resolving socket conflicts and "Address already in use" OS errors.

## [0.4.0] - 2026-06-14
### Added
- **`termstory agy` subcommand**: One-shot bridge to `agy -p` for instant AI pair-programming sessions; gracefully errors if `agy` is not on PATH.
- **TUI status bar version + last ingestion**: `StatsHeader` now shows the running version (e.g. `v0.4.0`) and the timestamp of the most recently synced session.
- **GitHub Actions CI pipeline**: Automated `pytest` across Python 3.9–3.12 with ruff lint check on every push/PR via `.github/workflows/ci.yml`.
- **Performance regression tests** (`tests/test_performance.py`): Bulk 500-command ingestion must complete under 5 s; session loading is guarded against N+1 query patterns.
- **E2E integration tests** (`tests/test_integration.py`): Three end-to-end scenarios covering search, project-name queries, and multi-session detection from long idle gaps.
- **README badges**: PyPI version, CI status, Python versions, and MIT license shields added to the top of `README.md`.
- **ROADMAP v0.4.x / v0.5.x sections**: Concrete near-term milestones now tracked alongside the long-term research items.

### Changed
- **`__version__`** bumped to `0.4.0` across `__init__.py`, `pyproject.toml`, and `setup.py`.

### Fixed
- **Skipped test** in `test_timestamp_detective.py`: `test_fuzzy_match_above_threshold` no longer uses a conditional `skipTest`; the assertion is now unconditional since the SequenceMatcher ratio is deterministic (≈ 0.923).

## [0.2.14] - 2026-06-08
### Added
- **Thread Starvation Guards**: Implemented two-factor guards (`exclusive=True` on `@work` and wall-clock timeouts) to prevent thread pool exhaustion from UI requests and hung LLM sockets.
- **Corrupt DB Fallback**: `safe_init_db` now automatically rotates corrupted SQLite databases to a `.bak` file and transparently reinitializes, preventing app lockups.
- **Background Async Operations**: Offloaded all heavy UI rendering and API operations to Textual's background `@work(thread=True)` threads to eliminate main-thread freezes.

### Fixed
- **SQLite Upgrade Deadlocks**: Eliminated database locking during concurrent reads/writes via explicit `BEGIN IMMEDIATE` transactions and `INSERT OR IGNORE` statements. Also increased connection timeout to 30.0s.
- **Schema Integrity**: Fixed project path collisions by migrating the `UNIQUE` SQLite constraint in the `projects` table from `name` to `path`.
- **TUI UI Crash**: Fixed `TypeError` in Python 3.14 by safely casting time variables to integers when rendering the timeline tree.
- **UI Aesthetic Discipline**: Removed all usages of `rich.panel.Panel` in favor of dense text separators to maintain the flat, screenshot-friendly design philosophy.

## [0.2.13] - 2026-06-08
### Added
- **Session-Preserving Burst Clustering**: Legacy shell history lacking timestamps is now grouped into 20-command chunks spaced realistically to preserve TermStory's 30-minute session grouping architecture.
- **Circadian Snapping**: Synthetic legacy chunks are snapped to working hours (9 AM - 6 PM) and strictly backwards to Friday if falling on a weekend, maintaining realistic timeline metrics.
- **30-Day Buffer Bounds**: A strict 30-day buffer isolates synthetic legacy history from `termstory today` and recent active timelines, preventing 5-year-old commands from leaking into the present.
- **Metric Exclusions**: Legacy commands (`is_legacy=True`) are now excluded from the GitHub-style Activity Heatmap, "Current Streak" calculations, and `termstory insights` active day metrics to prevent artificial inflation.
- **Legacy Badging**: Synthetic chunks are now explicitly labeled with `[Legacy Archive]` in all TUI and CLI outputs.
- **Shell Support**: Added Fish and PowerShell history format parsing.
- **Maintenance Command**: Added `termstory optimize` command to vacuum the SQLite database and maintain index performance.
- **TUI Visuals**: Added UI resizing responsiveness and "Copied" flash visual feedback upon clipboard copy.
- **Symlink Protection**: Added cyclic symlink detection and network path (NFS/SMB) escape protections in the project discovery engine.
- **Multiplexer Support**: Upgraded session tracking to gracefully embrace interleaved TTY sessions, preventing Tmux/Zellij pane splits from fracturing sessions.
- **Test Coverage**: Increased core test coverage to 75%, targeting `config`, `cli`, and `sanitizer` edge cases.



## [0.2.12] - 2026-06-08

### Added
- `test_parse_zsh_history_legacy_spread` test case to validate large legacy history distributions.

### Fixed
- Legacy history cramming bug where naive 1-second fallback stepping collapsed thousands of commands into a single calendar day; replaced with robust proportional spread interpolation guaranteeing at least a 1-day span.
- Suffix bound interpolation to proportionally spread commands forward from the last known anchor instead of jumping to the present time.
- Five-year silent filter pruning edge-case: fallback timestamp subtraction is now strictly bounded by the 5-year history cutoff to ensure older legacy commands are never accidentally dropped.
- Redundant `git log` subprocess calls in `TimestampDetective` by reusing the internal git log cache for oldest repo anchor lookups.
- Duplicate configuration insertions in `~/.zshrc` when saving the `EXTENDED_HISTORY` onboarding prompt setting.
- Terminal state corruption on abnormal exits by ensuring the TUI properly cleans up terminal settings.

## [0.2.11] - 2026-06-07

### Added
- One-time console reminder on exit when the TUI is closed and the AI provider is disabled.
- Automatic local Git project path scanning (`discover_project_paths`) in the ingestion pipeline to feed into the `TimestampDetective` for legacy command reconstruction.
- Thread-safe AI error tracking in thread-local storage (`_local_ai_state`) to support concurrent background workers.
- Specialized JSON payload parsing for LLM API errors (e.g. Groq, OpenAI) to extract clean error messages.
- Warning toast notifications displaying detailed thread-local error messages when AI generation fails.
- Configurable git log search timeout (`timeout` parameter in `get_project_commits`), allowing up to 30 seconds for deep history queries while keeping the default fast path at 10 seconds.
- Whitespace normalization on HTTPError message displays to format nicely in TUI toast notifications.
- Lazy/conditional Git repository discovery that runs only if legacy (timestamp-less) history commands are detected in Zsh history files.
- Safe `project_paths` callable resolution inside `parser.py` using try-except wrapping to prevent file permission/read errors from crashing the history parser.
- Fail-fast mechanism for bulk auto-summarization to stop immediately on the first failure, avoiding consecutive error toast spams and redundant network calls.
- Tracked successes and added a warning toast displaying the exact number of successful generations (e.g. `Bulk auto-summarization stopped. Succeeded: 0/2.`).

### Fixed
- Repeated onboarding prompt by gating the `EXTENDED_HISTORY` timestamp-consent prompt behind a `has_seen_timestamp_prompt` config flag.
- Default input response handling in CLI prompts: pressing Enter defaults to `"y"` to match standard command line conventions.
- Prompt suppression logic: the prompt-seen flag is only saved after the shell config file append operation succeeds, ensuring it will retry if the file write fails.
- Deferred configuration loading in `cli.py` to prevent unnecessary disk I/O on normal startup.
- Dynamic git commit ingestion timeframe to fetch commits starting from the oldest parsed command's timestamp, correctly linking commits to recovered legacy commands.
- Empty HTTPError response body handling: falls back to `e.reason` instead of preserving blank messages.

---

## [0.2.10] - 2026-06-07

### Added
- Configurable AI token limit (`max_tokens`) and request timeout (`request_timeout_seconds`) settings.
- macOS `install.log` timestamp anchors in `TimestampDetective` to correlate package install times.

### Fixed
- Configuration value type conversion: values are now cast to target types (like bool or int) on CLI set commands to prevent type errors.
- macOS syslog offset parsing: corrected regex matching for timezone offsets (e.g., `-07`) to standard ISO format (`-07:00`) for python's `datetime.fromisoformat`.
- macOS syslog package filter: enforced strict package name matching in log inspection.
- AI timeout parameter override propagation.

---

## [0.2.9] - 2026-06-06

### Added
- **Timestamp Detective**: Forensic pipeline for recovering real timestamps for legacy history commands by fuzzy-matching git history, file stats, package manager installs, and docker images.
- Hybrid parser support: processes mixed/hybrid Zsh files containing both legacy and timestamped lines.
- Database-driven timestamp locking: sequentially locks in synthetic timestamps to prevent history shifting.
- Bulletproof UTF-8 encoding fallback on legacy archive files.
- Interactive `EXTENDED_HISTORY` onboarding prompt for Zsh/Bash users.
