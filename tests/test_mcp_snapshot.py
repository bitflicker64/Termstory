import os
import tempfile
import json
import sqlite3
from unittest.mock import patch, MagicMock
import pytest

from termstory.database import Database
from termstory.models import Project, Session, Command
from termstory.mcp_snapshot import (
    capture_ide_state,
    capture_git_status,
    capture_mcp_snapshot,
    capture_and_store_mcp_snapshot
)
from termstory.formatter import format_mcp_snapshots


def test_capture_ide_state():
    # Test with no IDE variables set
    with patch.dict(os.environ, {}, clear=True):
        state = capture_ide_state()
        assert state["ide_name"] == "Unknown"
        assert len(state["env_vars"]) == 0

    # Test with VS Code environment variables
    with patch.dict(os.environ, {"TERM_PROGRAM": "vscode", "VSCODE_GIT_IPC_HANDLE": "1234"}, clear=True):
        state = capture_ide_state()
        assert state["ide_name"] == "VS Code"
        assert state["env_vars"]["TERM_PROGRAM"] == "vscode"
        assert state["env_vars"]["VSCODE_GIT_IPC_HANDLE"] == "1234"

    # Test with Cursor environment variables
    with patch.dict(os.environ, {"TERM_PROGRAM": "Cursor", "CURSOR_PID": "5678"}, clear=True):
        state = capture_ide_state()
        assert state["ide_name"] == "Cursor"
        assert state["env_vars"]["TERM_PROGRAM"] == "Cursor"
        assert state["env_vars"]["CURSOR_PID"] == "5678"

    # Test with Neovim environment variables
    with patch.dict(os.environ, {"EDITOR": "nvim"}, clear=True):
        state = capture_ide_state()
        assert state["ide_name"] == "Neovim"
        assert state["env_vars"]["EDITOR"] == "nvim"


def test_capture_git_status():
    # Test with non-existent directory
    status = capture_git_status("/non/existent/path")
    assert not status["is_repo"]
    assert status["branch"] is None
    assert len(status["uncommitted_files"]) == 0


def test_mcp_snapshots_database_integration():
    # Create temp DB
    temp_fd, temp_path = tempfile.mkstemp()
    os.close(temp_fd)
    
    try:
        db = Database(temp_path)
        db.init_db()
        
        # Save a dummy session to reference
        conn = db.get_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO sessions (start_time, end_time, duration_seconds) VALUES (1000, 2000, 1000)")
        session_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        # Save MCP snapshot
        payload = {"cwd": "/test/cwd", "ide": {"ide_name": "VS Code"}, "git": {"is_repo": True, "branch": "main"}}
        db.save_mcp_snapshot(session_id, "cli", payload, 1500)
        
        # Retrieve and verify
        snapshots = db.get_mcp_snapshots(session_id)
        assert len(snapshots) == 1
        assert snapshots[0]["source"] == "cli"
        assert snapshots[0]["payload"] == payload
        assert snapshots[0]["captured_at"] == 1500
        
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def test_format_mcp_snapshots():
    snapshots = [
        {
            "captured_at": 1781568000,  # some date
            "source": "cli",
            "payload": {
                "cwd": "/Users/developer/termstory",
                "ide": {
                    "ide_name": "VS Code",
                    "env_vars": {"TERM_PROGRAM": "vscode"}
                },
                "git": {
                    "is_repo": True,
                    "branch": "feat/mcp",
                    "uncommitted_files": ["M termstory/cli.py", "?? tests/test_mcp.py"]
                }
            }
        }
    ]
    
    output = format_mcp_snapshots(snapshots)
    assert "MCP Workspace Snapshots" in output
    assert "/Users/developer/termstory" in output
    assert "VS Code" in output
    assert "feat/mcp" in output
    assert "termstory/cli.py" in output
    
    # Test empty snapshot handling
    empty_output = format_mcp_snapshots([])
    assert "No MCP snapshots captured" in empty_output


@patch("termstory.mcp_snapshot.os.getcwd", return_value="/Users/developer/termstory")
@patch("termstory.mcp_snapshot.capture_ide_state", return_value={"ide_name": "VS Code", "env_vars": {}})
@patch("termstory.mcp_snapshot.capture_git_status", return_value={"is_repo": True, "branch": "main", "uncommitted_files": []})
def test_capture_and_store_mcp_snapshot(mock_git, mock_ide, mock_cwd):
    temp_fd, temp_path = tempfile.mkstemp()
    os.close(temp_fd)
    
    try:
        db = Database(temp_path)
        db.init_db()
        
        # Save a dummy session to reference
        conn = db.get_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO sessions (start_time, end_time, duration_seconds) VALUES (1000, 2000, 1000)")
        session_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        # Run capture_and_store_mcp_snapshot
        capture_and_store_mcp_snapshot(db)
        
        # Verify it was stored
        snapshots = db.get_mcp_snapshots(session_id)
        assert len(snapshots) == 1
        assert snapshots[0]["payload"]["cwd"] == "/Users/developer/termstory"
        assert snapshots[0]["payload"]["ide"]["ide_name"] == "VS Code"
        assert snapshots[0]["payload"]["git"]["branch"] == "main"
        
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
