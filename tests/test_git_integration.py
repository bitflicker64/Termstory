import os
import subprocess
from termstory.git_integration import clean_commit_message, is_git_repo, get_project_commits

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

from unittest.mock import patch
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
