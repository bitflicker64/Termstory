# Architecture Threat Tracker

### 1. Time-Traveling History (NTP Jumps & Suspend Chaos)
**Severity**: High
**Status**: Resolved
**Impacted Component**: `Session Builder` (`session.py`) & `Legacy History Interpolation` (`parser.py`)
**Description**: System sleep/suspend cycles or NTP clock synchronizations can cause sudden massive jumps in timestamps. In `session.py`, this causes artificial gaps (`time_gap > gap_threshold`) that fragment single sessions into multiple disconnected sessions. In `parser.py`, negative or massive jumps corrupt the monotonic chunk clustering and circadian snapping logic, leading to overlapping or incorrectly placed legacy sessions.
**Recommended Remediation**: Introduce a clock-drift/suspend compensation heuristic. Track elapsed monotonic time or system uptime to detect sleep cycles, and cap the maximum `time_gap` to a reasonable working session limit if a suspend event is detected. For the legacy parser, enforce absolute bounds and sort/re-clamp after interpolation.

### 2. Multiplexer PROMPT_COMMAND Injection (Tmux/Zellij)
**Severity**: Medium
**Status**: Resolved
**Impacted Component**: `Parser Engine (Zsh Hybrid Mode)` (`parser.py`)
**Description**: Terminal multiplexers like Tmux or Zellij often inject custom hooks, escape sequences, or multi-line `PROMPT_COMMAND` executions directly into the shell history file. The current hybrid parser regex (`^:\s*(\d+):(\d+);(.*)$`) may fail to correctly consume these corrupted continuations, resulting in either silently dropped commands or bleeding of timestamped regions into the legacy fallback parser, scrambling the history timeline.
**Recommended Remediation**: Strengthen the `clean_command` logic and regex parsing to safely strip terminal multiplexer control characters and ignore known injected hook commands. Ensure the multiline continuation parser explicitly resets state if an invalid multiplexer boundary is detected instead of merging it into the current command block.

### 3. Polyglot Credential Leaks (Sanitizer Blindspots)
**Severity**: Critical
**Status**: Resolved
**Impacted Component**: `Privacy Sanitizer` (`sanitizer.py`)
**Description**: The local sanitization pipeline relies on hardcoded regexes (AWS keys, Slack tokens) and generic `--password` flags. It currently misses modern token formats (e.g., GitHub Fine-Grained tokens `github_pat_...`, Stripe keys `sk_live_...`, 1Password session tokens) and esoteric CLI flags. This leaves a severe blindspot where polyglot developer environments might leak sensitive credentials to external LLM providers during AI summary generation.
**Recommended Remediation**: Expand `BLACKLIST_PATTERNS` to cover modern token prefixes (GitHub PATs, Stripe, NPM tokens, OpenAI keys). Implement an entropy-based heuristic to catch high-entropy strings passed as arguments, and introduce a user-configurable `.termstoryignore` or custom redaction rules configuration to allow developers to block domain-specific secrets.

### 4. Half-Open Socket Zombies with Local LLMs
**Severity**: High
**Status**: Resolved
**Impacted Component**: `Zero-Dependency AI Client` (`ai.py`) & `TUI Responsiveness`
**Description**: Local LLMs (like Ollama) can sometimes accept a TCP connection but hang indefinitely during model loading or token generation. The `urllib.request.urlopen(req, timeout=timeout)` call applies the timeout primarily to the initial connection. If the local LLM server trickles bytes or leaves the socket half-open, the background threads will hang, leading to zombie threads that ultimately freeze or crash the TUI's async `@work` workers.
**Recommended Remediation**: Implement a strict wall-clock timeout wrapper using a threading Timer or the `socket.setdefaulttimeout()` at a lower level. Additionally, implement an application-level circuit breaker in `ai.py` that temporarily disables local LLM calls if multiple timeouts or half-open hangs are detected, protecting the TUI's responsiveness.

### 5. NFS/SMB Symlink Loops & Project Resolution Hangs
**Severity**: Medium
**Status**: Resolved
**Impacted Component**: `Project Resolver` (`project.py`)
**Description**: The `find_project_root` function performs blocking `os.listdir(current)` calls while walking up the directory tree to locate `.git` or `package.json` markers. If a user enters a stale NFS/SMB network mount or a directory containing complex symlink loops, `os.listdir` can block indefinitely at the OS level, completely hanging the TermStory ingestion and CLI commands.
**Recommended Remediation**: Wrap `os.listdir` calls with a strict timeout or use non-blocking os-level stats. Introduce a maximum depth counter to prevent infinite recursion in symlink cycles, and explicitly skip known network mount prefixes (e.g., `/mnt`, `/Volumes/smb`) unless explicitly whitelisted in the user's configuration.

