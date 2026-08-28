import os
import tempfile
import json
import sqlite3
import subprocess
from unittest.mock import patch, MagicMock
import pytest

from termstory.database import Database
from termstory.models import Project, Session, Command
from termstory.mcp_snapshot import (
    capture_ide_state,
    capture_git_status,
    capture_mcp_snapshot,
    capture_and_store_mcp_snapshot,
    _scrub_cwd
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
        # The value of an IDE-prefixed variable must NOT be persisted.
        assert "VSCODE_GIT_IPC_HANDLE" not in state["env_vars"]

    # Test with Cursor environment variables
    with patch.dict(os.environ, {"TERM_PROGRAM": "Cursor", "CURSOR_PID": "5678"}, clear=True):
        state = capture_ide_state()
        assert state["ide_name"] == "Cursor"
        assert state["env_vars"]["TERM_PROGRAM"] == "Cursor"
        # The value of an IDE-prefixed variable must NOT be persisted.
        assert "CURSOR_PID" not in state["env_vars"]

    # Test with Neovim environment variables
    with patch.dict(os.environ, {"EDITOR": "nvim"}, clear=True):
        state = capture_ide_state()
        assert state["ide_name"] == "Neovim"
        assert state["env_vars"]["EDITOR"] == "nvim"


def test_capture_ide_state_does_not_persist_sensitive_values():
    # VS Code: socket handles and askpass helper paths must not leak.
    vscode_env = {
        "VSCODE_IPC_HOOK_CLI": "/tmp/vscode-ipc-user-123.sock",
        "VSCODE_GIT_ASKPASS_NODE": "/home/user/.vscode/extensions/example/node",
        "VSCODE_GIT_ASKPASS_MAIN": "/home/user/.vscode/extensions/example/main.js",
        "VSCODE_GIT_IPC_HANDLE": "/tmp/vscode-git-abc.sock",
    }
    with patch.dict(os.environ, vscode_env, clear=True):
        state = capture_ide_state()
        assert state["ide_name"] == "VS Code"
        assert state["env_vars"] == {}
        payload = json.dumps(state)
        assert "/tmp/vscode-ipc-user-123.sock" not in payload
        assert "/home/user/.vscode/extensions" not in payload
        assert "vscode-ipc-user" not in payload

    # Neovim: the session socket path must not persist.
    with patch.dict(os.environ, {"NVIM_LISTEN_ADDRESS": "/tmp/nvim-user/session.sock"}, clear=True):
        state = capture_ide_state()
        assert state["ide_name"] == "Neovim"
        assert state["env_vars"] == {}
        assert "/tmp/nvim-user/session.sock" not in json.dumps(state)

    # JetBrains: representative variable value must not reach the payload.
    jetbrains_env = {
        "JETBRAINS_GATEWAY_CMD": "/home/user/.local/share/JetBrains/RemoteDev/session/socket",
        "IDEA_INITIAL_DIRECTORY": "/home/user/IdeaProjects/myproj",
    }
    with patch.dict(os.environ, jetbrains_env, clear=True):
        state = capture_ide_state()
        assert state["ide_name"] == "JetBrains"
        assert state["env_vars"] == {}
        payload = json.dumps(state)
        assert "/home/user/.local/share/JetBrains" not in payload
        assert "/home/user/IdeaProjects/myproj" not in payload


def test_capture_ide_state_detection_signals():
    # Detection via IDE-prefixed variable presence only.
    with patch.dict(os.environ, {"XCODE_DEVELOPER_DIR": "/Applications/Xcode.app"}, clear=True):
        assert capture_ide_state()["ide_name"] == "Xcode"
    with patch.dict(os.environ, {"CURSOR_TRACE_ID": "abc123"}, clear=True):
        assert capture_ide_state()["ide_name"] == "Cursor"
    with patch.dict(os.environ, {"IDEA_JDK": "/opt/jdk"}, clear=True):
        assert capture_ide_state()["ide_name"] == "JetBrains"
    with patch.dict(os.environ, {"NVIM_APPNAME": "nvim"}, clear=True):
        assert capture_ide_state()["ide_name"] == "Neovim"

    # Detection via EDITOR / VISUAL values (Vim + VS Code).
    with patch.dict(os.environ, {"VISUAL": "/usr/bin/vim"}, clear=True):
        state = capture_ide_state()
        assert state["ide_name"] == "Vim"
        # Only the command name is retained, not the path.
        assert state["env_vars"]["VISUAL"] == "vim"
    with patch.dict(os.environ, {"EDITOR": "/usr/local/bin/code --wait"}, clear=True):
        state = capture_ide_state()
        assert state["ide_name"] == "VS Code"
        assert state["env_vars"]["EDITOR"] == "code"


def test_capture_git_status():
    # Test with non-existent directory
    status = capture_git_status("/non/existent/path")
    assert not status["is_repo"]
    assert status["branch"] is None
    assert len(status["uncommitted_files"]) == 0


def _git_porcelain_completed_process(args, stdout):
    return subprocess.CompletedProcess(args, 0, stdout, "")


def _git_status_side_effect(*args, **kwargs):
    cmd_args = args[0]
    if "--is-inside-work-tree" in cmd_args:
        return _git_porcelain_completed_process(cmd_args, "true\n")
    if "HEAD" in cmd_args:
        return _git_porcelain_completed_process(cmd_args, "main\n")
    raise AssertionError(f"Unexpected git args: {cmd_args}")

def _git_status_popen_side_effect(*args, **kwargs):
    cmd_args = args[0]
    if "--porcelain" in cmd_args:
        # The status invocation must request the bounded untracked-directory
        # behavior that prevents recursive scanning of huge untracked trees.
        assert "--untracked-files=normal" in cmd_args, cmd_args
        mock_proc = MagicMock()
        mock_proc.stdout = [line + "\n" for line in _GIT_STATUS_STDOUT.split("\n")]
        mock_proc.returncode = 0
        return mock_proc
    raise AssertionError(f"Unexpected git args for Popen: {cmd_args}")


_GIT_STATUS_STDOUT = "\n".join([
    " M termstory/cli.py",
    "?? /home/user/private_notes/secret.txt",
    "D  /home/user/.config/termstory/backup.db",
    "R  /home/user/docs/old.txt -> /home/user/docs/new.txt",
    "",
])


def test_capture_git_status_sanitizes_uncommitted_files():
    with patch.dict(os.environ, {"HOME": "/home/user", "USERNAME": "user"}, clear=False), \
         patch("termstory.mcp_snapshot.os.path.exists", return_value=True), \
         patch("termstory.mcp_snapshot.os.path.isdir", return_value=True), \
         patch("termstory.mcp_snapshot.subprocess.run", side_effect=_git_status_side_effect), \
         patch("termstory.mcp_snapshot.subprocess.Popen", side_effect=_git_status_popen_side_effect):
        status = capture_git_status("/home/user/project")

    assert status["is_repo"] is True
    assert status["branch"] == "main"

    raw_lines = [
        "?? /home/user/private_notes/secret.txt",
        "D  /home/user/.config/termstory/backup.db",
        "R  /home/user/docs/old.txt -> /home/user/docs/new.txt",
    ]
    joined = "\n".join(status["uncommitted_files"])
    for raw in raw_lines:
        assert raw not in status["uncommitted_files"]
        assert raw not in joined
    # The locally-identifying home-directory / username component is gone.
    assert "/home/user" not in joined

    # Non-sensitive relative paths must be preserved for consumers.
    assert any("termstory/cli.py" in item for item in status["uncommitted_files"])


def test_capture_and_store_mcp_snapshot_no_sensitive_data_persisted():
    db = MagicMock()
    db.get_latest_session_id.return_value = 1
    db.get_mcp_snapshots.return_value = []

    env = {
        "TERM_PROGRAM": "vscode",
        "VSCODE_IPC_HOOK_CLI": "/tmp/vscode-ipc-user-123.sock",
        "VSCODE_GIT_ASKPASS_NODE": "/home/user/.vscode/extensions/example/node",
        "NVIM_LISTEN_ADDRESS": "/tmp/nvim-user/session.sock",
        "HOME": "/home/user",
        "USERNAME": "user",
    }
    with patch.dict(os.environ, env, clear=True), \
         patch("termstory.mcp_snapshot.os.getcwd", return_value="/home/user/project"), \
         patch("termstory.mcp_snapshot.os.path.exists", return_value=True), \
         patch("termstory.mcp_snapshot.os.path.isdir", return_value=True), \
         patch("termstory.mcp_snapshot.subprocess.run", side_effect=_git_status_side_effect), \
         patch("termstory.mcp_snapshot.subprocess.Popen", side_effect=_git_status_popen_side_effect):
        capture_and_store_mcp_snapshot(db)

    assert db.save_mcp_snapshot.called
    payload = db.save_mcp_snapshot.call_args.kwargs.get("payload")
    if payload is None:
        payload = db.save_mcp_snapshot.call_args.args[2]
    payload_text = json.dumps(payload)

    # No raw sensitive environment values may reach the persisted payload.
    assert "/tmp/vscode-ipc-user-123.sock" not in payload_text
    assert "/home/user/.vscode/extensions" not in payload_text
    assert "/tmp/nvim-user/session.sock" not in payload_text

    # IDE detection must still work.
    assert payload["ide"]["ide_name"] == "VS Code"

    # No raw git-status path may reach the persisted payload.
    git_files = json.dumps(payload["git"]["uncommitted_files"])
    assert "/home/user" not in git_files
    assert "?? /home/user/private_notes/secret.txt" not in git_files


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


def test_capture_mcp_snapshot_deleted_cwd():
    with patch("termstory.mcp_snapshot.os.getcwd", side_effect=FileNotFoundError("No such file or directory")):
        snapshot = capture_mcp_snapshot()
        assert snapshot["cwd"] is None
        assert not snapshot["git"]["is_repo"]


# ---------------------------------------------------------------------------
# Issue #477 regressions: the persisted snapshot "cwd" must be scrubbed of
# locally-identifying home/username path components. All identity inputs
# (HOME / USERPROFILE / USERNAME / expanduser / getpass.getuser) are pinned in
# every test so results never depend on the developer machine's real values.
# ---------------------------------------------------------------------------

_ALICE_UNIX_ENV = {
    "HOME": "/home/alice",
    "USERPROFILE": "/home/alice",
    "USERNAME": "alice",
    "USER": "alice",
}

_ALICE_WIN_ENV = {
    "HOME": "C:\\Users\\alice",
    "USERPROFILE": "C:\\Users\\alice",
    "USERNAME": "alice",
    "USER": "alice",
}


def test_scrub_cwd_none_preserved():
    # A deleted cwd (os.getcwd() raising OSError) yields cwd=None upstream;
    # the sanitizer must preserve None rather than crash or coerce it.
    assert _scrub_cwd(None) is None


def test_scrub_cwd_unix_home_and_username_components():
    with patch.dict(os.environ, _ALICE_UNIX_ENV, clear=True), \
         patch("termstory.mcp_snapshot.os.path.expanduser", return_value="/home/alice"), \
         patch("termstory.mcp_snapshot.getpass.getuser", return_value="alice"):
        # Home is redacted on component boundaries; trailing project context
        # is preserved.
        assert _scrub_cwd("/home/alice/projects/Termstory") == "<REDACTED_HOME>/projects/Termstory"
        # The exact home path itself collapses to the placeholder.
        assert _scrub_cwd("/home/alice") == "<REDACTED_HOME>"
        # A username appearing as a nested (non-home-prefix) path component is
        # still redacted...
        assert _scrub_cwd("/srv/workspaces/alice/notes") == "/srv/workspaces/<REDACTED_USER>/notes"
        # ...as is a macOS-style home not advertised via HOME.
        assert _scrub_cwd("/Users/alice/termstory") == "/Users/<REDACTED_USER>/termstory"
        # A component merely containing the username as a substring must NOT
        # be corrupted.
        assert _scrub_cwd("/srv/alicebackup/build") == "/srv/alicebackup/build"
        # A path sharing a prefix with the home but forming a different
        # component must be left intact.
        assert _scrub_cwd("/home/alicee/data") == "/home/alicee/data"


def test_scrub_cwd_windows_home_and_case_insensitive_username():
    # Windows-style paths must be scrubbed regardless of the host OS the tests
    # run on, and casing differences must not bypass redaction.
    with patch.dict(os.environ, _ALICE_WIN_ENV, clear=True), \
         patch("termstory.mcp_snapshot.os.path.expanduser", return_value="C:\\Users\\alice"), \
         patch("termstory.mcp_snapshot.getpass.getuser", return_value="alice"):
        # Windows home prefix is redacted; project context survives.
        assert _scrub_cwd("C:\\Users\\alice\\Documents\\Termstory") == "<REDACTED_HOME>\\Documents\\Termstory"
        # C:\\Users\\Alice (different case) must also be caught.
        assert _scrub_cwd("C:\\Users\\Alice\\Documents\\Termstory") == "<REDACTED_HOME>\\Documents\\Termstory"
        # A POSIX path is still scrubbed even when the advertised home is a
        # Windows path (username component match is OS-independent).
        assert _scrub_cwd("/home/alice/projects/Termstory") == "/home/<REDACTED_USER>/projects/Termstory"
        # Username as a nested Windows path component.
        assert _scrub_cwd("D:\\data\\alice\\repo") == "D:\\data\\<REDACTED_USER>\\repo"
        # Longer components that merely contain the username stay intact.
        assert _scrub_cwd("C:\\Users\\alicebackup\\Termstory") == "C:\\Users\\alicebackup\\Termstory"


def test_scrub_cwd_preserves_paths_without_sensitive_components():
    with patch.dict(os.environ, _ALICE_UNIX_ENV, clear=True), \
         patch("termstory.mcp_snapshot.os.path.expanduser", return_value="/home/alice"), \
         patch("termstory.mcp_snapshot.getpass.getuser", return_value="alice"):
        # Paths with no home/username components must pass through untouched.
        assert _scrub_cwd("/opt/apps/Termstory") == "/opt/apps/Termstory"
        assert _scrub_cwd("C:\\dev\\tools\\Termstory") == "C:\\dev\\tools\\Termstory"
        assert _scrub_cwd("relative/nested/path") == "relative/nested/path"
        assert _scrub_cwd("") == ""


def test_capture_git_status_subprocess_timeout():
    mock_run = MagicMock(side_effect=subprocess.TimeoutExpired(cmd="git", timeout=5))
    with patch("termstory.mcp_snapshot.os.path.exists", return_value=True), \
         patch("termstory.mcp_snapshot.os.path.isdir", return_value=True), \
         patch("termstory.mcp_snapshot.subprocess.run", mock_run):
        status = capture_git_status("/some/path")
        # Guard against the fix regressing back to a vacuous test: the
        # exception handler is only meaningfully exercised if subprocess.run
        # was actually invoked.
        assert mock_run.called
        assert not status["is_repo"]
        assert status["branch"] is None
        assert len(status["uncommitted_files"]) == 0


def test_capture_git_status_bounds_large_untracked_tree():
    # Regression for #431: git status must be invoked with --untracked-files=normal
    # so huge untracked directories (node_modules/, dist/, build/) are collapsed to
    # single entries instead of being recursively scanned, while individual
    # untracked files are still reported. A pathological number of scattered
    # entries must also be truncated rather than producing an unbounded snapshot.
    import termstory.mcp_snapshot as mcp_snapshot
    from unittest import mock

    # Captures the actual git status command arguments used.
    recorded = {}

    def side_effect(cmd, **kwargs):
        if "--porcelain" in cmd:
            recorded["status_cmd"] = cmd
            # A tracked modification first (so it is retained), followed by 1500
            # scattered untracked files that must be capped.
            stdout = "\n".join([" M tracked.py"]
                               + [f"?? gen{i}.txt" for i in range(1500)])
            return subprocess.CompletedProcess(cmd, 0, stdout, "")
        if "--is-inside-work-tree" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "true\n", "")
        if "HEAD" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "main\n", "")
        raise AssertionError(f"Unexpected git args: {cmd}")

    with mock.patch("termstory.mcp_snapshot.os.path.exists", return_value=True), \
         mock.patch("termstory.mcp_snapshot.os.path.isdir", return_value=True), \
         mock.patch("termstory.mcp_snapshot.subprocess.run", side_effect=side_effect):
        status = capture_git_status("/some/path")

    # The status command must request the bounded untracked-directory behavior.
    assert "--untracked-files=normal" in recorded["status_cmd"]
    assert "--porcelain" in recorded["status_cmd"]

    # Normal semantics preserved: the repo/branch are detected and a tracked
    # modification is reported.
    assert status["is_repo"] is True
    assert status["branch"] == "main"
    assert any(" M tracked.py" in f for f in status["uncommitted_files"])

    # Individual untracked files are still reported (not suppressed wholesale).
    # The tracked modification is retained even with the cap (it appears first
    # in the git status output, so the 1000-entry cap keeps it).
    assert any("tracked.py" in f for f in status["uncommitted_files"])
    # ...but the retained list is capped so 1500 entries do not become an
    # unbounded snapshot.
    assert len(status["uncommitted_files"]) == mcp_snapshot._MAX_UNCOMMITTED_FILES



