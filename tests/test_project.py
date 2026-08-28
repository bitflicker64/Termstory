from termstory.models import Command, Session, Project
from termstory.project import (
    detect_projects, extract_cd_path, humanize_project_name, 
    disambiguate_project_names, _is_project_indicative_command, _extract_file_args,
    split_chained_commands
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
    # cmd1 is the cd itself — it ran in home (cwd before the cd takes effect),
    # so its per-command project_id is None (see #337/#339).
    assert cmd1.project_id is None
    # cmd2 ran after the cd, so it correctly attributes to proj_a.
    assert cmd2.project_id == proj_a.id

    # Verify Project B details
    proj_b = next(p for p in projects if "Awesome" in p.name)
    assert proj_b.path == "/Users/username/my-awesome-project"
    assert s2.project_id == proj_b.id
    # s2 starts with cwd still pointing at proj_a (the cwd persists across
    # sessions to mirror terminal tab preservation). So cmd3, the cd command
    # itself, attributes to proj_a; the cd only takes effect for cmd4.
    assert cmd3.project_id == proj_a.id
    assert cmd4.project_id == proj_b.id

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


def test_find_project_root_cache_invalidated_after_ttl_marker_creation(tmp_path, monkeypatch):
    """#417: creating a project marker must eventually invalidate the cached result."""
    import termstory.project as project_module
    from termstory.project import find_project_root, _PROJECT_ROOT_CACHE

    monkeypatch.setattr("os.path.expanduser", lambda path: str(tmp_path) if path == "~" else path)
    monkeypatch.setattr(project_module, "_PROJECT_ROOT_CACHE_TTL", 60.0)
    _PROJECT_ROOT_CACHE.clear()

    # Deterministic monotonic clock so TTL expiry does not depend on real elapsed time.
    clock = {"now": 1000.0}
    monkeypatch.setattr(project_module.time, "monotonic", lambda: clock["now"])

    # Repo root lives 3 levels under home so the no-marker fallback (2 levels)
    # differs from the marker-detected root.
    repo_root = tmp_path / "code" / "work" / "my-repo"
    nested = repo_root / "src" / "lib"
    nested.mkdir(parents=True)

    # 1. No marker yet -> fallback root (2 levels under home)
    assert find_project_root(str(nested)) == str(tmp_path / "code" / "work")

    # 2. Create the marker
    (repo_root / ".git").mkdir()

    # 3. Within the TTL the stale result is still served (caching benefit)
    assert find_project_root(str(nested)) == str(tmp_path / "code" / "work")

    # 4. Advance past the TTL so the newly created project root is detected
    clock["now"] += 61.0
    assert find_project_root(str(nested)) == str(repo_root)


def test_find_project_root_cache_invalidated_after_ttl_marker_removal(tmp_path, monkeypatch):
    """#417: removing a project marker must eventually invalidate the cached result."""
    import shutil
    import termstory.project as project_module
    from termstory.project import find_project_root, _PROJECT_ROOT_CACHE

    monkeypatch.setattr("os.path.expanduser", lambda path: str(tmp_path) if path == "~" else path)
    monkeypatch.setattr(project_module, "_PROJECT_ROOT_CACHE_TTL", 60.0)
    _PROJECT_ROOT_CACHE.clear()

    # Deterministic monotonic clock so TTL expiry does not depend on real elapsed time.
    clock = {"now": 1000.0}
    monkeypatch.setattr(project_module.time, "monotonic", lambda: clock["now"])

    repo_root = tmp_path / "code" / "work" / "my-repo"
    nested = repo_root / "src"
    nested.mkdir(parents=True)
    (repo_root / ".git").mkdir()

    # 1. Marker present -> project root detected
    assert find_project_root(str(nested)) == str(repo_root)

    # 2. Remove the marker
    shutil.rmtree(str(repo_root / ".git"))

    # 3. Within the TTL the stale root is still returned (caching benefit)
    assert find_project_root(str(nested)) == str(repo_root)

    # 4. Advance past the TTL so the stale project root is no longer returned
    clock["now"] += 61.0
    assert find_project_root(str(nested)) == str(tmp_path / "code" / "work")


def test_find_project_root_cache_serves_cached_result_within_ttl(tmp_path, monkeypatch):
    """#417: within the TTL the cached result is reused without re-walking the filesystem."""
    import termstory.project as project_module
    from termstory.project import find_project_root, _PROJECT_ROOT_CACHE

    monkeypatch.setattr("os.path.expanduser", lambda path: str(tmp_path) if path == "~" else path)
    monkeypatch.setattr(project_module, "_PROJECT_ROOT_CACHE_TTL", 60)
    _PROJECT_ROOT_CACHE.clear()

    calls = {"n": 0}
    real_impl = project_module._find_project_root_impl

    def counting_impl(path):
        calls["n"] += 1
        return real_impl(path)

    monkeypatch.setattr(project_module, "_find_project_root_impl", counting_impl)

    proj_dir = tmp_path / "Projects" / "cached-repo"
    nested = proj_dir / "sub"
    nested.mkdir(parents=True)
    (proj_dir / ".git").mkdir()

    assert find_project_root(str(nested)) == str(proj_dir)
    assert calls["n"] == 1

    # Subsequent calls within the TTL hit the cache and do NOT re-walk the tree
    assert find_project_root(str(nested)) == str(proj_dir)
    assert find_project_root(str(nested)) == str(proj_dir)
    assert calls["n"] == 1


def test_find_project_root_cache_normalized_key_shared(tmp_path, monkeypatch):
    """#484: equivalent path spellings share one cache entry and one result.

    ``repo/child/../child`` and ``repo/child`` refer to the same directory and
    must resolve to the same project root without spawning an extra
    ``_find_project_root_impl`` walk (which would otherwise create competing
    identities in the cache).
    """
    import termstory.project as project_module
    from termstory.project import find_project_root, _PROJECT_ROOT_CACHE

    monkeypatch.setattr("os.path.expanduser", lambda path: str(tmp_path) if path == "~" else path)
    monkeypatch.setattr(project_module, "_PROJECT_ROOT_CACHE_TTL", 60)
    _PROJECT_ROOT_CACHE.clear()

    calls = {"n": 0}
    real_impl = project_module._find_project_root_impl

    def counting_impl(path):
        calls["n"] += 1
        return real_impl(path)

    monkeypatch.setattr(project_module, "_find_project_root_impl", counting_impl)

    proj_dir = tmp_path / "Projects" / "cache-repo"
    child = proj_dir / "child"
    child.mkdir(parents=True)
    (proj_dir / ".git").mkdir()

    plain = find_project_root(str(child))
    dotdot = find_project_root(str(child / ".." / "child"))

    assert plain == str(proj_dir)
    assert dotdot == plain
    # Both spellings share the same cache entry, so only ONE impl walk runs.
    assert calls["n"] == 1


def test_find_project_root_cache_nested_not_poisoned(tmp_path, monkeypatch):
    """#484: an outer repo's cached root must not poison nested detection.

    Cache entries are keyed per cwd, so resolving an outer directory and a
    nested repo must yield distinct roots (inner wins for the nested cwd).
    """
    import termstory.project as project_module
    from termstory.project import find_project_root, _PROJECT_ROOT_CACHE

    monkeypatch.setattr("os.path.expanduser", lambda path: str(tmp_path) if path == "~" else path)
    monkeypatch.setattr(project_module, "_PROJECT_ROOT_CACHE_TTL", 60)
    _PROJECT_ROOT_CACHE.clear()

    outer = tmp_path / "Projects" / "outer-repo"
    nested_repo = outer / "nested"
    src = nested_repo / "src"
    src.mkdir(parents=True)
    (outer / ".git").mkdir()
    (nested_repo / ".git").mkdir()

    outer_root = find_project_root(str(outer))
    nested_root = find_project_root(str(src))

    assert outer_root == str(outer)
    assert nested_root == str(nested_repo)  # inner repo wins, not outer


def test_find_project_root_cache_raw_unc_not_aliased(tmp_path, monkeypatch):
    """P1 #1 (Greptile): raw paths with distinct UNC semantics must NOT alias.

    ``_find_project_root_impl`` branches on the RAW path prefix: a path whose
    raw spelling starts with ``\\\\`` or ``//`` hits the UNC/network
    short-circuit (returns home), while the same directory spelled without that
    prefix is resolved normally.  The cache key must therefore retain this
    raw-prefix signal; otherwise ``///workspace`` and ``/workspace`` would
    normalise to the same realpath and wrongly share one cached result while
    ``_find_project_root_impl`` would have produced different answers.

    This test drives semantically-distinct raw paths through the cache and
    asserts each triggers its own ``_find_project_root_impl`` lookup (no alias),
    and that the second result is not the first's cached value.  It does not
    depend on host path-normalisation, only on the raw-prefix flag being part of
    the cache identity, so it is deterministic across platforms.
    """
    import termstory.project as project_module
    from termstory.project import find_project_root, _PROJECT_ROOT_CACHE

    monkeypatch.setattr("os.path.expanduser", lambda path: str(tmp_path) if path == "~" else path)
    monkeypatch.setattr(project_module, "_PROJECT_ROOT_CACHE_TTL", 60)
    _PROJECT_ROOT_CACHE.clear()

    seen = []

    def recording_impl(path):
        seen.append(path)
        # Mirror _find_project_root_impl()'s raw-path UNC short-circuit exactly.
        if path.startswith(r"\\") or path.startswith(r"//"):
            return "HOME-UNC"
        return "NORMAL:" + path

    monkeypatch.setattr(project_module, "_find_project_root_impl", recording_impl)

    normal = "/workspace"
    unc = "//workspace"  # raw spelling triggers the UNC prefix branch

    r1 = find_project_root(normal)
    seen.clear()  # only inspect the lookups after the first is cached
    r2 = find_project_root(unc)

    # The UNC-spelled path must resolve via its OWN impl call with the UNC
    # result, NOT the cached result of the normal spelling.
    assert r2 == "HOME-UNC"
    assert r2 != r1
    assert seen == ["//workspace"]


def test_get_project_root_cache_ttl_reads_config(tmp_path, monkeypatch):
    """#417: project_root_cache_ttl config is respected by _get_project_root_cache_ttl."""
    import json
    from termstory.project import _get_project_root_cache_ttl

    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "config.json"
    config_file.write_text(json.dumps({"project_root_cache_ttl": 5}))

    monkeypatch.setattr("termstory.config.get_app_dir", lambda dir_type: str(config_dir))
    assert _get_project_root_cache_ttl() == 5.0


def test_get_project_root_cache_ttl_default_and_clamp(monkeypatch):
    """#417: default TTL is 60s; values below 1s are clamped; config errors fall back."""
    from unittest.mock import patch
    from termstory.project import _get_project_root_cache_ttl

    with patch("termstory.config.load_config", return_value={}):
        assert _get_project_root_cache_ttl() == 60.0

    with patch("termstory.config.load_config", return_value={"project_root_cache_ttl": 0}):
        assert _get_project_root_cache_ttl() == 1.0  # clamped minimum

    with patch("termstory.config.load_config", side_effect=RuntimeError("boom")):
        assert _get_project_root_cache_ttl() == 60.0  # fallback on config error



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

def test_split_chained_commands():
    assert split_chained_commands("echo 'hello && world'") == ["echo 'hello && world'"]
    assert split_chained_commands("echo hello && ls") == ["echo hello", "ls"]
    assert split_chained_commands("echo hello \\&\\& world; ls") == ["echo hello \\&\\& world", "ls"]
    assert split_chained_commands("echo \"hello \\\" world\" && ls") == ["echo \"hello \\\" world\"", "ls"]

def test_cd_minus(monkeypatch):
    import os
    original_listdir = os.listdir
    def mock_listdir(path):
        if "project-alpha" in path or "project-beta" in path:
            return [".git"]
        return original_listdir(path)
    monkeypatch.setattr(os, "listdir", mock_listdir)

    s1 = Session(id=1, start_time=1000, end_time=1200, duration_seconds=200, project_id=None,
                 commands=[
                     Command(timestamp=1000, command="cd ~/projects/project-alpha"),
                     Command(timestamp=1050, command="git status"),
                     Command(timestamp=1100, command="cd ~/projects/project-beta"),
                     Command(timestamp=1150, command="cd -"),
                     Command(timestamp=1200, command="git log")
                 ])
                 
    projects = detect_projects([s1])
    alpha_proj = next(p for p in projects if "alpha" in p.path.lower())
    assert s1.project_id == alpha_proj.id

def test_listdir_timeout_caching(monkeypatch):
    import time
    import pytest
    from termstory.project import _listdir_with_timeout, _BLACKLISTED_MOUNTS
    
    _BLACKLISTED_MOUNTS.clear()
    
    def mock_listdir_hang(path):
        time.sleep(2.0)
        return []
        
    import os
    import threading
    monkeypatch.setattr(os, "listdir", mock_listdir_hang)
    
    initial_threads = threading.active_count()
    
    # First call should time out after 0.1s (we'll use timeout=0.1)
    t0 = time.time()
    with pytest.raises(TimeoutError):
        _listdir_with_timeout("/some/hung/mount", timeout=0.1)
    t1 = time.time()
    assert 0.08 <= (t1 - t0) <= 0.5  # timed out correctly
    
    # Verify exactly one worker thread was created and is currently hung
    assert threading.active_count() == initial_threads + 1
    
    # Second call should time out immediately from cache
    t2 = time.time()
    with pytest.raises(TimeoutError) as exc_info:
        _listdir_with_timeout("/some/hung/mount", timeout=0.1)
    t3 = time.time()
    
    assert (t3 - t2) < 0.05  # should be virtually instant
    assert "cached" in str(exc_info.value)
    
    # Verify NO additional worker thread was created
    assert threading.active_count() == initial_threads + 1

def test_listdir_timeout_caching_custom_ttl(monkeypatch):
    """Custom nfs_timeout_cache_ttl from config is respected."""
    import time
    import pytest
    import termstory.project as project_module
    from termstory.project import _listdir_with_timeout, _BLACKLISTED_MOUNTS

    _BLACKLISTED_MOUNTS.clear()

    # Patch the module-level constant directly to 1 second
    monkeypatch.setattr(project_module, "_NFS_TIMEOUT_CACHE_TTL", 1)

    def mock_listdir_hang(path):
        time.sleep(2.0)
        return []

    import os
    monkeypatch.setattr(os, "listdir", mock_listdir_hang)

    # First call times out and caches the path
    with pytest.raises(TimeoutError):
        _listdir_with_timeout("/some/custom/ttl/mount", timeout=0.1)

    # Second call should hit cache immediately
    with pytest.raises(TimeoutError) as exc_info:
        _listdir_with_timeout("/some/custom/ttl/mount", timeout=0.1)
    assert "cached" in str(exc_info.value)

    # Wait for the 1-second TTL to expire
    time.sleep(1.1)

    # Third call should bypass cache and actually attempt listdir again
    with pytest.raises(TimeoutError) as exc_info2:
        _listdir_with_timeout("/some/custom/ttl/mount", timeout=0.1)
    assert "cached" not in str(exc_info2.value)


def test_detect_projects_per_command_project_context(monkeypatch):
    """#337: per-command project_id reflects the cwd at the time the
    command was issued, not just the final cwd of the session.

    Regression: previously every command in a session was attributed to the
    session's final project, so a session that ``cd``-ed into project B
    from project A had all its commands labelled as B.
    """
    import os
    original_listdir = os.listdir

    def mock_listdir(path):
        if path in (
            "/Users/username/Projects/acme-billing",
            "/Users/username/Projects/mobile-companion",
        ):
            return [".git"]
        return original_listdir(path)

    monkeypatch.setattr(os, "listdir", mock_listdir)

    s = Session(
        id=1, start_time=1000, end_time=5000, duration_seconds=4000,
        project_id=None,
        commands=[
            Command(timestamp=1000, command="cd ~/Projects/acme-billing"),
            Command(timestamp=1100, command="pytest tests/test_invoice_totals.py -q"),
            Command(timestamp=1200, command='git commit -m "Fix invoice rounding"'),
            Command(timestamp=2000, command="cd ~/Projects/mobile-companion"),
            Command(timestamp=2100, command="npm run test -- --watch=false"),
            Command(timestamp=2200, command='git commit -m "Clarify offline retry state"'),
        ],
    )

    projects = detect_projects([s])

    # Both projects must be discovered
    assert len(projects) == 2
    billing = next(p for p in projects if "Acme Billing" in p.name or "Billing" in p.name or "acme" in p.path.lower())
    mobile = next(p for p in projects if "Mobile Companion" in p.name or "mobile" in p.path.lower())

    # Session's final project must be the mobile-companion (the final cwd)
    assert s.project_id == mobile.id

    # Per-command attribution rules (see #337):
    # - cmd[0] is the cd itself, ran in home → None
    # - cmd[1..2] ran in acme-billing (after the cd took effect)
    # - cmd[3] is the cd to mobile, ran in acme-billing → acme-billing
    # - cmd[4..5] ran in mobile-companion
    assert s.commands[0].project_id is None
    assert s.commands[1].project_id == billing.id
    assert s.commands[2].project_id == billing.id
    assert s.commands[3].project_id == billing.id
    assert s.commands[4].project_id == mobile.id
    assert s.commands[5].project_id == mobile.id

    # Session.project_ids helper exposes the union of distinct command projects
    assert s.project_ids == {billing.id, mobile.id}


def test_detect_projects_per_command_null_handling(monkeypatch):
    """#337: commands whose cwd is not inside any project root must keep
    cmd.project_id == None (not inherit the session's final project)."""
    import os
    original_listdir = os.listdir

    def mock_listdir(path):
        if path == "/Users/username/Projects/real-project":
            return [".git"]
        return original_listdir(path)

    monkeypatch.setattr(os, "listdir", mock_listdir)

    s = Session(
        id=1, start_time=1000, end_time=2000, duration_seconds=1000,
        project_id=None,
        commands=[
            Command(timestamp=1000, command="echo 'before cd'"),
            Command(timestamp=1100, command="cd ~/Projects/real-project"),
            Command(timestamp=1200, command="git status"),
        ],
    )

    projects = detect_projects([s])

    assert len(projects) == 1
    real_proj = projects[0]

    # First command ran before any cd → no project attribution
    assert s.commands[0].project_id is None
    # Second command is the cd itself — runs in the pre-cd cwd (home)
    # so it gets no project attribution
    assert s.commands[1].project_id is None
    # Third command runs inside real-project
    assert s.commands[2].project_id == real_proj.id


def test_detect_projects_with_null_end_time():
    """Active (end_time=None) session must not crash Pass 2 gap arithmetic (#312/#372).

    ``s2`` sends cwd to home (``cd ~``) so it stays unassigned after Pass 1, and it
    contains a git command, which drives it into Pass 2 Strategy B where
    ``session.end_time`` is subtracted (``termstory/project.py`` ~549-550). Before the
    fix this raised ``TypeError: unsupported operand type(s) for -: 'int' and 'NoneType'``.
    """
    # Completed session -> becomes an assigned project.
    s1 = Session(
        id=1, start_time=1000, end_time=1100, duration_seconds=100,
        project_id=None,
        commands=[
            Command(timestamp=1000, command="cd ~/projects/incubator-hugegraph"),
            Command(timestamp=1050, command="git status"),
        ],
    )

    # Active session: `cd ~` sends cwd to home so it stays "Other" after Pass 1
    # (without the `cd /` path that would let Pass 2 Strategy A grab it), and the
    # indicative git command routes it into Pass 2 Strategy B, which subtracts its
    # None end_time against the other assigned session.
    s2 = Session(
        id=2, start_time=2000, end_time=None, duration_seconds=0,
        project_id=None,
        commands=[
            Command(timestamp=2000, command="cd ~"),
            Command(timestamp=2010, command="git push origin main"),
        ],
    )

    projects = detect_projects([s1, s2])  # must not raise TypeError

    proj = next((p for p in projects if "HugeGraph" in p.name), None)
    assert proj is not None
    # last_seen falls back to start_time for the active session and stays an int.
    assert isinstance(proj.last_seen, int)


def test_detect_projects_with_null_end_time_gap_arithmetic():
    """Active (end_time=None) session as a Pass 3 neighbor must not crash (#312/#372).

    ``s2`` resets cwd to ``/`` and has no git command, so it stays "Other" through
    Pass 2 and reaches Pass 3 neighbor propagation, where the forward gap subtracts
    ``session.end_time`` (``termstory/project.py`` ~597). Before the fix this raised
    ``TypeError`` on the None end_time.
    """
    # Completed session in project-alpha -> assigned.
    s1 = Session(
        id=1, start_time=1000, end_time=1100, duration_seconds=100,
        project_id=None,
        commands=[
            Command(timestamp=1000, command="cd ~/projects/project-alpha"),
            Command(timestamp=1050, command="git status"),
        ],
    )
    # Active "Other" session (cd / resets cwd, no git) sandwiched between two
    # assigned sessions -> Pass 3 forward-gap subtracts its None end_time.
    s2 = Session(
        id=2, start_time=2000, end_time=None, duration_seconds=0,
        project_id=None,
        commands=[
            Command(timestamp=2000, command="cd /"),
            Command(timestamp=2010, command="echo working"),
        ],
    )
    # Completed session in project-alpha again -> sandwich with s2 in the middle.
    s3 = Session(
        id=3, start_time=3000, end_time=3100, duration_seconds=100,
        project_id=None,
        commands=[
            Command(timestamp=3000, command="cd ~/projects/project-alpha"),
            Command(timestamp=3050, command="git log"),
        ],
    )

    projects = detect_projects([s1, s2, s3])  # must not raise
    assert isinstance(projects, list)


def test_detect_projects_null_end_time_as_prev_neighbor():
    """An active (end_time=None) session that is itself an *assigned* neighbour must
    not crash Pass 3's backward gap (#312/#372).

    ``s1`` has a valid ``cd`` so Pass 1 assigns it a project, but its ``end_time`` is
    None. A following "Other" session then computes ``prev_gap`` against ``s1`` in
    Pass 3 neighbour propagation, subtracting ``s1``'s None end_time
    (``termstory/project.py`` ~586).
    """
    # Active but assigned (valid cd) -> becomes the backward neighbour with None end.
    s1 = Session(
        id=1, start_time=1000, end_time=None, duration_seconds=0,
        project_id=None,
        commands=[
            Command(timestamp=1000, command="cd ~/projects/project-beta"),
            Command(timestamp=1050, command="git status"),
        ],
    )
    # "Other" session (cd ~, no git) after s1 -> Pass 3 backward gap uses s1.end_time.
    s2 = Session(
        id=2, start_time=2000, end_time=2100, duration_seconds=100,
        project_id=None,
        commands=[
            Command(timestamp=2000, command="cd ~"),
            Command(timestamp=2010, command="echo done"),
        ],
    )

    projects = detect_projects([s1, s2])  # must not raise
    assert isinstance(projects, list)
    # s1 remains assigned despite its None end_time.
    assert s1.project_id is not None