### 6. Multiplexer Jumbling & Session Tracking Conflict
**Severity**: High
**Status**: Resolved
**Impacted Component**: `Session Builder` (`session.py`)
**Description**: The `create_sessions()` logic uses a Virtual CWD tracking mechanism (`update_cwd_and_get_root` and `is_project_fork = (next_project_root != session_project_root)`) to split sessions based on directory changes detected in `cd` commands. This actively fights against the "Embrace the session bleed" philosophy defined in the Phase 2 brainstorm. It will incorrectly split concurrent interleaved commands from different multiplexer panes into microscopic, fragmented sessions instead of relying on the AI to weave them together.
**Recommended Remediation**: Remove the Virtual CWD parsing and project-based session forking logic. Embrace session bleed by purely relying on the 30-minute time-gap threshold to group sessions, allowing interleaved commands to be naturally summarized by the LLM prompt.

### 7. History File Path Hardcoding (Shell Agnosticism Failure)
**Severity**: High
**Status**: Resolved
**Impacted Component**: `Configuration & Discovery` (`config.py`)
**Description**: The `get_history_files()` function hardcodes the list of fallback candidate paths (`~/.zsh_history`, `~/.bash_history`, etc.). It assumes standard macOS/Linux configurations. If a user runs Fish shell, PowerShell, or Windows, and `HISTFILE` is absent, the app will fail to ingest any history at all, rather than just "missing hidden commands" as stated in the Phase 2 brainstorm.
**Recommended Remediation**: Expand candidate paths to include standard locations for `fish` (e.g., `~/.local/share/fish/fish_history`), `pwsh` on Windows/macOS, and intelligently query the active `$SHELL` environment variable to determine the primary history file location dynamically.

### 8. TUI Degradation & Offline Mode Violation
**Severity**: Medium
**Status**: Resolved
**Impacted Component**: `DetailsCanvas` (`tui.py`) & `Formatter` (`formatter.py`)
**Description**: The codebase violates the strict "Density over decoration" and offline mode degradation rules from the Phase 2 brainstorm. First, `tui.py` fails to display the ASCII-styled `[ERR] AI summary unavailable. Displaying raw SQLite history.` log when offline or experiencing API timeouts; it silently falls back to heuristic strings instead. Second, `formatter.py` extensively imports and uses `rich.panel.Panel` with rounded boxes (`box=ROUNDED`), directly contradicting the explicit ban on panels in favor of dense text separators.
**Recommended Remediation**: Replace all instances of `rich.panel.Panel` in `formatter.py` with dense ASCII text separators or borderless groups. In `tui.py`'s `DetailsCanvas`, explicitly render the `[ERR] AI summary unavailable...` static message before displaying heuristic logs when the AI engine is disabled, offline, or returns a timeout error.

### 9. Windows Absolute Path & UNC Path Blindspots
**Severity**: High
**Status**: Resolved
**Impacted Component**: `Session Builder` (`session.py`) & `Project Resolver` (`project.py`)
**Description**: The CWD tracking logic for `cd` commands hardcodes Unix-style absolute path detection (`startswith("/")` or `startswith("~")`). On Windows, absolute paths like `C:\Projects` fail this check and are concatenated as relative paths (e.g., `C:\current\cwd\C:\Projects`), entirely breaking session project grouping. Furthermore, the `find_project_root` function skips Unix network mounts (`/mnt`, `/Volumes/smb`) but misses Windows UNC paths (`\\Server\Share`), meaning `os.listdir` can hang indefinitely on stale Windows network drives.
**Recommended Remediation**: Replace `startswith("/")` with `os.path.isabs(path_arg)` for cross-platform absolute path detection. Add Windows UNC path pattern matching (e.g., `startswith("\\\\")`) to the network mount skipping logic in `project.py` to prevent filesystem traversal hangs.

