"""
termstory/timestamp_detective.py
=================================
The Timestamp Detective — reverse-engineers real Unix timestamps for legacy shell
history commands that were recorded without EXTENDED_HISTORY timestamps.

Background
----------
When a user's ~/.zsh_history lacks the `: <timestamp>:<duration>;<cmd>` prefix
(i.e. they never had `setopt EXTENDED_HISTORY`), every command is timestamp-less.
TermStory's parser synthesises timestamps via a 1-second step-back algorithm, but all
commands end up anchored to "today", producing one massive meaningless session.

This module solves that by running a multi-phase forensic pipeline:

  Phase A — Virtual CWD Tracker
    Simulates the user's working directory at every point in history by tracking
    `cd`, `pushd`, and `popd` commands in sequence.  This is critical for Phase B
    because git commit searches must be scoped to the right repository.

  Phase B — Per-Command Detectors  (run in priority order)
    1. Git Commit Matcher  — extracts the commit message from `git commit -m "…"`
       commands, runs `git log --all` on the CWD-derived repo, and fuzzy-matches the
       message to find the real commit timestamp.  Uses SequenceMatcher ratio ≥ 0.85.
    2. Inline Date Strings — extracts embedded date strings from git commit --date,
       tar/mysqldump output filenames, and mv rename patterns.
    3. File Stat           — for commands that create a new file/directory
       (touch, mkdir, git init, npm init, venv, cargo init, go mod init, echo >, …),
       stats the resulting path using st_birthtime (macOS) or st_mtime.
    4. Package Manager     — checks Homebrew Cellar, pip dist-info, npm lock files,
       Cargo registry, and RubyGems install directories for the artifact's mtime.
    5. Docker Image        — runs `docker image inspect <tag> --format={{.Created}}`
       for `docker build -t <tag>` commands.
    5. Venv / Lockfiles    — stats Gemfile.lock, go.sum, composer.lock, poetry.lock,
       mix.lock, and venv/bin/activate for the corresponding installer commands.

  Phase C — Anchor Interpolation Engine
    After Phase B, some commands have a real ("anchor") timestamp and others are still
    None.  For each contiguous gap between two anchors, the unresolved commands are
    distributed *linearly* between the two anchor timestamps:

        t_i = t_a + (t_b − t_a) × (i − i_a) / (i_b − i_a)

    Commands before the first anchor are proportionally spread to the oldest known
    repo commit; commands after the last anchor are proportionally spread forward
    using a minimum 1-day window. If no anchors exist at all, the function
    returns all items unchanged (the parser's existing mtime-based step-back handles
    this fallback).

Usage
-----
    from termstory.timestamp_detective import TimestampDetective

    detective = TimestampDetective(
        search_root="~",
        project_paths=["/Users/me/Projects/myapp", …]
    )
    enriched = detective.resolve_all(legacy_items)
    # Each item in `enriched` gains:
    #   "detected_ts"     → int | None
    #   "detected_source" → str | None (human-readable attribution)
    #   "is_legacy_still" → bool (True = still synthetic even if interpolated)
"""

import os
import logging

logger = logging.getLogger(__name__)
import re
import glob
import getpass
import subprocess
import difflib
import site
from datetime import datetime
from typing import Dict, List, Optional, Tuple


