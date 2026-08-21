import getpass
import logging
import os
import re
import sqlite3
import subprocess
import time
from typing import Dict, Any, List

from termstory.sanitizer import redact_command

logger = logging.getLogger(__name__)

# Cap the number of uncommitted-file entries retained in a single snapshot. A
# `git status` listing can otherwise surface tens of thousands of untracked
# files (e.g. scattered build artifacts), producing an enormous snapshot and
# heavy per-entry scrubbing. `--untracked-files=normal` already collapses each
# huge *untracked directory* (node_modules/, dist/, build/) to a single entry,
# so this cap only bounds the pathological scattered-file case.
_MAX_UNCOMMITTED_FILES = 1000

# IDE/editor environment variable prefixes. Their *presence* drives IDE
# detection, but their values are never persisted: they routinely hold socket
# paths (e.g. VSCODE_IPC_HOOK_CLI, NVIM_LISTEN_ADDRESS), askpass helpers,
# session identifiers, or license/locally-identifying data.
IDE_PREFIXES = ("VSCODE_", "IDEA_", "JETBRAINS_", "XCODE_", "NVIM_", "CURSOR_")

# General editor detection signals. TERM_PROGRAM is a program name (safe to
# persist verbatim); EDITOR/VISUAL normally name an editor binary, so only
# the trailing command name is retained (no home/username path component).
GENERAL_EDITOR_SIGNALS = ("EDITOR", "VISUAL")

def capture_ide_state() -> Dict[str, Any]:
    """Capture active IDE state from environment variables.

    IDE detection relies on the *presence* of IDE-prefixed environment
    variables and on a small set of safe signals (TERM_PROGRAM, EDITOR,
    VISUAL). Raw values of IDE-prefixed variables are deliberately not
    persisted because they frequently contain sockets, tokens, session
    identifiers or other locally-identifying data.
    """
    ide_vars: Dict[str, Any] = {}
    ide_name = "Unknown"

    # TERM_PROGRAM is a terminal/IDE program name (never a path), so its value
    # is safe to expose and directly identifies VS Code / Cursor.
    term_prog = os.environ.get("TERM_PROGRAM")
    if term_prog:
        ide_vars["TERM_PROGRAM"] = redact_command(term_prog)
        if "vscode" in term_prog.lower():
            ide_name = "VS Code"
        elif "cursor" in term_prog.lower():
            ide_name = "Cursor"

    # Scan environment variable *names* for IDE prefixes. Detection is based
    # on presence only; values are intentionally not stored.
    for k in os.environ:
        k_upper = k.upper()
        if any(term in k_upper for term in IDE_PREFIXES):
            if "VSCODE_" in k_upper and ide_name == "Unknown":
                ide_name = "VS Code"
            elif "CURSOR_" in k_upper and ide_name == "Unknown":
                ide_name = "Cursor"
            elif ("IDEA_" in k_upper or "JETBRAINS_" in k_upper) and ide_name == "Unknown":
                ide_name = "JetBrains"
            elif "XCODE_" in k_upper and ide_name == "Unknown":
                ide_name = "Xcode"
            elif "NVIM_" in k_upper and ide_name == "Unknown":
                ide_name = "Neovim"

    # General editors. EDITOR/VISUAL may point at a binary path; only the
    # trailing command name is retained so no locally-identifying path
    # component (home directory, username) is persisted.
    for var in GENERAL_EDITOR_SIGNALS:
        val = os.environ.get(var)
        if val:
            if ide_name == "Unknown":
                low = val.lower()
                if "nvim" in low:
                    ide_name = "Neovim"
                elif "vim" in low:
                    ide_name = "Vim"
                elif "code" in low:
                    ide_name = "VS Code"
            cmd = val.strip()
            cmd_name = cmd.split(" ", 1)[0] if cmd else val
            cmd_name = cmd_name.replace("\\", "/").rsplit("/", 1)[-1]
            ide_vars[var] = redact_command(cmd_name)

    return {
        "ide_name": ide_name,
        "env_vars": ide_vars
    }

def _home_dirs() -> List[str]:
    """Candidate home directory strings for the current user."""
    homes: List[str] = []
    for key in ("HOME", "USERPROFILE"):
        val = os.environ.get(key)
        if val and val not in homes:
            homes.append(val)
    try:
        expanded = os.path.expanduser("~")
        if expanded and expanded not in homes:
            homes.append(expanded)
    except Exception:
        pass
    return homes


def _user_names() -> List[str]:
    """Candidate username strings for the current user."""
    names: List[str] = []
    for key in ("USERNAME", "USER"):
        val = os.environ.get(key)
        if val and val not in names:
            names.append(val)
    try:
        uname = getpass.getuser()
        if uname and uname not in names:
            names.append(uname)
    except Exception:
        pass
    return names


