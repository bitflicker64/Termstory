## 14. Extended Commands & Features

In addition to the core TUI dashboard and daily timeline, TermStory features a suite of advanced CLI subcommands:

### 🧠 Ask (Natural Language Q&A)
Use semantic Q&A and search over your developer history. `termstory ask` retrieves relevant sessions using BM25 context retrieval combined with LLM processing to answer questions like:
- `termstory ask "What did I work on last Monday?"`
- `termstory ask "When did I fix the database deadlocks?"`

Session commands and commit messages are sanitized through the local redaction engine before being sent to the LLM — no raw secrets leave your machine. See [Privacy Sanitizer](privacy.md) for details.

### 🔮 Predict (Pre-Cognitive Workspace)
The pre-cognitive workspace predicts what project or workspace you will work on next based on historical transition probabilities and time-of-day patterns.
- `termstory predict`
- `termstory predict --top 5`
- `termstory predict --json`
- `termstory predict --days 30`

### 🎮 Gamification & Personality Profiles
- **RPG Class Alter Ego (`termstory rpg-class`)**: Automatically profiles your developer style into daily classes (e.g. *Regex Sorcerer*, *Docker Demolitionist*) based on commands executed, with optional AI biography generation.
- **Vampire Coder Index (`termstory vampire-index`)**: Measures late-night work intensity by tracking commands and commits done between midnight and 5:00 AM.
- **Project Necromancer Score (`termstory necromancer`)**: Measures the resurrection frequency of stale projects that were dormant for 6+ months.
- **Rage-Quit Signatures (`termstory rage-quit`)**: Analyzes the final terminal command run right before 12h+ coding breaks to identify patterns in how sessions end.

### 💢 Anger Translator (`termstory anger-translator`)
Translates developer commits and preceding terminal error diagnostics/rebuild attempts into emotional roasts and real internal states.

### 🔮 Fortune Teller (`termstory fortune-teller`)
Scans late-night chaotic sessions (such as bypass-tests, frantic commit iterations, rapid edits) to generate witty Monday-morning bug fortunes and predictions.

### ⏱️ Database Profiler (`termstory profile`)
Profiles database query execution times and identifies N+1 read patterns to help keep the TermStory TUI/CLI extremely fast.

### 📹 Replay (Terminal Session Playback)
Replay the exact commands and delays of a past development session directly in your terminal like a movie.
- `termstory replay`
- `termstory replay <session_id>`
- `termstory replay --speed 2.0`
- `termstory replay --list`

### 💡 Insights (Executive Focus & Activity)
Calculate developer focus scores, time-of-day work distributions, and project focus metrics to highlight active days, total durations, and main achievements.
- `termstory insights`

### 🌐 Web (HTML Report Generator)
Generate and automatically open a beautiful, high-density HTML report of your TermStory history and AI chronicles in your web browser.
- `termstory web`

### 📤 Export (Structured Data Export)
Export all or filtered sessions as JSON, CSV, or Markdown to share or perform custom analysis.
- `termstory export --format json`
- `termstory export --format csv --output history.csv`
- `termstory export --format markdown`

### 📊 Stats (Detailed Work Telemetry)
Compute and display high-density, CLI-native command category breakdown tables, tool usage, and terminal telemetry.
- `termstory stats`
- `termstory stats --json`

`termstory stats --json` emits machine-readable JSON (instead of the Rich/human
dashboard) intended for CI/CD pipelines, dashboards, scripts, and other
machine-readable integrations. The output is pure JSON on stdout. The JSON includes:

- `total_sessions` — total session count (`int`)
- `total_commands` — total command count (`int`)
- `total_projects` — total project count (`int`)
- `time_range` — activity window:
  - `earliest` — ISO-8601 timestamp of earliest activity, or `null`
  - `latest` — ISO-8601 timestamp of latest activity, or `null`
- `projects` — per-project breakdown list where each entry has `name`, `id`,
  `path`, `commands_count`, `total_duration`, `sessions_count`, `first_seen`,
  and `last_seen`

Project names follow the same semantics as the human-readable output, i.e. the
empty/`General / No Project` bucket is reported as `Other`. Unlike the
human table, JSON entries are keyed by project identity (`id`), so two distinct
projects that happen to share a display name are listed as separate entries with
their own `id`/`path` and statistics rather than being merged. On an empty
database, `stats --json` reports zero totals, an empty `projects` list, and a
`time_range` of `{"earliest": null, "latest": null}`.

### 🏷️ Tags (Session Classification)
Auto-classify and tag sessions (`deploy`, `debug`, `setup`, `test`, `docs`) based on shell commands and git commit messages.
- `termstory tags`
- `termstory tags debug`
- `termstory tags --rebuild`

### 🗄️ Archive (`termstory archive`)
Archive older sessions, commands, commits, and summaries (older than N days) into a separate archive database file to keep the active database lean.
- `termstory archive --days 90`

### 💾 Backup & Restore (`termstory backup` / `termstory restore`)
Create timestamped database backups or restore your database from a backup file in case of corruption or data migration.
- `termstory backup`
- `termstory restore /path/to/backup.db`

### 📅 Timeline (`termstory timeline`)
Render a high-density ASCII visual activity chart and timeline of command distributions over the recent days.
- `termstory timeline`
- `termstory timeline --days 30`

### 📓 Notebook (`termstory notebook`)
Export your terminal sessions as a clean, chronological Markdown journal/notebook grouped by day, optionally filtered by project or date range.
- `termstory notebook`
- `termstory notebook --project myapp --since 7`