@patch("termstory.mcp_snapshot.os.getcwd", return_value="/Users/developer/termstory")
@patch("termstory.mcp_snapshot.capture_ide_state", return_value={"ide_name": "VS Code", "env_vars": {}})
@patch("termstory.mcp_snapshot.capture_git_status", return_value={"is_repo": True, "branch": "main", "uncommitted_files": []})
def test_capture_and_store_mcp_snapshot_db_error_does_not_raise(mock_git, mock_ide, mock_cwd):
    db = MagicMock()
    db.get_latest_session_id.return_value = 1
    db.get_mcp_snapshots.return_value = []
    db.save_mcp_snapshot.side_effect = sqlite3.OperationalError("database is locked")

    # Must swallow the db failure and return normally. A snapshot
    # failure must never disrupt the core ingestion process.
    capture_and_store_mcp_snapshot(db)


# ---------------------------------------------------------------------------
# Issue #477: capture-level regressions. The REAL, unredacted cwd must still
# reach capture_git_status() for repository detection, while only the value
# persisted in (and displayed from) the snapshot is sanitized.
# ---------------------------------------------------------------------------

_GIT_INFO = {"is_repo": True, "branch": "main", "uncommitted_files": []}


def test_capture_mcp_snapshot_scrubs_cwd_but_git_receives_real_cwd():
    with patch.dict(os.environ, _ALICE_UNIX_ENV, clear=True), \
         patch("termstory.mcp_snapshot.os.path.expanduser", return_value="/home/alice"), \
         patch("termstory.mcp_snapshot.getpass.getuser", return_value="alice"), \
         patch("termstory.mcp_snapshot.os.getcwd", return_value="/home/alice/projects/Termstory"), \
         patch("termstory.mcp_snapshot.capture_git_status", return_value=dict(_GIT_INFO)) as mock_git:
        snapshot = capture_mcp_snapshot()

    cwd = snapshot["cwd"]
    assert cwd is not None
    assert "/home/alice" not in cwd
    assert "alice" not in cwd
    assert "Termstory" in cwd
    # Git repository detection received the REAL, unredacted cwd.
    mock_git.assert_called_once_with("/home/alice/projects/Termstory")
    assert snapshot["git"] == _GIT_INFO


