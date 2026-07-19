import logging
import os
import sqlite3
import subprocess
import time
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

def capture_ide_state() -> Dict[str, Any]:
    """Capture active IDE state from environment variables"""
    ide_vars = {}
    ide_name = "Unknown"
    
    # Look at TERM_PROGRAM
    term_prog = os.environ.get("TERM_PROGRAM")
    if term_prog:
        ide_vars["TERM_PROGRAM"] = term_prog
        if "vscode" in term_prog.lower():
            ide_name = "VS Code"
        elif "cursor" in term_prog.lower():
            ide_name = "Cursor"
            
    # Scan environment for IDE/editor related variables
    for k, v in os.environ.items():
        k_upper = k.upper()
        if any(term in k_upper for term in ["VSCODE_", "IDEA_", "JETBRAINS_", "XCODE_", "NVIM_", "CURSOR_"]):
            ide_vars[k] = v
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
                
    # Check general editors
    for var in ["EDITOR", "VISUAL"]:
        val = os.environ.get(var)
        if val:
            ide_vars[var] = val
            if ide_name == "Unknown":
                if "nvim" in val.lower():
                    ide_name = "Neovim"
                elif "vim" in val.lower():
                    ide_name = "Vim"
                elif "code" in val.lower():
                    ide_name = "VS Code"
                    
    return {
        "ide_name": ide_name,
        "env_vars": ide_vars
    }

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
            
        # Get uncommitted files (modified, untracked, deleted, etc.)
        status_res = subprocess.run(
            ["git", "-C", cwd, "status", "--porcelain"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            check=False,
            timeout=5
        )
        if status_res.returncode == 0:
            uncommitted = []
            for line in status_res.stdout.splitlines():
                if line.strip():
                    uncommitted.append(line.strip())
            result["uncommitted_files"] = uncommitted
            
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
