from termstory.models import Command, Session, Project
from termstory.project import (
    detect_projects, extract_cd_path, humanize_project_name, 
    disambiguate_project_names, _is_project_indicative_command, _extract_file_args
)

def test_extract_cd_path():
    assert extract_cd_path("cd ~/projects/incubator-hugegraph") == "~/projects/incubator-hugegraph"
    assert extract_cd_path("cd -P /usr/local/bin") == "/usr/local/bin"
    assert extract_cd_path("cd") == "~"
    assert extract_cd_path("cd -- '/Users/test/Spaces Dir'") == "/Users/test/Spaces Dir"
    assert extract_cd_path("ls -l") is None

def test_humanize_project_name():
    assert humanize_project_name("~/projects/incubator-hugegraph") == "Apache HugeGraph"
    assert humanize_project_name("/Users/username/my-awesome-project") == "Awesome Project"
    assert humanize_project_name("~") == "Home"
    assert humanize_project_name("/") == "Home"
    assert humanize_project_name("/some/nested/directory-name_here") == "Directory Name Here"
    
    # New V2 rules
    assert humanize_project_name("learning-k8s") == "Kubernetes"
    assert humanize_project_name("test-tf-cli") == "Terraform CLI"
    assert humanize_project_name("my-sqlite-db") == "Sqlite Database"

def test_disambiguate_project_names():
    p1 = Project(id=1, name="HugeGraph", path="/home/user/projects/hugegraph", first_seen=0, last_seen=0, session_count=1, total_time=1)
    p2 = Project(id=2, name="HugeGraph", path="/home/user/personal/hugegraph", first_seen=0, last_seen=0, session_count=1, total_time=1)
    p3 = Project(id=3, name="Other", path="/home/user/projects/other", first_seen=0, last_seen=0, session_count=1, total_time=1)
    
    names = disambiguate_project_names([p1, p2, p3])
    
    assert names[1] == "HugeGraph (/home/user/projects)"
    assert names[2] == "HugeGraph (/home/user/personal)"
    assert names[3] == "Other" # Unchanged as it's unique

def test_detect_projects(monkeypatch):
    import os
    original_listdir = os.listdir
    def mock_listdir(path):
        if path == "/Users/username/my-awesome-project":
            return [".git"]
        return original_listdir(path)
    monkeypatch.setattr(os, "listdir", mock_listdir)

    # Session 1: working in project A
    cmd1 = Command(timestamp=1000, command="cd ~/projects/incubator-hugegraph")
    cmd2 = Command(timestamp=1010, command="git status")
    s1 = Session(id=1, start_time=1000, end_time=1010, duration_seconds=10, project_id=None, commands=[cmd1, cmd2])
    
    # Session 2: working in project B
    cmd3 = Command(timestamp=2000, command="cd /Users/username/my-awesome-project")
    cmd4 = Command(timestamp=2020, command="python setup.py install")
    s2 = Session(id=2, start_time=2000, end_time=2020, duration_seconds=20, project_id=None, commands=[cmd3, cmd4])
    
    # Session 3: no cd commands
    cmd5 = Command(timestamp=3000, command="echo 'no projects here'")
    s3 = Session(id=3, start_time=3000, end_time=3000, duration_seconds=0, project_id=None, commands=[cmd5])
    
    projects = detect_projects([s1, s2, s3])
    
    # We should have exactly 2 projects detected
    assert len(projects) == 2
    
    # Verify Project A details
    proj_a = next(p for p in projects if "HugeGraph" in p.name)
    assert proj_a.path == "~/projects/incubator-hugegraph"
    assert proj_a.name == "Apache HugeGraph"
    assert s1.project_id == proj_a.id
    assert cmd1.project_id == proj_a.id
    assert cmd2.project_id == proj_a.id
    
    # Verify Project B details
    proj_b = next(p for p in projects if "Awesome" in p.name)
    assert proj_b.path == "/Users/username/my-awesome-project"
    assert s2.project_id == proj_b.id
    
    # Session 3 inherits Project B because the simulated cwd persists
    assert s3.project_id == proj_b.id
    assert cmd5.project_id == proj_b.id