def test_capture_mcp_snapshot_scrubs_windows_cwd_but_git_receives_real_cwd():
    real_cwd = "C:\\Users\\Alice\\Documents\\Termstory"
    with patch.dict(os.environ, _ALICE_WIN_ENV, clear=True), \
         patch("termstory.mcp_snapshot.os.path.expanduser", return_value="C:\\Users\\alice"), \
         patch("termstory.mcp_snapshot.getpass.getuser", return_value="alice"), \
         patch("termstory.mcp_snapshot.os.getcwd", return_value=real_cwd), \
         patch("termstory.mcp_snapshot.capture_git_status", return_value=dict(_GIT_INFO)) as mock_git:
        snapshot = capture_mcp_snapshot()

    cwd = snapshot["cwd"]
    assert cwd is not None
    assert "C:\\Users\\alice" not in cwd
    assert "C:\\Users\\Alice" not in cwd
    assert "alice" not in cwd.lower()
    assert "Termstory" in cwd
    # The REAL, unredacted path (with its original casing) reaches Git.
    mock_git.assert_called_once_with(real_cwd)


def test_capture_and_store_mcp_snapshot_persists_scrubbed_cwd():
    db = MagicMock()
    db.get_latest_session_id.return_value = 1
    db.get_mcp_snapshots.return_value = []

    with patch.dict(os.environ, _ALICE_UNIX_ENV, clear=True), \
         patch("termstory.mcp_snapshot.os.path.expanduser", return_value="/home/alice"), \
         patch("termstory.mcp_snapshot.getpass.getuser", return_value="alice"), \
         patch("termstory.mcp_snapshot.os.getcwd", return_value="/home/alice/projects/Termstory"), \
         patch("termstory.mcp_snapshot.capture_ide_state", return_value={"ide_name": "Unknown", "env_vars": {}}), \
         patch("termstory.mcp_snapshot.capture_git_status", return_value=dict(_GIT_INFO)) as mock_git:
        capture_and_store_mcp_snapshot(db)

    assert db.save_mcp_snapshot.called
    payload = db.save_mcp_snapshot.call_args.kwargs.get("payload")
    if payload is None:
        payload = db.save_mcp_snapshot.call_args.args[2]
    # The persisted cwd is sanitized but keeps the project context.
    assert payload["cwd"] == "<REDACTED_HOME>/projects/Termstory"
    # Neither the home path nor the username may leak anywhere in the payload.
    payload_text = json.dumps(payload)
    assert "/home/alice" not in payload_text
    assert "alice" not in payload_text
    # Git detection still ran against the real directory.
    mock_git.assert_called_once_with("/home/alice/projects/Termstory")