### ⏰ Reminders (`termstory remind`)
Schedule, list, and manage reminders linked to specific sessions (e.g. reminding yourself of a task in a few days).
- `termstory remind "follow up on bug fix" --days 2`
- `termstory remind --list`
- `termstory remind --complete 1`

### ⚙️ Reset (`termstory reset`)
Reset all TermStory state, clear the SQLite database, and wipe the configuration file back to clean defaults.
- `termstory reset`

### 🔍 Observability Relay (`termstory obs`)
Toggle observability settings (such as Nemo relay / DeepWiki settings) for Hermes via local `.env` and `config.yaml` updates.
- `termstory obs`

---

## 15. CLI Reference

### Daily

```bash
termstory                    # Today's timeline
termstory today              # Same as above
termstory today --compare    # Side-by-side with yesterday
```

### Search

```bash
termstory search <query>             # Chronological session search
termstory search docker
termstory search docker --project myapp
termstory search docker --since 2026-05-01
termstory search docker --detailed
termstory search docker --semantic   # Local hybrid semantic/RAG search
```

### Projects

```bash
termstory project <name>                       # 30-day deep dive
termstory project context <name> "description" # Set goals/context for a project
termstory project context <name> --show        # View project context description
```

### Insights & Metrics

```bash
termstory insights           # Focus scores, time-of-day distribution, project focus
termstory rpg-class          # Assigner for daily RPG developer class with biography
termstory vampire-index      # Late-night coding intensity analytics
termstory necromancer        # Dormant project reactivation metrics
termstory rage-quit          # Final commands run prior to 12h+ inactivity periods
```

### Diagnostics & Tuning

```bash
termstory profile            # Profile database query latency (defaults to limit 10)
termstory profile --limit 20 # Profile top 20 queries and identify N+1 paths
termstory fortune-teller     # Detect chaotic sessions & predict Monday-morning bugs
termstory anger-translator   # Translate recent commits/errors into emotional states
```

### Stats

```bash
termstory stats              # Detailed, high-density work statistics and telemetry
termstory stats --json       # Output session statistics as machine-readable JSON
```

### Ask

```bash
termstory ask "What did I work on last Monday?"  # Natural language queries using BM25 context retrieval
termstory ask "When did I fix the database deadlocks?"
```

### Predict

```bash
termstory predict            # Pre-Cognitive Workspace: predict what you will work on next
termstory predict --top 5    # Show top 5 predicted projects
termstory predict --json     # Output predictions as machine-readable JSON
termstory predict --days 30  # Analyze specific number of days of history
```

### Replay

```bash
termstory replay             # Replay the most recent terminal session
termstory replay 42          # Replay session #42
termstory replay --speed 2.0 # Fast-forward (2.0x playback speed)
termstory replay --speed 0.5 # Slow-motion playback (0.5x speed)
termstory replay --list      # List recent sessions to choose from
```

### Web

```bash
termstory web                # Generate and open a beautiful HTML report in your browser
```

### Export

```bash
termstory export --format json                           # Export all sessions as JSON to stdout
termstory export --format csv --output ~/history.csv      # Export as CSV to a file
termstory export --format markdown                       # Export as Markdown to stdout
termstory export --format markdown -o sessions.md         # Export as Markdown to a file
termstory export --project myapp --since 7               # Export last 7 days of "myapp" project
termstory export --since 2026-06-01                      # Export sessions since specific date
termstory export --since 2026-06-01 --until 2026-06-30   # Export a specific date range
termstory export --since 7d                              # Export last 7 days
termstory export --since 1w --until yesterday            # Export from 1 week ago through yesterday
```

The `--since` option defines the lower bound, and `--until` defines the inclusive
upper bound of the exported range (both applied to each session's start time).
`--until` is optional and, when given a date only (e.g. `2026-06-10`), includes
the entire specified day. Relative expressions such as `7d`, `1w`, and
`yesterday` are also supported.

### Tags

```bash
termstory tags               # View summary of session counts and durations per tag
termstory tags debug         # List recent sessions tagged with "debug"
termstory tags test --limit 10 # List top 10 sessions with "test" tag
termstory tags --rebuild     # Force rebuild/re-evaluate tag classification for all sessions
```

### TUI

```bash
termstory ui                 # Full dashboard, last 90 days
termstory ui --days 30       # Limit to last 30 days
termstory ui --all           # All history
```

### Config

```bash
termstory config list
termstory config get active_provider
termstory config set active_provider groq
termstory config set providers.groq.api_key gsk_...
```

### Date override

```bash
termstory 2026-05-15                 # Specific date
termstory --date 2026-05-15 week     # Week containing that date
```

### Database, Archiving & Maintenance

```bash
termstory optimize                   # Vacuum SQLite database and rebuild indexes
termstory archive                    # Archive sessions older than 90 days
termstory archive --days 30          # Archive sessions older than 30 days
termstory backup                     # Create a timestamped backup of the database
termstory restore /path/to/backup.db # Restore database from a backup file
termstory reset                      # Reset all TermStory state and database
```

### Timeline & Notebook

```bash
termstory timeline                   # Render ASCII activity timeline over 30 days
termstory timeline --days 60         # Render ASCII timeline over 60 days
termstory notebook                   # Export history sessions as Markdown journal
termstory notebook --project myapp   # Export journal for specific project
```

### Reminders

```bash
termstory remind "refactor auth"     # Add a reminder
termstory remind --list              # List active/pending reminders
termstory remind --complete 2        # Mark reminder #2 as completed
```

### Observability

```bash
termstory obs                        # Toggle DeepWiki observability relay for Hermes
```

---