def test_find_project_root(tmp_path, monkeypatch):
    monkeypatch.setattr("os.path.expanduser", lambda path: str(tmp_path) if path == "~" else path)
    
    # 1. Create a directory structure with git root
    proj_dir = tmp_path / "Projects" / "my-awesome-repo"
    sub_dir = proj_dir / "subfolder" / "deep-nested"
    sub_dir.mkdir(parents=True)
    
    # Create a git marker
    git_dir = proj_dir / ".git"
    git_dir.mkdir()
    
    # Verify resolving sub_dir root finds the repo root
    from termstory.project import find_project_root
    assert find_project_root(str(sub_dir)) == str(proj_dir)
    
    # 2. Test common project marker file (e.g. package.json)
    package_dir = tmp_path / "Projects" / "node-project"
    nested_node = package_dir / "src" / "components"
    nested_node.mkdir(parents=True)
    
    package_json = package_dir / "package.json"
    package_json.touch()
    
    assert find_project_root(str(nested_node)) == str(package_dir)
    
    # 3. Test fallback with no markers under known Projects path
    fallback_dir = tmp_path / "Projects" / "fallback-project" / "sub" / "dir"
    fallback_dir.mkdir(parents=True)
    assert find_project_root(str(fallback_dir)) == str(tmp_path / "Projects" / "fallback-project")

    # 4. Test fallback to home when not under Projects and no markers exist
    other_dir = tmp_path / "Downloads" / "some-random-folder"
    other_dir.mkdir(parents=True)
    assert find_project_root(str(other_dir)) == str(tmp_path)

def test_find_project_root_symlink_escape(tmp_path, monkeypatch):
    monkeypatch.setattr("os.path.expanduser", lambda path: str(tmp_path) if path == "~" else path)
    
    import os
    # 1. Create a dummy home directory
    home_dir = tmp_path
    
    # 2. Create an external directory (outside home)
    external_dir = home_dir.parent / "external-storage"
    external_dir.mkdir(exist_ok=True)
    
    external_proj = external_dir / "my-escaped-project"
    external_proj.mkdir(exist_ok=True)
    (external_proj / ".git").mkdir(exist_ok=True)
    
    # 3. Create a symlink inside home pointing to the external directory
    symlink_dir = home_dir / "my_symlink"
    if not symlink_dir.exists():
        os.symlink(str(external_proj), str(symlink_dir))
    
    # 4. Check that find_project_root on the symlink follows the escape
    # and correctly identifies the external project.
    from termstory.project import find_project_root
    assert find_project_root(str(symlink_dir)) == str(external_proj)
    
    # Cleanup symlink
    if symlink_dir.exists():
        symlink_dir.unlink()


# ── New tests for Pass 2 & Pass 3 ────────────────────────────────────

def test_is_project_indicative_command():
    """Test that project-indicative commands are correctly identified."""
    assert _is_project_indicative_command("git commit -m 'fix bug'") == True
    assert _is_project_indicative_command("git push origin main") == True
    assert _is_project_indicative_command("npm run dev") == True
    assert _is_project_indicative_command("cargo build") == True
    assert _is_project_indicative_command("python manage.py runserver") == True
    assert _is_project_indicative_command("pytest") == True
    assert _is_project_indicative_command("make") == True
    
    # Non-indicative commands
    assert _is_project_indicative_command("ls -la") == False
    assert _is_project_indicative_command("echo hello") == False
    assert _is_project_indicative_command("cd ~/Projects") == False
    assert _is_project_indicative_command("cat file.txt") == False


def test_extract_file_args():
    """Test file argument extraction from commands."""
    assert "src/app.py" in _extract_file_args("vim src/app.py")
    assert "setup.py" in _extract_file_args("python setup.py install")
    assert len(_extract_file_args("git status")) == 0
    # URLs and env vars should be skipped
    assert len(_extract_file_args("curl https://example.com")) == 0
    assert len(_extract_file_args("echo $HOME")) == 0


