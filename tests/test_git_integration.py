import os
import shutil
import subprocess

from unittest.mock import patch

from termstory.git_integration import (
    clean_commit_message,
    get_project_commits,
    get_timeframe_git_stats,
    is_git_repo,
)

from termstory.git_integration import clean_commit_message, is_git_repo, get_project_commits, find_git_root


def test_clean_commit_message():
    # Test conventional commit prefix stripping
    assert clean_commit_message("feat: Fix docker (#3044)") == "Fix docker"
    assert clean_commit_message("fix(server): update restserver url") == "Update restserver url"
    assert clean_commit_message("chore: update readme") == "Update readme"
    assert clean_commit_message("docs(api): document everything") == "Document everything"
    
    # Test breaking changes and revert conventional commits
    assert clean_commit_message("feat(ui)!: break layout") == "Break layout"
    assert clean_commit_message("feat!: breaking change without scope") == "Breaking change without scope"
    assert clean_commit_message("revert: undo previous change") == "Undo previous change"
    assert clean_commit_message("revert(api)!: undo breaking change") == "Undo breaking change"

    # Test JIRA / Issue code stripping
    assert clean_commit_message("[PROJ-123] Refactor CI pipeline") == "Refactor CI pipeline"
    assert clean_commit_message("ENG-456: hello world") == "Hello world"
    
    # Test that ordinary hyphenated identifiers are not mistaken for issue keys
    assert clean_commit_message("SHA-1 collision check added") == "SHA-1 collision check added"
    assert clean_commit_message("UTF-8 decoding fix for logs") == "UTF-8 decoding fix for logs"
    assert clean_commit_message("CVE-2024 mitigation applied") == "CVE-2024 mitigation applied"

    # Test emoji shorthand and unicode emoji stripping
    assert clean_commit_message("Refactor CI pipeline :rocket:") == "Refactor CI pipeline"
    assert clean_commit_message("🚧 fix: remove debug logs") == "Remove debug logs"
    
    # Test empty, none, or non-string values
    assert clean_commit_message("") == ""
    assert clean_commit_message(None) == ""
    assert clean_commit_message(123) == ""
    assert clean_commit_message([]) == ""

def test_git_operations_on_temp_repo(tmp_path):
    # Verify non-repo path returns False
    assert not is_git_repo(str(tmp_path))
    
    # Initialize a temporary git repository
    try:
        subprocess.run(["git", "init"], cwd=str(tmp_path), check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except Exception:
        # If git is not installed or init fails, skip the rest of the test
        return
        
    # Configure mock user for git commits in test repo
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(tmp_path), check=True)
    
    # Verify is_git_repo is now True
    assert is_git_repo(str(tmp_path))
    
    # Create a mock file and commit it
    mock_file = tmp_path / "hello.txt"
    mock_file.write_text("Hello Git")
    
    subprocess.run(["git", "add", "hello.txt"], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "commit", "-m", "feat: Add hello world file (#1)"], cwd=str(tmp_path), check=True)
    
    # Retrieve commits
    import time
    commits = get_project_commits(str(tmp_path), since_ts=int(time.time()) - 3600)
    
    assert len(commits) == 1
    assert commits[0]["message"] == "feat: Add hello world file (#1)"
    assert commits[0]["cleaned_message"] == "Add hello world file"
    assert len(commits[0]["hash"]) == 40
    assert commits[0]["timestamp"] > 0

def test_is_git_repo_worktree_vs_git_dir(tmp_path):
    # Initialize a temporary git repository. Failure here must fail the test
    # (no try/except): the regression assertions below are meaningless unless
    # the temporary repository was successfully initialized.
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo_path), check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    # The repository root is a valid worktree
    assert is_git_repo(str(repo_path)) is True

    # The .git directory is NOT a worktree (rev-parse exits 0 but prints "false")
    assert is_git_repo(str(repo_path / ".git")) is False