### 10. Environment Variable Expansion Failure in CD Tracking
**Severity**: Medium
**Status**: Resolved
**Impacted Component**: `Session Builder` (`session.py`) & `Project Resolver` (`project.py`)
**Description**: While the simulated CWD tracker expands `~` using `os.path.expanduser()`, it fails to expand environment variables using `os.path.expandvars()`. Navigating via variables (e.g., `cd $WORK_DIR`) causes the virtual file system to evaluate the literal string `$WORK_DIR` as a directory name. This derails the internal directory tree simulation, leading to orphaned subsequent commands and inaccurate `"Other"` project assignments.
**Recommended Remediation**: Apply `os.path.expandvars()` alongside `os.path.expanduser()` when resolving `path_arg` in both `session.py` (`update_cwd_and_get_root`) and `project.py` (Pass 1 cd-tracking).

### 11. Incomplete Multiplexer Boundary Resets (Bash/Zsh Parsers)
**Severity**: High
**Status**: Resolved
**Impacted Component**: `Parser Engine` (`parser.py`)
**Description**: Although `clean_command` successfully filters terminal multiplexer hooks, the multiline parser in `parse_zsh_history` fails to include `kitty +kitten` in its explicit state reset boundary check, potentially merging it into trailing backslash commands. More critically, `parse_bash_history` completely lacks any multiplexer injection boundary resets during its multiline parsing loop. If a `PROMPT_COMMAND` or multiplexer trace interrupts a Bash multiline command, it will be silently swallowed and merged, corrupting the command context and potentially exposing escape sequences to the LLM.
**Recommended Remediation**: Add `kitty +kitten` to the `lower_next` multiline boundary reset check in `parse_zsh_history`. Replicate the multiplexer boundary reset logic (`_zellij`, `tmux`, `prompt_command`) into the `while i < len(raw_lines):` multiline consumption loop in `parse_bash_history`.

### 12. Hardcoded Unix Path Separator in File Argument Extraction
**Severity**: Low
**Status**: Resolved
**Impacted Component**: `Project Resolver` (`project.py`)
**Description**: The `_extract_file_args` function attempts to rank project matches for unassigned sessions by counting directory depth via `score = len(farg.split("/"))`. This hardcoding of the Unix `/` separator means it will fail to split Windows paths (`\`), causing deep nested files on Windows to incorrectly receive a score of 1. This drastically weakens Strategy A for matching "Other" sessions to projects on Windows machines.
**Recommended Remediation**: Use `os.path.split()` iteratively or `len(re.split(r'[\\/]', farg))` to correctly calculate path depth independent of the host OS's native path separator.

### 13. Formatter Rich Table Box Decoration Violations
**Severity**: Medium
**Status**: Resolved
**Impacted Component**: `Formatter` (`formatter.py`)
**Description**: While Issue 8 documents `rich.panel.Panel` violations, the codebase also explicitly violates the "Density over decoration" philosophy in its table designs. `formatter.py` initializes `rich.table.Table` with `box=ROUNDED` (e.g., line 469). The philosophy explicitly demands "simple tables" and avoiding rounded panels or borders.
**Recommended Remediation**: Refactor all `Table` instantiations in `formatter.py` to use `box=SIMPLE`, `box=MINIMAL`, or `box=None` to align with the dense, terminal-native aesthetic.

### 14. TUI CSS Thick and Tall Border Usage
**Severity**: Low
**Status**: Resolved
**Impacted Component**: `TUI Stylesheet` (`tui.py`)
**Description**: The CSS stylesheet embedded in `tui.py` uses `border: thick $error;` for `ResetConfirmScreen` and `border: tall` for buttons and other components. Textual's `thick` border is a heavy, visually dense double-line equivalent, and `tall` creates nested visual boxes that contradict the directive to "Avoid ... double borders, or nested boxes."
**Recommended Remediation**: Downgrade these styles to `border: solid` or use simple background color contrast for focus/hover states instead of relying on heavy border lines.

### 15. Total AI Fallback Failure when Offline with Enabled Config
**Severity**: High
**Status**: Resolved
**Impacted Component**: `DetailsCanvas` (`tui.py`) & AI Fallback
**Description**: In `tui.py`, the offline degradation logic is flawed when AI is configured but the network is unavailable. If `ai_enabled` is true but the API request times out or fails (e.g., airplane mode), `generate_single_session_story` returns an error notification but the `DetailsCanvas` does not render the `get_session_memory_str(session)` heuristic fallback. The user is left with a blank summary section and just a "Failed to generate story" toast. The heuristic fallback currently only displays when the AI provider is explicitly set to "disabled".
**Recommended Remediation**: In `tui.py`'s session rendering logic (around line 1432), if a session lacks an `ai_summary` but generation has failed or timed out, gracefully render the local SQLite heuristic (`get_session_memory_str(session)`) alongside a retry button.