def test_neighbor_propagation_sandwich():
    """Pass 3: 'Other' session sandwiched between two sessions of the same project gets assigned."""
    # Session 1: known project (via cd)
    s1 = Session(id=1, start_time=1000, end_time=1100, duration_seconds=100, project_id=None,
                 commands=[Command(timestamp=1000, command="cd ~/projects/incubator-hugegraph"),
                           Command(timestamp=1050, command="git status")])
    
    # Session 2: no cd, no indicative commands
    s2 = Session(id=2, start_time=2000, end_time=2100, duration_seconds=100, project_id=None,
                 commands=[Command(timestamp=2000, command="echo hello")])
    
    # Session 3: same project (via cd)
    s3 = Session(id=3, start_time=3000, end_time=3100, duration_seconds=100, project_id=None,
                 commands=[Command(timestamp=3000, command="cd ~/projects/incubator-hugegraph"),
                           Command(timestamp=3050, command="git log")])
    
    projects = detect_projects([s1, s2, s3])
    
    # Session 2 should be propagated to the same project as s1 and s3
    assert s2.project_id is not None
    assert s2.project_id == s1.project_id
    assert s2.project_id == s3.project_id


def test_neighbor_propagation_follow():
    """Pass 3: 'Other' session immediately following a known project session gets assigned."""
    # Session 1: known project
    s1 = Session(id=1, start_time=1000, end_time=1100, duration_seconds=100, project_id=None,
                 commands=[Command(timestamp=1000, command="cd ~/projects/incubator-hugegraph"),
                           Command(timestamp=1050, command="git status")])
    
    # Session 2: no cd, follows within 2 hours
    s2 = Session(id=2, start_time=2000, end_time=2100, duration_seconds=100, project_id=None,
                 commands=[Command(timestamp=2000, command="echo hello")])
    
    projects = detect_projects([s1, s2])
    
    assert s2.project_id is not None
    assert s2.project_id == s1.project_id


def test_neighbor_propagation_long_gap_no_propagation():
    """Pass 3: 'Other' session with a gap > 2 hours should NOT be propagated."""
    # Session 1: known project
    s1 = Session(id=1, start_time=1000, end_time=1100, duration_seconds=100, project_id=None,
                 commands=[Command(timestamp=1000, command="cd ~/projects/incubator-hugegraph"),
                           Command(timestamp=1050, command="git status")])
    
    # Session 2: 3-hour gap
    gap = 3 * 3600  # 3 hours
    s2 = Session(id=2, start_time=1100 + gap, end_time=1200 + gap, duration_seconds=100, project_id=None,
                 commands=[Command(timestamp=1100 + gap, command="echo hello")])
    
    projects = detect_projects([s1, s2])
    
    # Session 2 should remain unassigned
    assert s2.project_id is None


def test_git_command_inference_without_cd():
    """Pass 2: Session with git commands but no cd should be inferred from nearby project sessions."""
    # Session 1: known project
    s1 = Session(id=1, start_time=1000, end_time=1100, duration_seconds=100, project_id=None,
                 commands=[Command(timestamp=1000, command="cd ~/projects/incubator-hugegraph"),
                           Command(timestamp=1050, command="git status")])
    
    # Session 2: git commit without cd, within 1 hour of s1
    s2 = Session(id=2, start_time=2000, end_time=2200, duration_seconds=200, project_id=None,
                 commands=[Command(timestamp=2000, command="git commit -m 'fix parser'"),
                           Command(timestamp=2100, command="git push origin main")])
    
    projects = detect_projects([s1, s2])
    
    # Session 2 should be assigned to same project via git command inference
    assert s2.project_id is not None
    assert s2.project_id == s1.project_id