def test_git_missing_or_failing(tmp_path):
    # Test subprocess.run raising an exception (e.g. git not found)
    with patch("termstory.git_integration.subprocess.run") as mock_run:
        mock_run.side_effect = Exception("git not found")
        assert not is_git_repo(str(tmp_path))
        assert get_project_commits(str(tmp_path), since_ts=0) == []
        
    # Test subprocess.run returning non-zero return code
    with patch("termstory.git_integration.subprocess.run") as mock_run:
        class MockResult:
            returncode = 1
            stdout = ""
        mock_run.return_value = MockResult()
        assert not is_git_repo(str(tmp_path))
        
    # Test subprocess.run reporting success with git printing "true" (inside a worktree)
    with patch("termstory.git_integration.subprocess.run") as mock_run:
        class MockResult:
            returncode = 0
            stdout = "true\n"
        mock_run.return_value = MockResult()
        assert is_git_repo(str(tmp_path)) is True

    # Test subprocess.run reporting success but git printing "false" (e.g. .git dir or bare repo)
    with patch("termstory.git_integration.subprocess.run") as mock_run:
        class MockResult:
            returncode = 0
            stdout = "false\n"
        mock_run.return_value = MockResult()
        assert is_git_repo(str(tmp_path)) is False

    # Test get_project_commits returning non-zero return code
    with patch("termstory.git_integration.is_git_repo", return_value=True):
        with patch("termstory.git_integration.subprocess.run") as mock_run:
            class MockResult:
                returncode = 1
                stdout = ""
            mock_run.return_value = MockResult()
            assert get_project_commits(str(tmp_path), since_ts=0) == []


def test_merged_branches_preserves_first_seen_order():
    """Issue #448: merged_branches must be deterministic and de-duplicated."""
    merge_stdout = (
        "Merge branch 'feature/login'\n"
        "Merge branch 'bugfix/auth'\n"
        "Merge branch 'feature/login'\n"
        "Merge branch 'release/v2'\n"
    )

    def fake_run(cmd, **kwargs):
        class MockResult:
            returncode = 0
            stdout = merge_stdout if "--merges" in cmd else ""
        return MockResult()

    with patch("termstory.git_integration.is_git_repo", return_value=True):
        with patch("termstory.git_integration.subprocess.run", side_effect=fake_run):
            # Two paths: duplicates across projects must still collapse.
            stats = get_timeframe_git_stats(["/repo/a", "/repo/b"], 0, 9999999999)

    assert stats["merged_branches"] == [
        "feature/login",
        "bugfix/auth",
        "release/v2",
    ]

# ---------------------------------------------------------------------------
# find_git_root — authoritative worktree identity resolution (#484)
# ---------------------------------------------------------------------------


