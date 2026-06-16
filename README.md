# TermStory — Your Personal Developer Memory Engine

[![PyPI version](https://img.shields.io/pypi/v/termstory.svg)](https://pypi.org/project/termstory/)
[![CI](https://github.com/bitflicker64/Termstory/actions/workflows/ci.yml/badge.svg)](https://github.com/bitflicker64/Termstory/actions/workflows/ci.yml)
[![Python Versions](https://img.shields.io/pypi/pyversions/termstory.svg)](https://pypi.org/project/termstory/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

> Parse your shell history. Recover your past. Understand your work.

TermStory turns your terminal history into a searchable, AI-narrated timeline of your development life. It groups shell commands into sessions, correlates Git commits, resolves project names, and renders everything into a high-density TUI dashboard — with a built-in forensic engine that can **recover the real dates of commands you typed before you even knew timestamps were missing**.

## Quick Start

### 1. Install
Install TermStory using pip:
```bash
pip install termstory
```

### 2. First Run
Run these commands to ingest history and view your timeline:
```bash
# Launch the interactive TUI Dashboard
termstory ui

# View your developer activity for today
termstory today

# Search across your history and session summaries
termstory search query
```

---

## Table of Contents

1. [Core Philosophy](#1-core-philosophy)
2. [Installation & Quick Start](#2-installation--quick-start)
3. [Project Layout](#3-project-layout)
4. [The Ingestion Pipeline](#4-the-ingestion-pipeline)
5. [Shell History Parsing](#5-shell-history-parsing)
6. [The Timestamp Detective (v0.2.9)](#6-the-timestamp-detective-v029)
7. [Project Resolution](#7-project-resolution)
8. [Git Commit Correlation](#8-git-commit-correlation)
9. [Database Schema](#9-database-schema)
10. [Privacy Sanitizer](#10-privacy-sanitizer)
11. [AI Client](#11-ai-client)
12. [TUI Dashboard](#12-tui-dashboard)
13. [AI Narrative Design](#13-ai-narrative-design)
14. [Extended Commands & Features](#14-extended-commands--features)
15. [CLI Reference](#15-cli-reference)
16. [Configuration](#16-configuration)
17. [Testing](#17-testing)
18. [Troubleshooting](#18-troubleshooting)

---

## 1. Core Philosophy

TermStory is **not** a tracking tool, a productivity auditor, or a corporate analytics dashboard. It is a **personal developer memory engine** built on three ideas:

- **Recognize, don't inspect.** The goal is instant recognition — *"Ah, that was the day I fought the Docker networking bug"* — not a wall of `cd` and `ls` entries. Noise commands are filtered automatically.
- **Density over decoration.** No rounded panels, no double borders, no empty margins. Clean column alignment, tight spacing, information-first. There is a strict ban on `rich.panel.Panel` in favor of dense text separators.
- **Screenshot-friendly.** Every view fits in one terminal screen and tells a complete, self-contained story.

---

## 2. Installation & Quick Start

**Requirements:** Python 3.9+, a terminal with `zsh` or `bash`.

```bash
pip install termstory
```

### Enable timestamps (zsh only — one time setup)

TermStory works best when your shell records timestamps. If you haven't done this already:

```bash
echo '\nsetopt EXTENDED_HISTORY\nsetopt HIST_STAMPS="yyyy-mm-dd"' >> ~/.zshrc
source ~/.zshrc
```

> **Already have old history without timestamps?** TermStory's Timestamp Detective (v0.2.9) will forensically recover real dates from your git log, filesystem metadata, and package manager artifacts automatically. You don't lose your past.

### First launch

```bash
termstory ui          # Interactive TUI dashboard (recommended)
termstory today       # Today's timeline in the terminal
termstory search auth # Search across all history
```

On first launch, TermStory detects whether you have timestamps enabled. If not, it offers to enable `EXTENDED_HISTORY` automatically. You can skip this and proceed anyway — the Timestamp Detective will handle your legacy history.

---

## 3. Project Layout

```
termstory/
├── setup.py                     # Package metadata
├── pyproject.toml               # Build config
├── README.md                    # This document
├── DATA_PRIVACY.md              # LLM data handling policy
├── termstory/
│   ├── __init__.py              # Version: 0.6.0
│   ├── __main__.py              # python3 -m termstory entry point
│   ├── cli.py                   # Typer CLI — all commands & ingestion entry point
│   ├── tui.py                   # Textual TUI dashboard & all widgets
│   ├── parser.py                # Shell history parsing engine
│   ├── timestamp_detective.py   # Forensic timestamp recovery engine
│   ├── session.py               # 30-minute session grouping
│   ├── project.py               # VCS root detection & project name resolution
│   ├── git_integration.py       # git log subprocess client & commit cleaner
│   ├── database.py              # SQLite layer (WAL, schema, queries, cache)
│   ├── date_utils.py            # Timezone & timestamp utilities
│   ├── sanitizer.py             # Local credential & PII redaction
│   ├── ai.py                    # Zero-dependency LLM client (urllib only)
│   ├── insights.py              # Focus score & pattern calculations
│   ├── models.py                # Command, Session, Project, Commit dataclasses
│   └── formatter.py            # CLI output layout & Rich styling
└── tests/
    ├── fixtures/
    │   └── sample_history.txt
    ├── test_parser.py
    ├── test_session.py
    ├── test_project.py
    ├── test_git_integration.py
    ├── test_database.py
    ├── test_database_queries.py
    ├── test_sanitizer.py
    ├── test_ai.py
    ├── test_tui.py
    ├── test_formatter_rich.py
    ├── test_insights.py
    ├── test_timestamp_detective.py
    └── test_integration.py
```

---

## 4. The Ingestion Pipeline

Every CLI command and every TUI launch runs this pipeline:

```
~/.zsh_history / ~/.bash_history
         │
         ▼
    parser.py  ──────────────────────────────────────────────────────────┐
    Parse raw history into Command objects.                               │
    Separate timestamped commands from legacy (no-timestamp) commands.   │
         │                                                                │
         ▼  (legacy commands only)                                        │
    timestamp_detective.py                                                │
    Phase A: Replay cd/pushd/popd → virtual CWD per command              │
    Phase B: 5 forensic detectors (git log, file stat, pkg mgr,          │
             docker, lockfiles) → real timestamps + Chain of Custody      │
    Phase C: Anchor Interpolation → linear fill between anchors          │
         │                                                                │
         └─────────────────────────────────────────────────────────────►─┤
         ▼                                                                │
    session.py                                                            │
    Group commands into sessions (30-minute idle threshold)              │
         │                                                                │
         ▼                                                                │
    project.py                                                            │
    Detect VCS root → extract project name from manifests → "Other"      │
         │                                                                │
         ▼                                                                │
    git_integration.py                                                    │
    Run git log for session timeframe → clean commit messages            │
         │                                                                │
         ▼                                                                │
    sanitizer.py                                                          │
    Redact credentials, IPs, tokens before any AI call                   │
         │                                                                │
         ▼                                                                │
    database.py  →  ~/.termstory/termstory.db (SQLite + WAL)            │
    Store sessions, commands, commits, recovery_source, AI cache         │
         │                                                                │
         ▼                                                                │
    tui.py / formatter.py                                                 │
    Render timeline, badges, AI summaries, Chain of Custody tooltips     │
```

---

## 5. Shell History Parsing

### Zsh — EXTENDED_HISTORY format

When `setopt EXTENDED_HISTORY` is enabled, Zsh writes:

```
: 1717500000:3;git commit -m "fix auth"
```

The parser extracts timestamp, duration, and command text with multiline support (trailing `\` continuation).

### Zsh — Hybrid / Frankenstein Mode

If you recently enabled `EXTENDED_HISTORY`, your `~/.zsh_history` contains a mix of timestamped and legacy lines. The parser handles this automatically:

- Timestamped lines are extracted normally.
- Legacy lines are collected separately.
- The oldest timestamped command's timestamp minus 60 seconds becomes the `anchor_time`.
- **The Timestamp Detective runs on all legacy lines** (see Section 6).
- **Timestamp Locking**: On subsequent runs, synthetic timestamps are looked up in the database and re-used to prevent legacy commands from shifting dates.

### Bash — HISTTIMEFORMAT
If `HISTTIMEFORMAT` is set, Bash writes `#<timestamp>` headers before each command. The parser associates each command with its preceding timestamp header. Without headers, timestamps are spaced 10 seconds apart backward from the file's `mtime`.

### Fish & PowerShell
Fish (`~/.local/share/fish/fish_history`) and PowerShell (`(Get-PSReadLineOption).HistorySavePath`) formats are natively supported. TermStory dynamically discovers active history file formats and reads timestamped blocks or falls back to legacy interpolation when headers are absent.

### Terminal Multiplexer Resilience
TermStory's parser intelligently strips injected `PROMPT_COMMAND` hooks and escape sequences left by multiplexers like Tmux, Zellij, and Kitty. Session boundaries natively embrace interleaved TTY sessions instead of artificially fracturing your timeline.

### Filtering

Commands older than 5 years or with future timestamps are silently dropped to prevent database pollution.

---

## 6. The Timestamp Detective

> The most significant feature in TermStory's history. If you've been using your terminal for years without `EXTENDED_HISTORY`, your shell history has no dates at all — every command appears to have happened "today". The Timestamp Detective reverse-engineers real timestamps by mining your git log, filesystem metadata, and package manager artifacts.

### The Problem

Without `EXTENDED_HISTORY`, a file like Vansh's 847-command history gets anchored to "today" via a 1-second step-back. His March commits, April npm installs, and May docker builds all collapse into one meaningless session labelled *today*.

### The Solution — Three Phases

#### Phase A — Virtual CWD Tracker

Before running any detector, the engine replays `cd`, `pushd`, and `popd` commands in sequence to compute the **virtual working directory** at every point in history. This is critical because git commit searches must be scoped to the correct repository — otherwise `git commit -m "fix typo"` could match a commit from the wrong project.

```
idx 0:  ls                     → cwd = "~"
idx 1:  cd ~/Projects/myapp    → cwd = "~/Projects/myapp"
idx 2:  npm install            → cwd = "~/Projects/myapp"
idx 3:  git commit -m "init"   → cwd = "~/Projects/myapp"  ← searches THIS repo only
idx 4:  cd ..                  → cwd = "~/Projects"
```

Supports: `cd -` (previous dir), relative `..` paths, `~` and `$HOME` expansion, `pushd`/`popd` stack.

#### Phase B — Five Forensic Detectors

Run in priority order. Each returns `(unix_timestamp, source_string)` or `None`.

**1. Git Commit Matcher** ⭐ Highest confidence

Extracts the message from `git commit -m "…"` and runs `git log --all` on the CWD-scoped repository. Fuzzy-matches the message using `difflib.SequenceMatcher` with a threshold of ≥ 0.85 after stripping conventional prefixes (`feat:`, `fix(scope):`) and emojis from both sides.

**Multi-Repo Collision Trap:** The CWD-derived repo is searched first. If Vansh has three repos with `git commit -m "fix typo"`, only the repo he was sitting in when he typed it gets matched. Fallback to other known project paths only if the local repo has no match.

**2. File Stat** 🟡 Medium confidence

Stats the artifact created by file-creating commands:

| Command | Target |
|---|---|
| `touch <file>` | `<file>` — `st_birthtime` (macOS) or `st_mtime` |
| `mkdir [-p] <dir>` | `<dir>` |
| `echo/printf/cat > <file>` | redirect target |
| `cp <src> <dst>` | `<dst>` |
| `git init [dir]` | `<dir>/.git` |
| `npm init [-y]` | `cwd/package.json` |
| `python -m venv <name>` | `<name>/bin/activate` |
| `cargo init [dir]` | `<dir>/Cargo.toml` |
| `go mod init` | `cwd/go.mod` |

`touch -t` (explicit timestamp set) is excluded. All paths are resolved against the virtual CWD.

**3. Package Manager Install Metadata** 🟡 Medium confidence

| Command | Artifact |
|---|---|
| `brew install <formula>` | `$(brew --prefix)/Cellar/<formula>` mtime |
| `pip install <pkg>` | pip dist-info directory mtime |
| `npm install` (local) | `package-lock.json` mtime |
| `npm install -g <pkg>` | global node_modules `/<pkg>` mtime |
| `cargo add/install <crate>` | `~/.cargo/registry/src/*/*crate*` mtime |
| `gem install <gem>` | `~/.gem/ruby/*/gems/<gem>-*` mtime |

Both `brew --prefix` and `npm root -g` subprocess calls are cached after the first call.

**4. Docker Image Inspector** 🟡 Medium confidence

For `docker build -t <tag>` commands, runs:
```bash
docker image inspect <tag> --format='{{.Created}}'
```
Parses the RFC3339 timestamp and returns the exact second the image was built.

**5. Venv / Lockfile Sentinels** 🟡 Low-medium confidence

| Command | Artifact |
|---|---|
| `bundle install` | `Gemfile.lock` mtime |
| `go mod tidy` / `go get` | `go.sum` mtime |
| `composer install` | `composer.lock` mtime |
| `poetry add/install` | `poetry.lock` mtime |
| `mix deps.get` | `mix.lock` mtime |
| `git clone <url> [dir]` | `<dir>/.git` birthtime |
| `ssh-keygen -f <file>` | `<file>` birthtime |

#### Phase C — Anchor Interpolation Engine ⭐ The Killer Feature

After Phase B, some commands have real timestamps (anchors) and others are still `None`. For each contiguous gap between two anchors, unresolved commands are distributed **linearly**:

```
t_i = t_a + (t_b − t_a) × (i − i_a) / (i_b − i_a)
```

**Example:**

```
[500] npm install express     → 🔍 ANCHOR  Tuesday 14:00  (package.json mtime)
[501] npm start               → 📐 INTERP  Tuesday 14:07  (between anchors)
[502] git commit -m "add express" → 🔍 ANCHOR Tuesday 14:15  (git log)
```

`npm start` is mathematically placed at 14:07 — the proportional moment between the two known events. Not a guess, not today — the most likely real time.

- **Prefix gap** (before first anchor): 1-second step-back from anchor.
- **Suffix gap** (after last anchor): 1-second step-forward from anchor.
- **No anchors at all**: original mtime step-back (same as v0.2.8 behaviour).

### Chain of Custody — The Trust Badge

Every command that the Detective recovered gets a `recovery_source` string persisted to the database. In the TUI, it renders as a badge below the command:

```
💻 Command Timeline:
  • 14:00:03  npm install express
      [🔍 stat: package.json mtime]
  • 14:07:31  npm start
      [🔍 Interpolated (between stat: package.json mtime → git log: myapp@a3f9b2c)]
  • 14:15:44  git commit -m "add express"
      [🔍 git log: myapp@a3f9b2c]
```

This turns *"how the hell did it know that?"* into a *"holy crap, this app is smart"* moment.

### TUI Session Labels

| Label | Meaning |
|---|---|
| `✨ npm init, express installed  (14:00 - 14:45)` | Real EXTENDED_HISTORY timestamp |
| `🔍 Recovered Archive (38 cmds • 14:00 - 14:45)` | Detective found anchors + interpolated |
| `📦 Legacy Archive (12 recovered cmds)` | Zero forensic evidence, fully synthetic |

---

## 7. Project Resolution

`project.py` maps a working directory to a human-readable project name using a two-pass strategy:

```
Working Directory: /home/dev/Projects/termstory/tests
         │
         ▼
Walk up directories looking for .git / .hg / .svn
         │
         ▼
Found root: /home/dev/Projects/termstory
         │
         ▼
Read build manifests:
  package.json → "name" field
  Cargo.toml   → [package] name
  setup.py     → name= argument
  pom.xml      → <artifactId>
         │
         ▼
Normalize: strip hyphens, fix casing
Result: "termstory"
```

**Fallback strictness:** If a directory is not inside a standard project root (`~/Projects/`, `~/src/`, etc.) and has no VCS markers, it maps to the user's home directory and is grouped under `"Other"`. This prevents `~/.ssh`, `~/Downloads`, or `/tmp` from polluting the project list.

**Symlink Protection:** TermStory safely traverses symlinks while preventing infinite recursive loops and stalling on stale network mounts (like NFS/SMB).

---

## 8. Git Commit Correlation

`git_integration.py` enriches sessions by fetching matching commits:

```bash
git -C <repo_root> log --all \
    --since="<session_start - 5min>" \
    --until="<session_end + 10min>" \
    --format="%H|%at|%s"
```

**Commit cleaning pipeline:**
- Strips conventional commit prefixes (`feat(scope):`, `fix:`, `chore:`)
- Removes Unicode emoji and `:shorthand:` tokens
- Drops merge commit messages and branch pointer refs
- Stores both raw and cleaned message for AI prompts

---

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

## 10. Privacy Sanitizer

All data passes through `sanitizer.py` **locally** before any AI call. Nothing sensitive ever leaves your machine.

### Session Blacklist

If any command in a session matches these patterns, the entire session is short-circuited to return `"Security/Authentication Operations"` — no commands are sent to the LLM at all:

```python
BLACKLIST_PATTERNS = [
    r'\bvault\b',
    r'\baws\s+configure\b',
    r'\bgh\s+auth\b',
    r'\bkubectl\s+.*?\bcreate\s+secret\b',
]
```

### Redaction Rules

| Type | Pattern | Replacement |
|---|---|---|
| Private keys | `-----BEGIN ... PRIVATE KEY-----` | `[REDACTED_PRIVATE_KEY]` |
| AWS keys | `AKIA[A-Z0-9]{16}` | `[REDACTED_AWS_KEY]` |
| Bearer tokens | `bearer <token>` | `Bearer [REDACTED_TOKEN]` |
| Flag values | `--password`, `--token`, `--api-key`, `-p` | `--password=[REDACTED]` |
| IPv4/IPv6 | standard address patterns | `[REDACTED_IP]` |
| Hostnames/FQDNs | `host.domain.tld` | `[REDACTED_HOST]` |
| Exports | `export KEY=value` | `export KEY=[REDACTED]` |

**File extension whitelist:** Paths ending in `.py`, `.json`, `.sh`, `.yml`, `.ts`, `.go`, etc. are never redacted even if they look like FQDNs — preserving filenames like `config.json` and `api.ts`.

---

## 11. AI Client

`ai.py` interfaces with any OpenAI-compatible LLM endpoint using **only Python's standard library** — no `requests`, no `openai-python`.

- **Transport:** `urllib.request.Request` with JSON payload
- **URL normalization:** Strips trailing slashes, auto-appends `/chat/completions`
- **Keyless mode:** Skips `Authorization: Bearer` header if API key is empty (Ollama compatibility)
- **Timeout:** 15 seconds to prevent blocking the TUI thread
- **Background execution:** All AI calls run in Textual `@work` async workers — UI never freezes

### Supported Providers

| Provider | Default model | Notes |
|---|---|---|
| **Groq** | `llama-3.1-8b-instant` | Fast, free tier available |
| **OpenAI** | `gpt-4o-mini` | Requires API key |
| **Ollama** | `llama3` | Fully local, no key needed |
| **Custom** | any | Any OpenAI-compatible endpoint |

---

## 12. TUI Dashboard

Launch with `termstory ui`.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  🔥 12 day streak  •  43 active days  •  127h 14m total                     │
│  Activity (Last 90 Days):  ░░▒▒░▓▓█▓▒░░▒▒▓▓█▓▒░░▒▓▓█▓▒░░▒▒▓▓█▓▒░░▒░░      │
├───────────────────────────────┬─────────────────────────────────────────────┤
│  June 2026                    │  📂 termstory  •  Tue Jun 03                │
│  ├─ Jun 07 (Sat)              │  ─────────────────────────────────────────  │
│  │  └─ termstory              │  [💻 Dev Log]                               │
│  │     ✨ v0.2.9 Timestamp… │  ├─ 🔨 Built: Timestamp Detective with      │
│  ├─ Jun 06 (Fri)              │         5 forensic detectors & interpolation│
│  │  ├─ termstory              │  ├─ 🔧 Flow: pytest 155 passed, twine      │
│  │  └─ Other                  │         upload, git push origin main        │
│  │     📦 Legacy Archive      │  └─ 🚀 Result: v0.2.9 published to PyPI    │
│  └─ Jun 05 (Thu)              │                                             │
│     └─ 🔍 Recovered Archive   │  💻 Command Timeline:                       │
│                               │  • 00:47:57  git commit -m "feat: v0.2.9…" │
│                               │      [🔍 git log: termstory@bbe9dfc]        │
│                               │  • 00:48:12  python3 -m pytest tests/       │
│                               │  • 00:49:03  twine upload dist/*            │
└───────────────────────────────┴─────────────────────────────────────────────┘
  ?:help  /:search  o:ai-config  c:copy  q:quit
```

### TUI Features

- **Responsive Resizing:** The layout gracefully reflows if you resize your terminal window.
- **Copy Feedback:** Visual "Copied" flash animations when using the `c` clipboard shortcut.

### Layout

- **StatsHeader** (top, full-width): streak count, active days, total duration, and a GitHub-style command-volume heatmap synced to the timeline's `--days` limit.
- **HistoryTree** (left 30%): collapsible Year → Month → Day → Project → Session tree. The root node shows an All-Time Wrapped dashboard. Month nodes show Monthly Wrapped.
- **DetailsCanvas** (right 70%): renders whatever the selected tree node points to — date chronicle, session details, wrapped views, or search results.

### Session Tree Labels

```
✨ npm init, express installed  (14:00 - 14:45)   ← real timestamp
🔍 Recovered Archive (38 cmds • 14:00 - 14:45)    ← Detective recovered
📦 Legacy Archive (12 recovered cmds)              ← fully synthetic
```

### Keyboard Shortcuts

| Key | Action |
|---|---|
| `?` | Toggle help overlay |
| `/` | Open search box (real-time filter of 90-day window) |
| `Enter` (in search) | Escape to all-time deep search |
| `Esc` | Close modal / clear search / restore timeline |
| `o` | Open AI provider configuration screen |
| `c` | Copy selected canvas content to OS clipboard |
| `j` / `k` / `↑` / `↓` | Navigate tree |
| `Ctrl+↓` / `Ctrl+↑` | Scroll canvas |
| `q` | Quit |

### Search Engine

Typing in `/` search filters the pre-loaded 90-day timeline in real-time (no DB hit). Pressing `Enter` escapes to an all-time database search across commands, commit messages, project names, and AI summaries. The tree root label changes to `🔍 Search Results: "query"`. Pressing `Esc` or clearing the box instantly restores the original timeline.

### Clipboard

`c` pipes text directly to `pbcopy` (macOS), `xclip`/`xsel`/`wl-copy` (Linux), or `clip` (Windows), bypassing OSC 52 terminal restrictions. ANSI escape codes are stripped before copy.

### Onboarding

On first launch with no config, an interactive onboarding screen offers `Ctrl+G` (Groq), `Ctrl+A` (OpenAI), `Ctrl+L` (Ollama), `Ctrl+C` (Custom), or `Ctrl+D` (disable AI). If your history lacks timestamps, a consent prompt offers to append `setopt EXTENDED_HISTORY` to `~/.zshrc`.

### Wrapped Views

Selecting the root **Timeline** node or a **Month** node shows a high-density wrapped dashboard:

- **Code churn matrix:** git insertions vs. deletions, net LOC growth
- **Time distribution:** hourly activity punch-card across the period
- **Project breakdown:** percentage distribution across all projects
- **Top commits & tooling:** most-used commands, editors, languages
- **AI Behavioral Audit:** a witty, witty roast of your patterns and productivity archetypes (*"The Midnight Alchemist"*, *"The Expansionist Architect"*) — regeneratable with `r`

---

## 13. AI Narrative Design

TermStory's prompts are designed to produce **high-density, CLI-styled developer logs** — not marketing prose.

### Session Summary Format

```
[💻 Dev Log]
├─ 🔨 Built: <short punchy action — what was built or coded>
├─ 🔧 Flow: <tools used, tests run, configs edited>
└─ 🚀 Result: <milestone shipped, fixed, or pushed>
```

or:

```
[🤖 Codebase Pulse]
• Hacked: <what was designed, refactored, or debugged>
• Tooling: <commands run, docker setups, libraries configured>
• Outcome: <what was verified, resolved, or shipped>
```

**Rules enforced in the prompt:**
- No paragraphs. No filler. No "ultimately the hard work paid off".
- Every line starts with a past-tense engineering verb: *wired up, refactored, debugged, spun up, stabilized, shipped*.
- Second-person narrative (`"You"`) for the Daily Chronicle view.

### Daily Chronicle (Date node)

When a date is selected in the TUI, a full "Story of You" daily narrative is generated — including an inferred breaks timeline, hourly activity punch-card, and second-person storytelling of the whole day.

---

## 14. Extended Commands & Features

In addition to the core TUI dashboard and daily timeline, TermStory features a suite of advanced CLI subcommands:

### 🧠 Ask (Natural Language Q&A)
Use semantic Q&A and search over your developer history. `termstory ask` retrieves relevant sessions using BM25 context retrieval combined with LLM processing to answer questions like:
- `termstory ask "What did I work on last Monday?"`
- `termstory ask "When did I fix the database deadlocks?"`

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
Export all or filtered sessions as JSON or CSV files to share or perform custom analysis.
- `termstory export --format json`
- `termstory export --format csv --output history.csv`

### 📊 Stats (Detailed Work Telemetry)
Compute and display high-density, CLI-native command category breakdown tables, tool usage, and terminal telemetry.
- `termstory stats`

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
termstory today --detailed   # All commands, no noise filtering, with timestamps
termstory today --compare    # Side-by-side with yesterday
termstory today --stats      # Command category frequency table
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

### Historical

```bash
termstory week               # Current week
termstory week --last        # Previous week
termstory month              # Current month
termstory month "May 2026"   # Specific month
termstory month --last       # Previous month
```

### Projects

```bash
termstory project <name>                       # 30-day deep dive
termstory project myapp --files                # Files edited, by frequency
termstory project myapp --stats                # Command category breakdown
termstory projects                             # All tracked projects
termstory projects --sort time                 # By total hours (default)
termstory projects --sort recent               # By last active date
termstory projects --sort name                 # Alphabetically
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
termstory export --project myapp --since 7               # Export last 7 days of "myapp" project
termstory export --since 2026-06-01                      # Export sessions since specific date
```

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

## 16. Configuration

Config lives at `~/.termstory/config.json`:

```json
{
    "ai_enabled": true,
    "active_provider": "groq",
    "request_timeout_seconds": 30,
    "has_seen_onboarding": true,
    "providers": {
        "groq": {
            "api_key": "gsk_...",
            "api_base_url": "https://api.groq.com/openai/v1",
            "model_name": "llama-3.1-8b-instant"
        },
        "openai": {
            "api_key": "sk-proj-...",
            "api_base_url": "https://api.openai.com/v1",
            "model_name": "gpt-4o-mini"
        },
        "ollama": {
            "api_key": "",
            "api_base_url": "http://localhost:11434/v1",
            "model_name": "llama3"
        },
        "custom": {
            "api_key": "",
            "api_base_url": "http://localhost:8080/v1",
            "model_name": "my-model"
        }
    }
}
```

All settings are editable via `termstory config set <dot.path> <value>`.

Key configuration parameters:
- `ai_enabled` (bool): Toggle AI summaries on/off.
- `active_provider` (string): Set to `"groq"`, `"openai"`, `"ollama"`, `"custom"`, or `"disabled"`.
- `request_timeout_seconds` (int): HTTP request timeout (in seconds) for LLM API calls. Defaults to `30`.
- `providers.<name>.<param>`: Provider-specific endpoints, API keys, and model names.

---

## 17. Testing

```bash
python3 -m pytest tests/ -v
```

**v0.6.0 results: 377 passed, 0 failures**

| Test File | What it covers |
|---|---|
| `test_parser.py` | Zsh format extraction, hybrid mode, bash fill algorithm, multiline commands |
| `test_timestamp_detective.py` | Virtual CWD tracker, all 5 detectors, anchor interpolation, full pipeline |
| `test_session.py` | 30-minute grouping, boundary detection, duration calculation |
| `test_project.py` | VCS root climbing, manifest parsing, "Other" fallback, home dir strictness |
| `test_git_integration.py` | `git log` subprocess mocking, commit message cleaning |
| `test_database.py` / `test_database_queries.py` | WAL config, schema init, CRUD, dedup migration, date range queries |
| `test_sanitizer.py` | Blacklist short-circuit, credential redaction, FQDN exclusion |
| `test_ai.py` / `test_ai_error_surfacing.py` | urllib payload construction, keyless mode, timeout, prompt templates, error surfacing |
| `test_tui.py` | Textual widget lifecycle, onboarding flow, search, wrapped view, clipboard |
| `test_formatter_rich.py` | CLI output layout and Rich markup |
| `test_integration.py` | End-to-end ingestion → DB → render |
| `test_archive.py` / `test_backup.py` | Database archiving, backup, and restore verification |
| `test_predict.py` / `test_search.py` | Pre-cognitive workspace prediction and advanced hybrid search filtering |
| `test_git_blame_anger_fortune_teller.py` | Anger translation and predictive bug forecasting logic |
| `test_mcp_snapshot.py` | MCP time-machine workspace snapshots |
| `test_expert_concurrency.py` / `test_stress.py` | Heavy concurrent database reads/writes, slowloris TUI simulated tests |

---

## 18. Troubleshooting

### Reset everything

```bash
rm -rf ~/.termstory/
termstory ui   # Fresh start, re-runs onboarding
```

### Force a re-ingest

```bash
termstory today --detailed
```

Reads the history file fresh and updates the database.

### Enable EXTENDED_HISTORY manually

```bash
echo '\nsetopt EXTENDED_HISTORY' >> ~/.zshrc
source ~/.zshrc
```

Future commands will get real timestamps. The Timestamp Detective handles everything you typed before enabling this.

### Local AI (Ollama)

```bash
ollama pull llama3
ollama run llama3
termstory ui   # Press 'o' → Ctrl+L to select Ollama
```

### My old history all shows up as "today"

This is expected if you never had `EXTENDED_HISTORY`. The Timestamp Detective will recover what it can using git commits, file stat, and package manager artifacts. Commands it can't place with evidence are grouped in `📦 Legacy Archive` or `🔍 Recovered Archive` in the TUI.

### The detective didn't recover my history

The Detective needs forensic artifacts — git repos, installed packages, or created files — that still exist on disk. If you've wiped your machine since typing those commands, the artifacts are gone and full recovery isn't possible. Enable `EXTENDED_HISTORY` now so this doesn't happen going forward.

---

## License

MIT © TermStory Contributors

**GitHub:** https://github.com/bitflicker64/Termstory  
**PyPI:** https://pypi.org/project/termstory/

### 🕰️ Legacy Archive & Missing Timestamps
TermStory shines brightest when your shell logs timestamps (e.g., `setopt EXTENDED_HISTORY` in Zsh). However, if TermStory encounters ancient history lacking timestamps, it engages the **Legacy Interpolation Engine**:
- **Burst Clustering**: Commands are grouped into chunks that preserve coherent terminal sessions.
- **Circadian Snapping**: Synthetic dates are mathematically constrained to standard working hours on weekdays, ensuring your timeline looks realistic.
- **Metric Exclusion**: Synthetic `[Legacy Archive]` commands are deliberately excluded from your Activity Heatmap and Streaks, so your "Perfect Coding Streaks" remain 100% authentic.