def test_find_project_root_network_mounts_and_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr("os.path.expanduser", lambda path: str(tmp_path) if path == "~" else path)
    
    from termstory.project import find_project_root
    
    # 1. Test blacklisted prefixes (not whitelisted)
    assert find_project_root("/mnt/stale_nfs") == str(tmp_path)
    assert find_project_root("/Volumes/smb/stale_smb") == str(tmp_path)
    assert find_project_root(r"\\Server\Share") == str(tmp_path)
    
    # 2. Test whitelist configuration
    import json
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "config.json"
    config_data = {
        "network_mount_whitelist": ["/mnt/my_safe_nfs"]
    }
    with open(config_file, "w") as f:
        json.dump(config_data, f)
        
    # We mock get_app_dir to point to this tmp config dir
    monkeypatch.setattr("termstory.config.get_app_dir", lambda dir_type: str(config_dir))
    
    # Check that whitelisted path is NOT immediately returned as home
    # but instead it attempts listdir (and falls back to home because path doesn't exist)
    assert find_project_root("/mnt/my_safe_nfs/non_existent_folder") == str(tmp_path)

    # 3. Test listdir timeout/hang
    import time
    def mock_listdir_slow(path):
        time.sleep(1.0)
        return []
    
    # Create a real directory that is NOT network blacklisted
    local_dir = tmp_path / "Projects" / "local-project"
    local_dir.mkdir(parents=True, exist_ok=True)
    
    monkeypatch.setattr("os.listdir", mock_listdir_slow)
    # Because listing local_dir times out, it should gracefully fall back to home instead of hanging
    t0 = time.time()
    res = find_project_root(str(local_dir))
    t1 = time.time()
    assert res == str(local_dir)
    assert t1 - t0 < 3.0  # Timeout prevents 4 calls from taking 4.0s (mock listdir sleeps 1.0s per call, 4 calls = 4.0s)


def test_neighbor_propagation_next_project_only():
    """Pass 3: 'Other' session with next_project in proximity but no prev_project gets assigned to next_project."""
    # Session 1: no cd, no project
    s1 = Session(id=1, start_time=1000, end_time=1100, duration_seconds=100, project_id=None,
                 commands=[Command(timestamp=1000, command="echo hello")])
    
    # Session 2: known project, starts within 2 hours of s1's end
    s2 = Session(id=2, start_time=2000, end_time=2100, duration_seconds=100, project_id=None,
                 commands=[Command(timestamp=2000, command="cd ~/projects/incubator-hugegraph"),
                           Command(timestamp=2050, command="git status")])
    
    projects = detect_projects([s1, s2])
    
    assert s1.project_id is not None
    assert s1.project_id == s2.project_id


def test_neighbor_propagation_proximity_comparison():
    """Pass 3: 'Other' session sandwiched but closer to next_project than prev_project (or vice versa)."""
    # Session 1: project Alpha, ends at 1100
    s1 = Session(id=1, start_time=1000, end_time=1100, duration_seconds=100, project_id=None,
                 commands=[Command(timestamp=1000, command="cd ~/projects/project-alpha"),
                           Command(timestamp=1050, command="git status")])
    
    # Session 2: no project, starts at 2000, ends at 2100.
    # gap from s1: 2000 - 1100 = 900 seconds.
    # gap to s3: 2500 - 2100 = 400 seconds.
    s2 = Session(id=2, start_time=2000, end_time=2100, duration_seconds=100, project_id=None,
                 commands=[Command(timestamp=2000, command="cd"),
                           Command(timestamp=2050, command="echo hello")])
    
    # Session 3: project Beta, starts at 2500
    s3 = Session(id=3, start_time=2500, end_time=2600, duration_seconds=100, project_id=None,
                 commands=[Command(timestamp=2500, command="cd ~/projects/project-beta"),
                           Command(timestamp=2550, command="git status")])
    
    projects = detect_projects([s1, s2, s3])
    
    # Since s2 is closer to s3 (400s gap) than s1 (900s gap), it should be assigned to s3's project
    assert s2.project_id is not None
    assert s2.project_id == s3.project_id