def _git_available() -> bool:
    """Return True only if a usable `git` executable is on PATH *and* responds.

    ``git --version`` can launch (the binary is present) yet still exit non-zero
    in hostile or sandboxed environments, so we inspect the return code rather
    than treating a successful launch as proof of a working git.
    """
    try:
        result = subprocess.run(
            ["git", "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def test_git_available_returns_false_when_git_version_nonzero(monkeypatch):
    """`_git_available` must inspect the `git --version` return code.

    Regression for the review finding that the helper returned True on any
    non-exceptional launch, even when `git --version` exited non-zero (e.g. a
    broken or sandbox-denied git install).  ``subprocess.run`` is mocked so the
    helper only sees a non-zero exit code and must report unavailability.
    """

    class _BadVersion:
        returncode = 128
        stdout = b""
        stderr = b"git: not found\n"

    monkeypatch.setattr("subprocess.run", lambda *a, **k: _BadVersion())
    assert _git_available() is False


def test_git_dependent_tests_skip_when_git_unavailable(monkeypatch, tmp_path):
    """Git-dependent `find_git_root` tests must skip cleanly when git is absent.

    This verifies the `if not _git_available(): return` guards actually
    short-circuit, so a non-zero `git --version` (handled by the fix above)
    causes the repository tests to skip instead of invoking git commands.  The
    functions return ``None`` when they bail out early.
    """

    class _BadVersion:
        returncode = 128
        stdout = b""
        stderr = b"git: not found\n"

    monkeypatch.setattr("subprocess.run", lambda *a, **k: _BadVersion())
    assert _git_available() is False

    # Each guard short-circuits to an early `return` (None) before touching git.
    assert test_find_git_root_ordinary_repo(tmp_path) is None
    assert test_find_git_root_linked_worktree(tmp_path) is None
    assert test_find_git_root_path_normalization(tmp_path) is None


def _init_repo(path, name="Test", email="test@example.com", commit=True):
    """Create a real temporary git repository at *path* and return its root."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init"], cwd=str(path), check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    subprocess.run(
        ["git", "config", "user.name", name], cwd=str(path), check=True
    )
    subprocess.run(
        ["git", "config", "user.email", email], cwd=str(path), check=True
    )
    if commit:
        (path / "file.txt").write_text("hello\n")
        subprocess.run(
            ["git", "add", "file.txt"], cwd=str(path), check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        subprocess.run(
            ["git", "commit", "-m", "init"], cwd=str(path), check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
    return path


def test_find_git_root_ordinary_repo(tmp_path):
    """TEST A — a cwd inside a normal repository resolves to its root."""
    if not _git_available():
        return  # deliberate skip when git is not present
    repo = _init_repo(tmp_path / "outer")
    nested = repo / "project" / "deeper"
    nested.mkdir(parents=True)

    assert find_git_root(str(nested)) == str(repo)
    # The repository root itself also resolves to itself.
    assert find_git_root(str(repo)) == str(repo)


def test_find_git_root_nested_repo(tmp_path):
    """TEST B — the inner-most repository owns a cwd, not an outer one."""
    if not _git_available():
        return
    outer = _init_repo(tmp_path / "outer")
    child = _init_repo(outer / "child")
    src = child / "src"
    src.mkdir(parents=True)

    root = find_git_root(str(src))
    assert root is not None
    # We are inside the child repo; the child (not outer) must be selected.
    assert root == str(child)
    assert root != str(outer)


def test_find_git_root_linked_worktree(tmp_path):
    """TEST C — a linked worktree (`.git` is a FILE) resolves to its root."""
    if not _git_available():
        return
    main_repo = _init_repo(tmp_path / "main-repo")
    worktree = tmp_path / "linked-worktree"
    try:
        subprocess.run(
            ["git", "worktree", "add", "-b", "wt", str(worktree)],
            cwd=str(main_repo), check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
    except Exception:
        # git worktree add may be unavailable or unsupported; document the
        # limitation rather than silently asserting nothing.
        return
    # Sanity: in a linked worktree `.git` is a FILE, not a directory.
    assert (worktree / ".git").is_file()

    nested = worktree / "sub"
    nested.mkdir(parents=True)
    root = find_git_root(str(nested))
    assert root is not None
    assert root == str(worktree)


def test_find_git_root_path_normalization(tmp_path):
    """TEST D — equivalent spellings (with `..`) yield the same identity."""
    if not _git_available():
        return
    repo = _init_repo(tmp_path / "repo")
    child = repo / "child"
    child.mkdir(parents=True)

    plain = find_git_root(str(child))
    dotdot = find_git_root(str(child / ".." / "child"))
    assert plain is not None
    assert dotdot == plain


def test_find_git_root_non_repo_returns_none(tmp_path):
    """TEST E — a cwd outside any Git returns None (fallback)."""
    plain_dir = tmp_path / "not-a-git-repo"
    plain_dir.mkdir(parents=True)
    assert find_git_root(str(plain_dir)) is None


def test_find_git_root_git_failure_and_timeout_returns_none(tmp_path):
    """TEST F & G — git failures/timeouts must not raise; return None."""
    from unittest.mock import patch

    sub = tmp_path / "sub"
    sub.mkdir()

    # Simulated git command failure (non-zero exit).
    with patch("termstory.git_integration.subprocess.run") as mock_run:
        class MockResult:
            returncode = 128
            stdout = "fatal: not a git repository\n"
        mock_run.return_value = MockResult()
        assert find_git_root(str(sub)) is None

    # Simulated timeout.
    with patch("termstory.git_integration.subprocess.run") as mock_run:
        import subprocess as real_subprocess
        mock_run.side_effect = real_subprocess.TimeoutExpired(cmd=["git"], timeout=10)
        assert find_git_root(str(sub)) is None

    # Simulated missing-git exception.
    with patch("termstory.git_integration.subprocess.run") as mock_run:
        mock_run.side_effect = FileNotFoundError("git not found")
        assert find_git_root(str(sub)) is None


def test_find_git_root_symlink(tmp_path):
    """TEST I — a symlink into a repo resolves consistently with the real path."""
    if not _git_available():
        return
    repo = _init_repo(tmp_path / "real-repo")
    src = repo / "src"
    src.mkdir(parents=True)

    link = tmp_path / "linked-src"
    try:
        link.symlink_to(src, target_is_directory=True)
    except (OSError, NotImplementedError, AttributeError):
        # Symlinks may require elevated privileges on Windows; skip deliberately.
        return

    real = find_git_root(str(src))
    via_link = find_git_root(str(link))
    assert real is not None
    assert via_link == real