@patch("termstory.mcp_snapshot.os.getcwd", return_value="/Users/developer/termstory")
@patch("termstory.mcp_snapshot.capture_ide_state", return_value={"ide_name": "VS Code", "env_vars": {}})
@patch("termstory.mcp_snapshot.capture_git_status", return_value={"is_repo": True, "branch": "main", "uncommitted_files": []})
def test_capture_and_store_mcp_snapshot_non_serializable_payload_does_not_raise(mock_git, mock_ide, mock_cwd):
    # Regression for a non-serializable payload: Database.save_mcp_snapshot's
    # internal json.dumps(payload) raises TypeError for objects it can't
    # encode, and _safe_rollback_and_reraise re-raises it unchanged,
    # capture_and_store_mcp_snapshot must still swallow it.
    db = MagicMock()
    db.get_latest_session_id.return_value = 1
    db.get_mcp_snapshots.return_value = []
    db.save_mcp_snapshot.side_effect = TypeError("Object of type set is not JSON serializable")

    capture_and_store_mcp_snapshot(db)

def test_capture_git_status_bounds_large_untracked_tree():
    """Verify that capture_git_status limits the number of tracked untracked files
    and sets the truncated flag appropriately."""
    mock_run = MagicMock()
    mock_run.return_value = _git_porcelain_completed_process(["dummy"], "true\n")
    
    mock_branch_run = MagicMock()
    mock_branch_run.return_value = _git_porcelain_completed_process(["dummy"], "main\n")
    
    def run_side_effect(*args, **kwargs):
        if "HEAD" in args[0]:
            return mock_branch_run.return_value
        return mock_run.return_value
        
    mock_proc = MagicMock()
    # Generate 1 modified file and 1500 untracked files
    lines = [" M tracked_file.py\n"] + [f"?? file_{i}.txt\n" for i in range(1500)]
    mock_proc.stdout = lines
    mock_proc.returncode = 0
    
    with patch("termstory.mcp_snapshot.os.path.exists", return_value=True), \
         patch("termstory.mcp_snapshot.os.path.isdir", return_value=True), \
         patch("termstory.mcp_snapshot.subprocess.run", side_effect=run_side_effect), \
         patch("termstory.mcp_snapshot.subprocess.Popen", return_value=mock_proc) as mock_popen:
         
        status = capture_git_status("/some/path")
        
        mock_popen.assert_called_once()
        called_args = mock_popen.call_args[0][0]
        assert "--untracked-files=normal" in called_args
        
        assert len(status["uncommitted_files"]) == 1000
        assert "M tracked_file.py" in status["uncommitted_files"]
        assert status.get("uncommitted_files_truncated") is True
        mock_proc.terminate.assert_called_once()