class TimestampDetective:
    """
    Multi-phase forensic engine that recovers real timestamps for legacy shell
    commands by mining git history, filesystem metadata, and package manager artifacts.

    Attributes
    ----------
    search_root : str
        Absolute path used as the base for git repo discovery and relative path
        resolution (typically the user's home directory).
    project_paths : List[str]
        Known project directories passed in from the CLI ingestion pipeline.
        Used as fallback git repo search targets when the virtual CWD cannot
        be mapped to a valid git root.
    """

    def __init__(self, search_root: str, project_paths: Optional[List[str]] = None):
        self.search_root = os.path.expanduser(search_root)
        self.project_paths = [os.path.expanduser(p) for p in (project_paths or [])]

        # Cache: repo_path -> list of commit dicts  (avoid repeated git log calls)
        self._git_log_cache: Dict[str, List[Dict]] = {}

        # Lazily resolved package manager roots — populated on first use
        self._brew_prefix: Optional[str] = None   # e.g. /opt/homebrew
        self._npm_global_root: Optional[str] = None  # e.g. /usr/local/lib/node_modules

        # Snapshot of "now" used for timestamp validity guards throughout the run
        self.now = int(datetime.now().timestamp())
        self.five_years_ago = self.now - (5 * 365 * 24 * 60 * 60)

    # =========================================================================
    # Internal helpers
    # =========================================================================

    def _is_valid_timestamp(self, ts: int) -> bool:
        """
        Guard: reject timestamps that are impossibly old (> 5 years) or in the
        future.  Both cases indicate a bad stat/parse result that would pollute
        the timeline.
        """
        return self.five_years_ago < ts <= self.now

    def _get_file_timestamp(self, path: str) -> Optional[int]:
        """
        Return the best available creation timestamp for `path`.

        Preference order:
          1. st_birthtime  — exact creation time, macOS / BSD only.
          2. st_mtime      — last modification time, available on all platforms.

        Returns None if the path doesn't exist, the stat fails, or the resulting
        timestamp fails the validity guard.
        """
        try:
            if not os.path.exists(path):
                return None
            stat = os.stat(path)
            # st_birthtime is a macOS / BSD extension; fall back to st_mtime on Linux
            ts = int(getattr(stat, "st_birthtime", stat.st_mtime))
            if self._is_valid_timestamp(ts):
                return ts
            # If birthtime is invalid, also try mtime (they can differ on some FSes)
            ts_mtime = int(stat.st_mtime)
            if self._is_valid_timestamp(ts_mtime):
                return ts_mtime
        except Exception as e:
            logger.debug("timestamp_detective probe failed: %s", e)
        return None

    def _get_brew_prefix(self) -> Optional[str]:
        """
        Lazily resolve the Homebrew installation prefix via `brew --prefix`.
        Cached after the first call so subsequent detector runs are instant.
        Returns None if Homebrew is not installed or the call fails.
        """
        if self._brew_prefix is not None:
            # Empty string means "already tried, not available"
            return self._brew_prefix if self._brew_prefix else None
        try:
            res = subprocess.run(
                ["brew", "--prefix"],
                capture_output=True, text=True, timeout=5
            )
            self._brew_prefix = res.stdout.strip() if res.returncode == 0 else ""
        except Exception as e:
            logger.debug("timestamp_detective brew prefix probe failed: %s", e)
            self._brew_prefix = ""
        return self._brew_prefix or None

    def _get_npm_global_root(self) -> Optional[str]:
        """
        Lazily resolve the npm global node_modules directory via `npm root -g`.
        Cached after the first call.  Returns None if npm is not installed.
        """
        if self._npm_global_root is not None:
            return self._npm_global_root if self._npm_global_root else None
        try:
            res = subprocess.run(
                ["npm", "root", "-g"],
                capture_output=True, text=True, timeout=5
            )
            self._npm_global_root = res.stdout.strip() if res.returncode == 0 else ""
        except Exception as e:
            logger.debug("timestamp_detective npm root probe failed: %s", e)
            self._npm_global_root = ""
        return self._npm_global_root or None

    def _find_git_root(self, path: str) -> Optional[str]:
        """
        Walk upward from `path` until a `.git` directory is found, then return
        that directory as the repository root.  Stops after 10 levels to avoid
        climbing to the filesystem root on misconfigured machines.
        Returns None if no git root is found.
        """
        current = os.path.abspath(os.path.expanduser(path))
        for _ in range(10):
            if os.path.isdir(os.path.join(current, ".git")):
                return current
            parent = os.path.dirname(current)
            if parent == current:
                break  # reached filesystem root
            current = parent
        return None

    def _load_git_log(self, repo_path: str) -> List[Dict]:
        """
        Load and cache the full commit log for a git repository.

        Uses `git log --all` so commits on feature branches are also included
        (important: legacy history may reference work done on non-default branches).

        Format: `%H|%at|%s`
          %H  → full commit hash
          %at → author date as Unix timestamp
          %s  → commit subject line

        Returns an empty list if the repo is invalid or git is unavailable.
        """
        if repo_path in self._git_log_cache:
            return self._git_log_cache[repo_path]

        commits = []
        try:
            res = subprocess.run(
                ["git", "-C", repo_path, "log", "--all", "--format=%H|%at|%s"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=15  # large repos may take a moment
            )
            if res.returncode == 0:
                for line in res.stdout.strip().split("\n"):
                    line = line.strip()
                    if not line:
                        continue
                    parts = line.split("|", 2)
                    if len(parts) < 3:
                        continue
                    commit_hash, ts_str, subject = parts
                    try:
                        commits.append({
                            "hash": commit_hash,
                            "timestamp": int(ts_str),
                            "message": subject.strip()
                        })
                    except ValueError:
                        pass
        except Exception as e:
            logger.debug("timestamp_detective probe failed: %s", e)

        self._git_log_cache[repo_path] = commits
        return commits

    def _clean_for_match(self, msg: str) -> str:
        """
        Normalise a commit message for fuzzy comparison by stripping:
          - Conventional commit prefixes (feat:, fix(scope):, …)
          - Unicode emoji characters and :emoji: shorthand
          - Leading/trailing whitespace

        This mirrors the logic in git_integration.clean_commit_message but is
        applied to *both* the shell command string and the git log subject so
        that `git commit -m "feat: add login"` correctly matches "add login" in
        the commit log.
        """
        # Strip conventional commit prefix: feat(scope): / fix: / chore: etc.
        msg = re.sub(
            r'(?i)^(feat|fix|chore|docs|refactor|test|style|ci|perf|build)'
            r'(?:\([a-zA-Z0-9_\-\/]+\))?:\s*',
            '', msg
        )
        # Strip raw Unicode emoji (broad ranges)
        msg = re.sub(
            r'[\U0001f000-\U0001ffff\U00002600-\U000027bf]',
            '', msg, flags=re.UNICODE
        )
        # Strip :shorthand: emoji tokens
        msg = re.sub(r':[a-zA-Z0-9_\-+]+:', '', msg)
        return msg.strip().lower()

    def _extract_git_commit_message(self, command: str) -> Optional[str]:
        """
        Parse a `git commit` command string and return the message string.

        Handles the following variants:
          git commit -m "message"
          git commit -am "message"          (combined add+commit flag)
          git commit --message "message"
          git commit -m 'single quotes'
          git commit -m unquoted_single_word
        Returns None if no recognisable -m / --message flag is present.
        """
        patterns = [
            # Double-quoted: git commit [-flags] -m "msg"
            r'git\s+commit\s+[^\n]*?-[a-zA-Z]*m[=\s]*"([^"]+)"',
            # Single-quoted: git commit [-flags] -m 'msg'
            r"git\s+commit\s+[^\n]*?-[a-zA-Z]*m[=\s]*'([^']+)'",
            # Long flag: --message "msg"
            r'git\s+commit\s+.*?--message[=\s]+"([^"]+)"',
            r"git\s+commit\s+.*?--message[=\s]+'([^']+)'",
            # Unquoted single word (rare but valid)
            r"git\s+commit\s+[^\n]*?-[a-zA-Z]*m[=\s]+([^\s'\"]\S*)",
        ]
        for pat in patterns:
            m = re.search(pat, command)
            if m:
                return m.group(1)
        return None

    # =========================================================================
    # Phase A — Virtual CWD Tracker
    # =========================================================================

    def _build_virtual_cwd_map(self, legacy_items: List[dict]) -> Dict[int, str]:
        """
        Simulate the user's working directory at each command index by tracking
        shell directory-change builtins: `cd`, `pushd`, and `popd`.

        Why this matters
        ----------------
        Git commit detection must query the correct repository.  If Vansh has
        three projects that all contain commits with `git commit -m "fix typo"`,
        using the wrong repo would assign the wrong timestamp.  By knowing the
        virtual CWD when each `git commit` was typed, we scope the git log search
        to the repository that was actually active.

        Algorithm
        ---------
        • Starts at the user's home directory.
        • `cd <target>` : resolves the target against the current virtual CWD,
          handles `~`, `..`, `-` (previous dir), and absolute paths.
        • `pushd <target>` : pushes current onto a stack and changes to target.
        • `popd`           : pops and restores the previous directory.
        • Any other command leaves the CWD unchanged.

        Returns a dict mapping `{command_index: absolute_cwd_string}`.
        """
        home = self.search_root  # already expanded in __init__
        cwd_map: Dict[int, str] = {}
        current_cwd = home
        prev_cwd = home        # used by `cd -`
        cwd_stack: List[str] = []  # pushd/popd stack

        for idx, item in enumerate(legacy_items):
            cmd_full = item["command"].strip()
            
            subcommands = [s.strip() for s in re.split(r'&&|;|\|\|', cmd_full)]
            
            for cmd in subcommands:
                # ── cd ─────────────────────────────────────────────────────────
                if cmd == "cd" or cmd.startswith("cd ") or cmd.startswith("cd\t"):
                    import shlex
                    try:
                        tokens = shlex.split(cmd)
                    except Exception as e:
                        logger.debug("shlex split failed: %s", e)
                        tokens = cmd.split()
                    
                    target = ""
                    if len(tokens) > 1:
                        path_args = [t for t in tokens[1:] if not t.startswith('-') or t == '-']
                        if path_args:
                            target = path_args[0]
                    
                    new_cwd = current_cwd

                    if not target or target in ("~", "$HOME"):
                        new_cwd = home
                    elif target == "-":
                        new_cwd, prev_cwd = prev_cwd, current_cwd
                    elif target.startswith("/"):
                        new_cwd = os.path.normpath(os.path.expanduser(target))
                    else:
                        new_cwd = os.path.normpath(
                            os.path.join(current_cwd, os.path.expanduser(target))
                        )

                    if target != "-":
                        prev_cwd = current_cwd
                    current_cwd = new_cwd

                # ── pushd ──────────────────────────────────────────────────────
                elif cmd == "pushd" or cmd.startswith("pushd ") or cmd.startswith("pushd\t"):
                    import shlex
                    try:
                        tokens = shlex.split(cmd)
                    except Exception as e:
                        logger.debug("shlex split failed: %s", e)
                        tokens = cmd.split()
                        
                    if len(tokens) > 1:
                        target = tokens[1]
                        cwd_stack.append(current_cwd)
                        if target.startswith("/"):
                            current_cwd = os.path.normpath(os.path.expanduser(target))
                        elif target.startswith("~"):
                            current_cwd = os.path.normpath(os.path.expanduser(target))
                        else:
                            current_cwd = os.path.normpath(
                                os.path.join(current_cwd, os.path.expanduser(target))
                            )
                    elif cwd_stack:
                        # pushd with no args swaps the top of the stack and current_cwd
                        cwd_stack[-1], current_cwd = current_cwd, cwd_stack[-1]

                # ── popd ───────────────────────────────────────────────────────
                elif (cmd == "popd" or cmd.startswith("popd ") or cmd.startswith("popd\t")) and cwd_stack:
                    current_cwd = cwd_stack.pop()

            cwd_map[idx] = current_cwd

        return cwd_map

    # =========================================================================
    # Phase B — Detectors
    # =========================================================================

    def detect_git_commit(
        self, command: str, virtual_cwd: str
    ) -> Optional[Tuple[int, str]]:
        """
        Detector 1 — Git Commit Message Matcher  ⭐ Highest confidence.

        Strategy
        --------
        1. Extract the `-m "message"` string from the command.
        2. Clean both the extracted message and each git log subject using
           _clean_for_match() to normalise conventional prefixes and emojis.
        3. Use difflib.SequenceMatcher ratio ≥ 0.85 to find the best match.
        4. Scope the search to the git repo at `virtual_cwd` first
           (Multi-Repo Collision Trap — Upgrade #4).  Only fall back to other
           known project paths if no local repo is found.

        Collision guard
        ---------------
        By searching the CWD-derived repo first, we avoid assigning the timestamp
        from an identically-worded commit in a different project.  "fix typo" in
        Project A should not steal the timestamp from Project B's "fix typo" commit.

        Returns (unix_timestamp, source_string) or None.
        """
        msg = self._extract_git_commit_message(command)
        if not msg:
            return None
        cleaned_cmd_msg = self._clean_for_match(msg)
        if not cleaned_cmd_msg:
            return None

        # Build prioritised list of repos to search — CWD repo goes first
        repos_to_try: List[str] = []
        local_root = self._find_git_root(virtual_cwd)
        if local_root:
            repos_to_try.append(local_root)
        for p in self.project_paths:
            root = self._find_git_root(p)
            if root and root not in repos_to_try:
                repos_to_try.append(root)

        best_ts: Optional[int] = None
        best_ratio = 0.0
        best_source = ""

        for repo_path in repos_to_try:
            commits = self._load_git_log(repo_path)
            repo_name = os.path.basename(repo_path)
            for commit in reversed(commits):
                if commit["hash"] in getattr(self, '_used_commit_hashes', set()):
                    continue
                commit_cleaned = self._clean_for_match(commit["message"])
                ratio = difflib.SequenceMatcher(
                    None, cleaned_cmd_msg, commit_cleaned
                ).ratio()
                if ratio >= 0.85 and ratio > best_ratio:
                    ts = commit["timestamp"]
                    if self._is_valid_timestamp(ts):
                        best_ratio = ratio
                        best_ts = ts
                        best_hash = commit["hash"]
                        best_source = f"git log: {repo_name}@{commit['hash'][:7]}"
                        if ratio == 1.0:
                            break
            if best_ratio == 1.0:
                break

        if best_ts is not None:
            if not hasattr(self, '_used_commit_hashes'):
                self._used_commit_hashes = set()
            self._used_commit_hashes.add(best_hash)
            return (best_ts, best_source)
        return None



    def _parse_date_string(self, date_str: str) -> Optional[int]:
        from datetime import datetime, timezone
        if date_str.endswith("Z"):
            date_str = date_str[:-1] + "+0000"
            
        formats = [
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%d",
            "%Y_%m_%d",
            "%a, %d %b %Y %H:%M:%S %z",
            "%a %b %d %H:%M:%S %Y %z"
        ]
        for fmt in formats:
            try:
                dt = datetime.strptime(date_str, fmt)
                if dt.tzinfo is None:
                    # Assume local time if no tz provided.
                    # dt.astimezone() converts naive dt as local time correctly.
                    dt = dt.astimezone()
                ts = int(dt.timestamp())
                if self._is_valid_timestamp(ts):
                    return ts
            except ValueError:
                continue
        return None

    def detect_inline_date(self, command: str, virtual_cwd: str) -> Optional[Tuple[int, str]]:
        """
        Detector 7 — Inline Date Strings 🟢 High/Medium confidence.

        Extracts dates from command strings where the date represents "when this was executed".
        Avoids querying/searching arguments (like --since, --newer).
        """
        import re

        # Explicit exclusions
        exclude_patterns = [
            r'--since[= ]', r'--until[= ]', r'--after[= ]', r'--before[= ]',
            r'-newer\b', r'\bgrep\b', r'\bcat\b', r'\bless\b', r'\bhead\b', r'\btail\b',
            r'\btouch\s+-t\b'
        ]
        for ep in exclude_patterns:
            if re.search(ep, command):
                return None

        # 1. git commit --date="..."
        m1 = re.search(r'git\s+commit\b.*?--date[= ](?:(["\'])(.*?)\1|([^\s\'"]+))', command)
        if m1:
            date_str = m1.group(2) or m1.group(3)
            ts = self._parse_date_string(date_str)
            if ts: return (ts, f"inline date: {date_str}")

        # 2. GIT_COMMITTER_DATE="..." git commit
        m2 = re.search(r'GIT_(?:COMMITTER|AUTHOR)_DATE=(?:(["\'])(.*?)\1|([^\s\'"]+))', command)
        if m2:
            date_str = m2.group(2) or m2.group(3)
            ts = self._parse_date_string(date_str)
            if ts: return (ts, f"inline date: {date_str}")

        # 3. tar create with date in name
        m3 = re.search(r'^tar\s+.*?-[a-zA-Z]*[cC][a-zA-Z]*\s+.*?(\d{4}[-_]\d{2}[-_]\d{2})', command)
        if m3:
            ts = self._parse_date_string(m3.group(1))
            if ts: return (ts, f"inline date in filename: {m3.group(1)}")

        # 4. mysqldump / pg_dump with date in name
        m4 = re.search(r'(?:mysqldump|pg_dump)\b.*>\s*\S*(\d{4}[-_]\d{2}[-_]\d{2})', command)
        if m4:
            ts = self._parse_date_string(m4.group(1))
            if ts: return (ts, f"inline date in filename: {m4.group(1)}")

        # 5. mv rename to date
        m5 = re.search(r'^mv\s+\S+\s+\S*(\d{4}[-_]\d{2}[-_]\d{2})', command)
        if m5:
            ts = self._parse_date_string(m5.group(1))
            if ts: return (ts, f"inline date in filename: {m5.group(1)}")

        return None

    def detect_file_stat(
        self, command: str, virtual_cwd: str
    ) -> Optional[Tuple[int, str]]:
        """
        Detector 2 — File System `stat`  🟡 Medium confidence.

        Matches commands that create a new file or directory and stats the
        resulting path for its birth/modification timestamp.

        Supported patterns and their target paths:
          touch <file>              → <file>
          mkdir [-p] <dir>          → last path component
          echo/printf/cat > <file>  → redirect target
          cp <src> <dst>            → <dst>
          git init [dir]            → <dir>/.git  or  cwd/.git
          npm init [-y]             → cwd/package.json
          python -m venv / virtualenv <name> → <name>/bin/activate
          cargo init [dir]          → <dir>/Cargo.toml  or  cwd/Cargo.toml
          go mod init               → cwd/go.mod

        All relative paths are resolved against `virtual_cwd` before statting.
        `touch -t <timestamp>` is explicitly excluded because that command sets
        the file's timestamp rather than recording when it was created.

        Returns (unix_timestamp, source_string) or None.
        """
        cmd = command.strip()
        target_path: Optional[str] = None
        label: Optional[str] = None

        # ── touch <file>  (but NOT touch -t which sets mtime explicitly) ──
        m = re.match(r'^touch\s+(?!-t\s)(.+)$', cmd)
        if m:
            target_path = m.group(1).strip().strip('"\'')
            label = "stat: touch"

        # ── mkdir [-p] <dir> ──
        if not target_path:
            m = re.match(r'^mkdir\s+(?:-p\s+)?(.+)$', cmd)
            if m:
                # Take the last path component (e.g. `mkdir -p a/b/c` → c)
                target_path = m.group(1).strip().strip('"\'').split()[-1]
                label = "stat: mkdir"

        # ── echo/printf/cat/printf > file  (output redirection) ──
        if not target_path:
            if re.search(r'\becho\b|\bprintf\b|\bcat\b', cmd):
                m = re.search(r'>\s*([^\s|&;><]+)$', cmd)
                if m:
                    target_path = m.group(1).strip().strip('"\'')
                    label = "stat: redirect >"

        # ── cp <src> <dst> ──
        if not target_path:
            m = re.match(r'^cp\s+(?:-[a-zA-Z]+\s+)*(\S+)\s+(\S+)$', cmd)
            if m:
                target_path = m.group(2).strip().strip('"\'')
                label = "stat: cp destination"

        # ── git init [dir] ──
        if not target_path:
            m = re.match(r'^git\s+init\s*(.*)', cmd)
            if m:
                subdir = m.group(1).strip().strip('"\'') or "."
                base = virtual_cwd if subdir == "." else os.path.join(virtual_cwd, subdir)
                target_path = os.path.join(base, ".git")
                label = "stat: git init → .git"

        # ── npm init [-y] ──
        if not target_path:
            if re.match(r'^npm\s+init', cmd):
                target_path = os.path.join(virtual_cwd, "package.json")
                label = "stat: npm init → package.json"

        # ── python -m venv <name>  /  virtualenv <name> ──
        if not target_path:
            m = re.match(r'^(?:python3?\s+-m\s+venv|virtualenv)\s+(\S+)', cmd)
            if m:
                venv_name = m.group(1).strip().strip('"\'')
                target_path = os.path.join(virtual_cwd, venv_name, "bin", "activate")
                label = f"stat: venv/{venv_name}/bin/activate"

        # ── cargo init [dir] ──
        if not target_path:
            m = re.match(r'^cargo\s+init\s*(.*)', cmd)
            if m:
                subdir = m.group(1).strip().strip('"\'') or "."
                base = virtual_cwd if subdir == "." else os.path.join(virtual_cwd, subdir)
                target_path = os.path.join(base, "Cargo.toml")
                label = "stat: cargo init → Cargo.toml"

        # ── go mod init ──
        if not target_path:
            if re.match(r'^go\s+mod\s+init', cmd):
                target_path = os.path.join(virtual_cwd, "go.mod")
                label = "stat: go mod init → go.mod"

        if not target_path:
            return None

        # Resolve relative paths against the virtual working directory
        if not os.path.isabs(target_path):
            target_path = os.path.join(virtual_cwd, target_path)
        target_path = os.path.normpath(os.path.expanduser(target_path))

        ts = self._get_file_timestamp(target_path)
        return (ts, label) if ts else None

    def detect_package_manager(
        self, command: str, virtual_cwd: str
    ) -> Optional[Tuple[int, str]]:
        """
        Detector 3 — Package Manager Install Metadata  🟡 Medium confidence.

        Each package manager leaves a datable artifact on the filesystem after
        a successful install.  We stat that artifact to recover when the install
        actually ran.

        Supported managers:
          brew install/upgrade <formula>  → Homebrew Cellar/<formula> dir mtime
          pip/pip3 install <pkg>          → pip dist-info directory mtime
          npm install / npm i             → package-lock.json mtime (local)
          npm install -g <pkg>            → global node_modules/<pkg> dir mtime
          cargo add/install <crate>       → ~/.cargo/registry/src/*/*crate* dir
          gem install <gem>              → ~/.gem/ruby/*/gems/<gem>-* dir

        Returns (unix_timestamp, source_string) or None.
        """
        cmd = command.strip()

        # ── Homebrew: brew install / brew upgrade ──
        m = re.match(r'^brew\s+(?:install|upgrade)\s+([\w@/.\-]+)', cmd)
        if m:
            # Strip tap prefix (homebrew/core/jq → jq) and version pin (jq@1.6 → jq)
            formula = m.group(1).split("/")[-1].split("@")[0]
            prefix = self._get_brew_prefix()
            if prefix:
                cellar_path = os.path.join(prefix, "Cellar", formula)
                ts = self._get_file_timestamp(cellar_path)
                if ts:
                    return (ts, f"brew Cellar/{formula}")

        # ── pip / pip3: single-package install (skip -r requirements files) ──
        m = re.match(r'^pip3?\s+install\s+(?!-r\s)(?!--requirement\s)([\w\-\.]+)', cmd)
        if m:
            package = m.group(1)
            ts = self._get_pip_package_timestamp(package)
            if ts:
                return (ts, f"pip dist-info: {package}")

        # ── npm local install (no -g flag): use package-lock.json mtime ──
        if re.match(r'^npm\s+(?:install|i)(?!\s+.*-g)(?:\s|$)', cmd):
            for lock_name in ("package-lock.json", os.path.join("node_modules", ".package-lock.json")):
                lock_path = os.path.join(virtual_cwd, lock_name)
                ts = self._get_file_timestamp(lock_path)
                if ts:
                    return (ts, f"npm: {lock_name}")

        # ── npm global install: npm install -g <pkg> or npm i -g <pkg> ──
        m = re.search(r'npm\s+(?:install|i)\s+(?:-g|--global)\s+([\w@/.\-]+)', cmd)
        if not m:
            m = re.search(r'npm\s+(?:install|i)\s+([\w@/.\-]+)\s+(?:-g|--global)', cmd)
        if m:
            package = m.group(1).split("@")[0].split("/")[-1]
            npm_root = self._get_npm_global_root()
            if npm_root:
                pkg_path = os.path.join(npm_root, package)
                ts = self._get_file_timestamp(pkg_path)
                if ts:
                    return (ts, f"npm global: {package}")

        # ── Cargo: cargo add / cargo install ──
        m = re.match(r'^cargo\s+(?:add|install)\s+([\w\-]+)', cmd)
        if m:
            crate = m.group(1)
            ts = self._get_cargo_crate_timestamp(crate)
            if ts:
                return (ts, f"cargo registry: {crate}")

        # ── RubyGems: gem install ──
        m = re.match(r'^gem\s+install\s+([\w\-]+)', cmd)
        if m:
            gem_name = m.group(1)
            ts = self._get_gem_timestamp(gem_name)
            if ts:
                return (ts, f"gem: {gem_name}")

        return None

    def _get_pip_package_timestamp(self, package: str) -> Optional[int]:
        """
        Find the dist-info directory for a pip package and stat it.

        Tries importlib.metadata first (Python 3.8+), then falls back to a
        glob search over all known site-packages directories.
        """
        # Try importlib.metadata (most reliable — returns the installed location)
        try:
            import importlib.metadata as ilm
            try:
                dist = ilm.distribution(package)
                # _path is the dist-info directory path on CPython
                dist_path = str(getattr(dist, "_path", None) or dist.locate_file(""))
                ts = self._get_file_timestamp(dist_path)
                if ts:
                    return ts
            except Exception as e:
                logger.debug("timestamp_detective probe failed: %s", e)
        except ImportError:
            pass

        # Fallback: glob site-packages for matching dist-info directories
        site_dirs = getattr(site, "getsitepackages", lambda: [])()
        user_site = site.getusersitepackages() if hasattr(site, "getusersitepackages") else None
        if user_site:
            site_dirs = list(site_dirs) + [user_site]

        for site_dir in site_dirs:
            pattern = os.path.join(site_dir, f"{package}*.dist-info")
            matches = glob.glob(pattern)
            if matches:
                ts = self._get_file_timestamp(sorted(matches)[-1])  # pick newest version
                if ts:
                    return ts
        return None

    def _get_cargo_crate_timestamp(self, crate: str) -> Optional[int]:
        """
        Stat the Cargo registry source directory for a given crate.
        ~/.cargo/registry/src/<registry-hash>/<crate>-<version>/ is created when
        a crate is first compiled and its source is extracted.
        """
        registry_src = os.path.expanduser("~/.cargo/registry/src")
        if not os.path.isdir(registry_src):
            return None
        # Use a glob because the registry hash sub-directory name varies
        pattern = os.path.join(registry_src, "*", f"{crate}-*")
        matches = glob.glob(pattern)
        if matches:
            return self._get_file_timestamp(sorted(matches)[-1])
        return None

    def _get_gem_timestamp(self, gem_name: str) -> Optional[int]:
        """
        Stat the RubyGems installation directory for a given gem.
        Looks inside ~/.gem/ruby/<version>/gems/<gem>-<version>/.
        """
        gem_home = os.path.expanduser("~/.gem/ruby")
        if not os.path.isdir(gem_home):
            return None
        pattern = os.path.join(gem_home, "*", "gems", f"{gem_name}-*")
        matches = glob.glob(pattern)
        if matches:
            return self._get_file_timestamp(sorted(matches)[-1])
        return None

    def detect_docker(
        self, command: str, virtual_cwd: str
    ) -> Optional[Tuple[int, str]]:
        """
        Detector 4 — Docker Image Inspector  🟡 Medium confidence.

        For `docker build -t <tag>` commands, runs:
            docker image inspect <tag> --format='{{.Created}}'
        to obtain the exact timestamp when the image was built.

        The Created field is in RFC3339/ISO format; we parse it with
        datetime.fromisoformat() after normalising the trailing timezone.

        Also handles:
          docker build --tag <tag>         (long-form flag)

        Returns (unix_timestamp, source_string) or None.
        """
        cmd = command.strip()

        # Match: docker build [opts] -t <tag> [path]
        m = re.search(r'docker\s+build\s+.*?-[a-zA-Z]*t\s+([\w:./\-]+)', cmd)
        if not m:
            m = re.search(r'docker\s+build\s+.*?--tag\s+([\w:./\-]+)', cmd)

        if not m:
            return None

        tag = m.group(1)
        try:
            res = subprocess.run(
                ["docker", "image", "inspect", tag, "--format={{.Created}}"],
                capture_output=True, text=True, timeout=5
            )
            if res.returncode != 0:
                return None

            created_str = res.stdout.strip()
            if not created_str:
                return None

            # Normalise timezone: strip sub-second precision and ensure +00:00 form
            created_str = re.sub(r'\.\d+', '', created_str)
            created_str = created_str.replace("Z", "+00:00")
            dt = datetime.fromisoformat(created_str)
            ts = int(dt.timestamp())
            if self._is_valid_timestamp(ts):
                return (ts, f"docker image inspect: {tag}")
        except Exception as e:
            logger.debug("timestamp_detective probe failed: %s", e)

        return None

    def detect_venv_lockfile(
        self, command: str, virtual_cwd: str
    ) -> Optional[Tuple[int, str]]:
        """
        Detector 5 — Venv / Lockfile Sentinels  🟡 Low-Medium confidence.

        Many language toolchains update or create a lockfile / sentinel file
        on every dependency operation.  We stat that file to recover when the
        command was run.

        Supported patterns:
          bundle install / update    → Gemfile.lock
          go mod tidy / go get       → go.sum
          composer install / update  → composer.lock
          poetry add / install       → poetry.lock
          mix deps.get               → mix.lock
          git clone <url> [dir]      → <dir>/.git  (birthtime of the cloned repo)
          ssh-keygen -f <file>       → <file>       (birthtime of the key)

        Returns (unix_timestamp, source_string) or None.
        """
        cmd = command.strip()

        # ── Language lockfiles ──────────────────────────────────────────────
        lockfile_patterns = [
            (r'^bundle\s+(?:install|update)',       "Gemfile.lock",    "bundler: Gemfile.lock"),
            (r'^go\s+mod\s+tidy|^go\s+get\s',      "go.sum",          "go: go.sum"),
            (r'^composer\s+(?:install|update)',     "composer.lock",   "composer: composer.lock"),
            (r'^poetry\s+(?:add|install|update)',   "poetry.lock",     "poetry: poetry.lock"),
            (r'^mix\s+deps\.get',                   "mix.lock",        "mix: mix.lock"),
        ]
        for pattern, filename, label in lockfile_patterns:
            if re.match(pattern, cmd):
                lock_path = os.path.join(virtual_cwd, filename)
                ts = self._get_file_timestamp(lock_path)
                if ts:
                    return (ts, label)

        # ── git clone <url> [target_dir] ────────────────────────────────────
        # Two forms:
        #   git clone https://github.com/user/repo.git mydir
        #   git clone https://github.com/user/repo.git   (dir derived from URL)
        m = re.match(r'^git\s+clone\s+(\S+)(?:\s+(\S+))?', cmd)
        if m:
            url = m.group(1)
            explicit_dir = m.group(2)
            if explicit_dir:
                dir_name = explicit_dir.strip().strip('"\'')
            else:
                # Derive directory name from URL (strip .git suffix)
                dir_name = url.rstrip("/").split("/")[-1]
                if dir_name.endswith(".git"):
                    dir_name = dir_name[:-4]
            git_path = os.path.join(virtual_cwd, dir_name, ".git")
            ts = self._get_file_timestamp(git_path)
            if ts:
                return (ts, f"git clone → {dir_name}/.git birthtime")

        # ── ssh-keygen -f <keyfile> ─────────────────────────────────────────
        m = re.match(r'^ssh-keygen\s+.*-f\s+(\S+)', cmd)
        if m:
            key_path = m.group(1).strip().strip('"\'')
            if not os.path.isabs(key_path):
                key_path = os.path.join(virtual_cwd, key_path)
            ts = self._get_file_timestamp(os.path.normpath(key_path))
            if ts:
                return (ts, "stat: ssh-keygen key birthtime")

        return None

    def detect_macos_system_log(
        self, command: str, virtual_cwd: str
    ) -> Optional[Tuple[int, str]]:
        """
        Detector 6 — macOS System Log Anchors (last / brew log)  🟡 Low-Medium confidence.

        macOS keeps forensic trails that can anchor legacy commands:

          • `brew install <formula>` → `brew log <formula>` exposes the tap's git
            history for that formula.  The most recent commit date is a strong
            proxy for when the formula was last updated / installed.
          • login/reboot-adjacent commands (`ssh`, `sudo`, `su`) → the macOS
            `last` command records login session timestamps, which can anchor
            commands that were run right after a login session.

        As a secondary source, `/private/var/log/install.log` records exact
        installer timestamps for package installs (see `detect_macos_syslog`).

        Returns (unix_timestamp, source_string) or None.
        """
        cmd = command.strip()

        # ── brew install <formula> → brew log <formula> ─────────────────────
        m = re.match(r'^brew\s+install\s+([\w@/.\-]+)', cmd)
        if m:
            # Strip tap prefix (homebrew/core/jq → jq) and version pin (jq@1.6 → jq)
            formula = m.group(1).split("/")[-1].split("@")[0]
            try:
                res = subprocess.run(
                    ["brew", "log", "--max-count=1", formula],
                    capture_output=True, text=True, timeout=5
                )
                if res.returncode == 0 and res.stdout:
                    ts = self._parse_git_log_date(res.stdout)
                    if ts and self._is_valid_timestamp(ts):
                        return (ts, f"brew log: {formula}")
            except Exception as e:
                logger.debug("timestamp_detective probe failed: %s", e)

            # Fallback: scan the macOS installer system log for this formula
            ts = self.detect_macos_syslog(formula)
            if ts and self._is_valid_timestamp(ts):
                return (ts, f"install.log: {formula}")

        # ── login/reboot-adjacent commands → last -1 <username> ─────────────
        if re.match(r'^(?:sudo|su|ssh)(?:\s|$)', cmd):
            try:
                username = getpass.getuser()
            except Exception as e:
                logger.debug("getpass user failed: %s", e)
                username = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
            if username:
                try:
                    res = subprocess.run(
                        ["last", "-1", username],
                        capture_output=True, text=True, timeout=5
                    )
                    if res.returncode == 0 and res.stdout:
                        ts = self._parse_last_login(res.stdout)
                        if ts and self._is_valid_timestamp(ts):
                            return (ts, f"last login: {username}")
                except Exception as e:
                    logger.debug("timestamp_detective probe failed: %s", e)

        return None

    def _parse_git_log_date(self, log_output: str) -> Optional[int]:
        """
        Parse the first `Date:` line out of `git log` (or `brew log`) output and
        return it as a Unix timestamp.  Handles both the default git date format
        and the ISO (`--date=iso`) format.
        """
        for line in log_output.splitlines():
            stripped = line.strip()
            if stripped.startswith("Date:"):
                date_str = stripped[len("Date:"):].strip()
                for fmt in ("%a %b %d %H:%M:%S %Y %z", "%Y-%m-%d %H:%M:%S %z"):
                    try:
                        return int(datetime.strptime(date_str, fmt).timestamp())
                    except ValueError:
                        continue
        return None

    def _parse_last_login(self, last_output: str) -> Optional[int]:
        """
        Parse the login timestamp from macOS `last` output.

        A typical line looks like:
            user  ttys000  192.168.0.1  Thu Jun  5 09:14   still logged in

        `last` omits the year, so we assume the current year and step back one
        year if that would place the login in the future.
        """
        m = re.search(
            r'([A-Z][a-z]{2}\s+[A-Z][a-z]{2}\s+\d{1,2}\s+\d{1,2}:\d{2})',
            last_output
        )
        if not m:
            return None
        date_str = re.sub(r'\s+', ' ', m.group(1)).strip()
        try:
            dt = datetime.strptime(date_str, "%a %b %d %H:%M")
        except ValueError:
            return None
        now = datetime.now()
        dt = dt.replace(year=now.year)
        if dt.timestamp() > now.timestamp():
            dt = dt.replace(year=now.year - 1)
        return int(dt.timestamp())

    def detect_macos_syslog(self, package: str) -> Optional[int]:
        """
        Helper — scan macOS `/private/var/log/install.log` for an install record
        of `package` (or any brew-related line) and return the most recent
        matching install timestamp.

        Lines look like:
            2024-01-01 12:00:00+00 host installd[1]: ... Installed: <formula>

        Returns the Unix timestamp or None.
        """
        log_path = "/private/var/log/install.log"
        if not os.path.exists(log_path):
            return None
        try:
            with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
        except OSError:
            return None

        needle = package.lower()
        date_re = re.compile(r'^(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})([+-]\d{2}:?\d{2}|[+-]\d{2}|Z)?')
        best_ts: Optional[int] = None
        for line in lines:
            low = line.lower()
            if "installed" not in low:
                continue
            if needle not in low:
                continue
            m = date_re.match(line.strip())
            if not m:
                continue
            date_str = m.group(1).replace("T", " ")
            offset = m.group(2)
            try:
                if offset:
                    if offset == "Z":
                        offset = "+00:00"
                    elif len(offset) == 3:  # e.g. -07 or +00
                        offset = offset + ":00"
                    elif len(offset) == 5:  # e.g. -0700
                        offset = offset[:3] + ":" + offset[3:]
                    iso_str = date_str.replace(" ", "T") + offset
                    dt = datetime.fromisoformat(iso_str)
                    ts = int(dt.timestamp())
                else:
                    ts = int(datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S").timestamp())
            except Exception as e:
                logger.debug("timestamp parse failed: %s", e)
                continue
            if self._is_valid_timestamp(ts) and (best_ts is None or ts > best_ts):
                best_ts = ts
        return best_ts

    def _run_all_detectors(
        self, command: str, virtual_cwd: str
    ) -> Optional[Tuple[int, str]]:
        """
        Run all detectors in priority order and return the first successful result.

        Priority order (highest confidence first):
          1. Git commit matcher  — exact message fuzzy-match against git history
          2. Inline date strings — parses embedded dates like --date=YYYY-MM-DD
          3. File stat           — filesystem birthtime/mtime of created artifact
          4. Package manager     — install artifact mtime (brew/pip/npm/cargo/gem)
          5. Docker image        — docker image inspect timestamp
          6. Venv / lockfiles    — lockfile/sentinel mtime
          7. macOS system log    — brew log / last login anchors

        Each detector is wrapped in a try/except so a single buggy detector
        cannot crash the entire pipeline.
        """
        detectors = [
            self.detect_git_commit,
            self.detect_inline_date,
            self.detect_file_stat,
            self.detect_package_manager,
            self.detect_docker,
            self.detect_venv_lockfile,
            self.detect_macos_system_log,
        ]
        for detector in detectors:
            try:
                result = detector(command, virtual_cwd)
                if result is not None:
                    return result
            except Exception as e:
                logger.debug("Detector %s error: %s", detector.__name__, e)
                # Keep the pipeline running for remaining detectors
                continue
                continue
        return None

    # =========================================================================
    # Phase C — Anchor Interpolation Engine
    # =========================================================================

    def _find_oldest_repo_anchor(self) -> Optional[int]:
        """
        Scan all known project paths for the oldest git commit timestamp.

        Used as a left-bound for prefix gap interpolation: commands before the
        first Detective anchor are spread between this oldest commit and t_first
        rather than being crammed into n_prefix seconds before t_first.

        Reuses _git_log_cache to avoid redundant git log subprocess calls.
        Returns None if no git repos are found or all commits are invalid.
        """
        oldest: Optional[int] = None
        candidate_roots = set()

        for p in self.project_paths:
            root = self._find_git_root(p)
            if root:
                candidate_roots.add(root)

        # Also check search_root itself
        root = self._find_git_root(self.search_root)
        if root:
            candidate_roots.add(root)

        for repo_path in candidate_roots:
            commits = self._load_git_log(repo_path)  # uses _git_log_cache
            for commit in commits:
                ts = commit.get("timestamp", 0)
                if self._is_valid_timestamp(ts):
                    if oldest is None or ts < oldest:
                        oldest = ts

        return oldest

    def _interpolate(self, items: List[dict]) -> List[dict]:
        """
        Phase C — Anchor Interpolation Engine  ⭐ The Killer Feature.

        After Phase B, some items have a real `detected_ts` (anchors) and the
        rest are still None.  This method fills the gaps using linear interpolation.

        Math
        ----
        For a gap between anchor A (index i_a, timestamp t_a) and anchor B
        (index i_b, timestamp t_b), every unresolved item at index i is assigned:

            t_i = t_a + (t_b − t_a) × (i − i_a) / (i_b − i_a)

        This is equivalent to placing the command at the proportional position in
        time between the two known events — the mathematically most-likely moment.

        Edge cases
        ----------
        • Prefix gap (items before the first anchor):
            Spread proportionally back to the oldest known repository commit.
        • Suffix gap (items after the last anchor):
            Spread proportionally forward from the last anchor using a minimum 1-day window.
        • No anchors at all:
            Return items unchanged; the parser's mtime step-back handles this.

        is_legacy flag
        --------------
        Interpolated commands remain `is_legacy_still = True` because their
        timestamps are still synthetic (educated guesses, not ground truth).
        Only Phase B resolved commands with `is_legacy_still = False` have real
        timestamps from external evidence.

        The distinction matters for the Chain of Custody tooltip: interpolated
        commands show "[🔍 Interpolated (between X and Y)]" while Phase B resolved
        commands show the specific evidence source.
        """
        n = len(items)
        if n == 0:
            return items

        # Collect indices of all anchors (items where Phase B found a real timestamp)
        anchors = [
            (i, items[i]) for i in range(n)
            if items[i].get("detected_ts") is not None
        ]

        if not anchors:
            # No forensic evidence found at all — leave everything as-is
            # and let the parser's mtime step-back handle it
            return items

        result = list(items)  # shallow copy; we'll mutate detected_ts/source

        # ── Fill gaps between consecutive anchor pairs ──────────────────────
        for k in range(len(anchors) - 1):
            i_a, anchor_a = anchors[k]
            i_b, anchor_b = anchors[k + 1]
            t_a = anchor_a["detected_ts"]
            t_b = anchor_b["detected_ts"]
            src_a = anchor_a.get("detected_source", "anchor")
            src_b = anchor_b.get("detected_source", "anchor")

            # Indices in the gap that still need timestamps
            gap_indices = [
                i for i in range(i_a + 1, i_b)
                if result[i].get("detected_ts") is None
            ]
            if not gap_indices:
                continue

            span = i_b - i_a  # total index distance between the two anchors
            for i in gap_indices:
                fraction = (i - i_a) / span
                interpolated_ts = int(t_a + (t_b - t_a) * fraction)
                result[i]["detected_ts"] = interpolated_ts
                result[i]["detected_source"] = (
                    f"Interpolated (between {src_a} → {src_b})"
                )
                result[i]["is_legacy_still"] = True  # synthetic, but correctly placed

        # ── Prefix gap: commands before the first anchor ────────────────────
        first_anchor_idx, first_anchor = anchors[0]
        t_first = first_anchor["detected_ts"]
        src_first = first_anchor.get("detected_source", "first anchor")
        prefix_unresolved = [
            i for i in range(first_anchor_idx)
            if result[i].get("detected_ts") is None
        ]
        n_prefix = len(prefix_unresolved)
        if prefix_unresolved:
            # Find the oldest available repo anchor as the left bound.
            # This spreads prefix commands across real time rather than cramming
            # them into n_prefix seconds before t_first.
            oldest_repo_ts = self._find_oldest_repo_anchor()
            if oldest_repo_ts is not None and oldest_repo_ts < t_first:
                left_bound = oldest_repo_ts
            else:
                # No repo anchor available: push back 1 day per command, but
                # clamp to five_years_ago + n_prefix to avoid the parser's
                # 5-year filter silently dropping these commands.
                raw_bound = t_first - n_prefix * 86400
                left_bound = max(raw_bound, self.five_years_ago + n_prefix)

            for offset, i in enumerate(prefix_unresolved):
                fraction = (offset + 1) / (n_prefix + 1)
                result[i]["detected_ts"] = int(left_bound + (t_first - left_bound) * fraction)
                result[i]["detected_source"] = f"Pre-anchor interpolation (before {src_first})"
                result[i]["is_legacy_still"] = True

        # ── Suffix gap: commands after the last anchor ──────────────────────
        last_anchor_idx, last_anchor = anchors[-1]
        t_last = last_anchor["detected_ts"]
        src_last = last_anchor.get("detected_source", "last anchor")
        suffix_unresolved = [
            i for i in range(last_anchor_idx + 1, n)
            if result[i].get("detected_ts") is None
        ]
        n_suffix = len(suffix_unresolved)
        window = max(n_suffix * 10, 86400)
        if t_last + window > self.now:
            window = max(n_suffix, self.now - t_last)
        for offset, i in enumerate(suffix_unresolved):
            fraction = (offset + 1) / (n_suffix + 1)
            ts = int(t_last + fraction * window)
            if ts > self.now:
                ts = self.now
            result[i]["detected_ts"] = ts
            result[i]["detected_source"] = f"Post-anchor proportional spread (after {src_last})"
            result[i]["is_legacy_still"] = True

        return result

    # =========================================================================
    # Public API
    # =========================================================================

    def resolve_all(self, legacy_items: List[dict]) -> List[dict]:
        """
        Main entry point for the Timestamp Detective pipeline.

        Takes the ordered list of legacy command dicts (items with no original
        timestamp from the Zsh/Bash parser) and returns the same list enriched
        with three new keys on every item:

            "detected_ts"     → int | None
                Real Unix timestamp recovered from external evidence, or the
                interpolated/step-back synthetic timestamp.  None only if the
                file had zero anchors (handled by the parser's mtime fallback).

            "detected_source" → str | None
                Human-readable attribution string for the Chain of Custody UI.
                None for items that had no anchors at all.

            "is_legacy_still" → bool
                True  → timestamp is synthetic (interpolated or step-back);
                        this command will be tagged is_legacy=True on its Command obj.
                False → timestamp is real evidence from Phase B; command gets
                        is_legacy=False and appears in the genuine timeline.

        Pipeline
        --------
        Phase A: Build a virtual CWD map by replaying cd/pushd/popd in sequence.
        Phase B: Run all detectors on each command using the per-command CWD.
        Phase C: Fill remaining gaps with linear interpolation between anchors.

        Parameters
        ----------
        legacy_items : List[dict]
            Each dict must have at least a "command" key (the raw command string).
            The dicts are mutated in-place via the returned list.

        Returns
        -------
        List[dict]
            Same list, every item enriched with detected_ts / detected_source /
            is_legacy_still.
        """
        if not legacy_items:
            return legacy_items

        # ── Phase A: Virtual CWD Tracker ────────────────────────────────────
        cwd_map = self._build_virtual_cwd_map(legacy_items)

        # ── Phase B: Per-command detector sweep ─────────────────────────────
        enriched = []
        for idx, item in enumerate(legacy_items):
            virtual_cwd = cwd_map.get(idx, self.search_root)
            entry = dict(item)  # copy so we don't mutate the original list
            entry["detected_ts"] = None
            entry["detected_source"] = None
            entry["is_legacy_still"] = True  # default: assume still synthetic

            detection = self._run_all_detectors(item["command"], virtual_cwd)
            if detection is not None:
                ts, source = detection
                entry["detected_ts"] = ts
                entry["detected_source"] = source
                entry["is_legacy_still"] = False  # promoted: real timestamp found!

            enriched.append(entry)

        # ── Phase C: Anchor Interpolation ───────────────────────────────────
        return self._interpolate(enriched)