def _scrub_git_path(line: str) -> str:
    """Scrub locally-identifying path components from a raw git-status line.

    Replaces the user's home directory and any username appearing as a path
    component with redaction placeholders, then runs the value through the
    standard command redaction pipeline as a final defensive pass.
    """
    scrubbed = line
    for home in _home_dirs():
        if home:
            scrubbed = scrubbed.replace(home, "<REDACTED_HOME>")
    for name in _user_names():
        # Only match whole path segments (word-boundary delimited) and skip
        # trivial single-character names so ordinary file names are not
        # needlessly corrupted.
        if name and len(name) >= 2:
            scrubbed = re.sub(
                r"(?<![A-Za-z0-9_]){}(?![A-Za-z0-9_])".format(re.escape(name)),
                "<REDACTED_USER>",
                scrubbed,
            )
    return redact_command(scrubbed)


def capture_git_status(cwd: str) -> Dict[str, Any]:
    """Capture Git status (branch, uncommitted files) for the given directory"""
    result = {
        "is_repo": False,
        "branch": None,
        "uncommitted_files": []
    }
    
    if not cwd or not os.path.exists(cwd) or not os.path.isdir(cwd):
        return result
        
    try:
        # Check if directory is inside a git repository
        res = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--is-inside-work-tree"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            check=False,
            timeout=5
        )
        if res.returncode != 0:
            return result
            
        result["is_repo"] = True
        
        # Get active branch name
        branch_res = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--abbrev-ref", "HEAD"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            check=False,
            timeout=5
        )
        if branch_res.returncode == 0:
            result["branch"] = branch_res.stdout.strip()
            
        # Get uncommitted files (modified, untracked, deleted, etc.).
        # `--untracked-files=normal` prevents Git from recursively walking huge
        # untracked directories (node_modules/, dist/, build/): each such
        # directory is reported as a single `?? dir/` entry instead of being
        # enumerated in full, bounding the cost on large untracked trees. The
        # retained list is additionally capped by _MAX_UNCOMMITTED_FILES so a
        # pathological number of scattered untracked files cannot produce an
        # unbounded snapshot. We use a streaming approach with Popen to prevent
        # memory exhaustion.
        proc = subprocess.Popen(
            ["git", "-C", cwd, "status", "--porcelain", "--untracked-files=normal"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
        )
        uncommitted = []
        truncated = False
        
        if proc.stdout:
            for line in proc.stdout:
                if line.strip():
                    uncommitted.append(_scrub_git_path(line.strip()))
                    if len(uncommitted) >= _MAX_UNCOMMITTED_FILES:
                        truncated = True
                        proc.terminate()
                        try:
                            proc.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                        break
                        
        if not truncated:
            proc.wait(timeout=5)
            
        if proc.returncode == 0 or truncated:
            result["uncommitted_files"] = uncommitted
            if truncated:
                result["uncommitted_files_truncated"] = True
            
    except (subprocess.TimeoutExpired, OSError):
        logger.exception(
            "capture_git_status: git command failed or timed out for %r; returning partial result.",
            cwd,
        )
        
    return result

def capture_mcp_snapshot() -> Dict[str, Any]:
    """Capture a snapshot of the IDE state, git status, and active terminal directories"""
    try:
        cwd = os.getcwd()
    except OSError:
        logger.exception(
            "capture_mcp_snapshot: failed to resolve current working directory; cwd will be None."
        )
        cwd = None
    ide_info = capture_ide_state()
    git_info = capture_git_status(cwd)
    return {
        "cwd": cwd,
        "ide": ide_info,
        "git": git_info
    }

def capture_and_store_mcp_snapshot(db: Any) -> None:
    """Helper to capture the current state and store it under the latest session"""
    try:
        session_id = db.get_latest_session_id()
        if not session_id:
            return
            
        snapshot = capture_mcp_snapshot()
        
        # Check if we already have an identical snapshot for this session
        existing = db.get_mcp_snapshots(session_id)
        if existing:
            last_snapshot = existing[-1]
            if last_snapshot.get("source") == "cli" and last_snapshot.get("payload") == snapshot:
                return
                
        db.save_mcp_snapshot(
            session_id=session_id,
            source="cli",
            payload=snapshot,
            captured_at=int(time.time())
        )
    except (sqlite3.DatabaseError, OSError, RuntimeError, TypeError, ValueError):
        # Log but do not re-raise: an MCP snapshot failure must not
        # disrupt the core ingestion process. TypeError/ValueError are
        # included because Database.save_mcp_snapshot's json.dumps(payload)
        # call re-raises those unchanged on a non-serializable or circular
        # payload (via _safe_rollback_and_reraise), on top of the sqlite3/
        # OSError/RuntimeError failure modes from the db layer itself.
        logger.exception(
            "capture_and_store_mcp_snapshot: failed to capture or store MCP snapshot; continuing without it."
        )
