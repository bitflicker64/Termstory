import os
import re
from typing import List, Optional, Dict
from termstory.models import Session, Project

import shlex
import functools
import time

def extract_cd_path(cmd_str: str) -> Optional[str]:
    """Extract directory path from a cd command"""
    try:
        tokens = shlex.split(cmd_str)
    except Exception:
        tokens = cmd_str.strip().split()
        
    if not tokens or tokens[0] != 'cd':
        return None
        
    # Filter out cd flags (like -P, -L, --, etc.)
    path_args = [t for t in tokens[1:] if not t.startswith('-') or t == '-']
    if not path_args:
        # cd with no arguments defaults to ~ (home)
        return "~"
        
    # Take the first argument
    return path_args[0]

def humanize_project_name(path: str) -> str:
    """Humanize directory name (e.g. incubator-hugegraph -> Apache HugeGraph)"""
    if path == "~" or path == os.path.expanduser("~") or path == "/":
        return "Home"
        
    # Remove trailing slash
    normalized_path = path.rstrip('/')
    base_name = os.path.basename(normalized_path)
    if not base_name:
        return "Root"
        
    # Replace hyphens and underscores with spaces
    name = base_name.replace('-', ' ').replace('_', ' ')
    
    # Specific heuristics and capitalization replacements
    word_replacements = {
        "hugegraph": "HugeGraph",
        "incubator": "Apache",
        "k8s": "Kubernetes",
        "tf": "Terraform",
        "db": "Database",
        "cli": "CLI",
    }
    
    prefixes_to_strip = {"my", "project", "learning", "test"}
    
    words = name.split()
    while words and words[0].lower() in prefixes_to_strip:
        words.pop(0)
        
    processed_words = []
    for word in words:
        word_lower = word.lower()
        if word_lower in word_replacements:
            processed_words.append(word_replacements[word_lower])
        else:
            # Capitalize first letter
            processed_words.append(word.capitalize())
            
    if not processed_words:
        return base_name.capitalize()
        
    return " ".join(processed_words)

def disambiguate_project_names(projects: List[Project]) -> Dict[int, str]:
    """Return a mapping of project_id -> display_name. If name clashes exist, 
    appends the abbreviated parent directory path hint."""
    from collections import defaultdict
    by_name = defaultdict(list)
    for p in projects:
        if p.id is not None:
            by_name[p.name].append(p)
            
    display_names = {}
    for name, projs in by_name.items():
        if len(projs) == 1:
            display_names[projs[0].id] = projs[0].name
        else:
            for p in projs:
                parent_dir = os.path.dirname(p.path)
                home = os.path.expanduser("~")
                if parent_dir == home:
                    parent_dir = "~"
                elif parent_dir.startswith(home + "/"):
                    parent_dir = "~" + parent_dir[len(home):]
                display_names[p.id] = f"{p.name} ({parent_dir})"
    return display_names

import threading

def _listdir_with_timeout(path: str, timeout: float = 0.5) -> List[str]:
    """Execute os.listdir in a daemon thread to enforce a timeout (e.g. on hung NFS mounts)"""
    result = []
    exception_container = [None]

    def target():
        try:
            result.extend(os.listdir(path))
        except Exception as e:
            exception_container[0] = e

    t = threading.Thread(target=target)
    t.daemon = True
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError(f"os.listdir timed out on path: {path}")
    if exception_container[0] is not None:
        raise exception_container[0]
    return result

@functools.lru_cache(maxsize=1024)
def _find_project_root_cached(path: str) -> str:
    return _find_project_root_impl(path)

def find_project_root(path: str) -> str:
    """Find the root project directory for a given path by looking for repository/project markers, 
    stopping at home or root directories. Prioritizes VCS roots (.git, .hg, .svn) first."""
    return _find_project_root_cached(path)

def _find_project_root_impl(path: str) -> str:
    home = os.path.realpath(os.path.abspath(os.path.expanduser("~")))
    
    # Check for Windows UNC paths on the raw path first
    if path.startswith(r"\\") or path.startswith(r"//"):
        return home

    # Expand and make absolute, resolve symlinks
    abs_path = os.path.realpath(os.path.abspath(os.path.expanduser(path)))
    
    # If the path is home or root, just return it
    if abs_path == home or abs_path == "/":
        return abs_path

    # Check for NFS/SMB network mounts and Windows UNC paths
    from termstory.config import load_config
    try:
        config = load_config()
    except Exception:
        config = {}
    whitelist = config.get("network_mount_whitelist", [])

    is_network = False
    blacklist_prefixes = ["/mnt", "/Volumes/smb"]
    for prefix in blacklist_prefixes:
        if abs_path == prefix or abs_path.startswith(prefix + "/"):
            is_network = True
            break

    if is_network:
        # Check if whitelisted
        whitelisted = False
        for wl_path in whitelist:
            wl_abs = os.path.realpath(os.path.abspath(os.path.expanduser(wl_path)))
            if abs_path == wl_abs or abs_path.startswith(wl_abs + "/") or abs_path.startswith(wl_abs + "\\"):
                whitelisted = True
                break
        if not whitelisted:
            return home

    max_depth = 50

    # --- Pass 1: Search for VCS roots (.git, .hg, .svn) ---
    current = abs_path
    vcs_markers = {".git", ".hg", ".svn"}
    depth = 0
    while current and current != home and current != "/" and depth < max_depth:
        depth += 1
        try:
            files = _listdir_with_timeout(current, timeout=0.5)
            if any(marker in files for marker in vcs_markers):
                return current
        except Exception:
            pass
            
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
        
    # --- Pass 2: Search for other project markers (pom.xml, package.json, etc.) ---
    current = abs_path
    project_markers = {
        "package.json", "pom.xml", "build.gradle", "Cargo.toml", 
        "requirements.txt", "setup.py", "Makefile", "go.mod", 
        "CMakeLists.txt", "pyproject.toml"
    }
    depth = 0
    while current and current != home and current != "/" and depth < max_depth:
        depth += 1
        try:
            files = _listdir_with_timeout(current, timeout=0.5)
            if any(marker in files for marker in project_markers):
                return current
        except Exception:
            pass
            
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
        
    # Fallback logic if no project markers were found:
    # Check if the path is inside home. Implement a blacklist for explicitly irrelevant directories.
    # Any non-blacklisted folder up to 2 directory levels deep will be allowed to register as a project.
    if abs_path.startswith(home + os.sep):
        rel_to_home = os.path.relpath(abs_path, home)
        parts = rel_to_home.split(os.sep)
        
        blacklist = {"trash", "downloads", "desktop", "cache", "documents", "pictures", "movies", "music", "public", "applications", "library"}
        for part in parts:
            if part.startswith('.') or part.lower() in blacklist:
                return home
                
        levels = min(2, len(parts))
        return os.path.join(home, *parts[:levels])
        
    return home

def _is_project_indicative_command(cmd_str: str) -> bool:
    """Check if a command strongly implies the user is inside a project directory."""
    cmd = cmd_str.strip()
    # Git write operations (read-only like `git status` are less indicative)
    git_write_cmds = {"git commit", "git push", "git pull", "git merge", "git rebase",
                      "git checkout", "git switch", "git stash", "git add", "git diff",
                      "git branch", "git cherry-pick", "git reset", "git tag", "git fetch"}
    for gc in git_write_cmds:
        if cmd.startswith(gc):
            return True
    # Build/run commands that only make sense inside a project
    project_cmds = ["npm ", "npx ", "yarn ", "pnpm ", "cargo ", "make", "gradle ",
                    "mvn ", "python manage.py", "python setup.py", "pip install -e",
                    "docker-compose ", "docker compose ", "pytest", "python -m pytest",
                    "python3 -m pytest", "go build", "go run", "go test", "flutter ",
                    "rails ", "bundle ", "mix ", "dotnet "]
    for pc in project_cmds:
        if cmd.startswith(pc) or cmd == pc.strip():
            return True
    return False


def _extract_file_args(cmd_str: str) -> List[str]:
    """Extract potential file path arguments from a command."""
    try:
        tokens = shlex.split(cmd_str)
    except Exception:
        tokens = cmd_str.strip().split()
    
    if not tokens:
        return []
    
    # Skip the command itself and any flags
    file_args = []
    for token in tokens[1:]:
        if token.startswith("-"):
            continue
        # Look for things that look like file paths (have extensions or path separators)
        if "/" in token or "." in token:
            # Skip URLs, environment variables, and obviously non-file things
            if token.startswith("http") or token.startswith("$") or "=" in token:
                continue
            file_args.append(token)
    return file_args


def _assign_project_to_session(session, project, projects_dict) -> None:
    """Helper to link a session and its commands to a project."""
    session.project_id = project.id
    for cmd in session.commands:
        cmd.project_id = project.id


def detect_projects(sessions: List[Session]) -> List[Project]:
    """Detect projects from cd commands in sessions, humanize names, and update links in sessions/commands.
    
    Uses a 3-pass approach:
      Pass 1: Track cd commands to maintain simulated CWD (existing logic)
      Pass 2: Command-based inference — git/build commands + file path matching for 'Other' sessions
      Pass 3: Neighbor propagation — assign 'Other' sessions based on adjacent session context
    """
    projects_dict = {}
    project_id_counter = 1
    
    # Sort sessions by start_time to keep timelines linear
    sorted_sessions = sorted(sessions, key=lambda s: s.start_time)
    
    # Persist cwd state across sessions to mirror terminal tab preservation
    cwd = os.path.expanduser("~")
    home = os.path.abspath(os.path.expanduser("~"))
    
    # ── Pass 1: cd-tracking (existing logic) ──────────────────────────
    last_session_end = None
    for session in sorted_sessions:
        if last_session_end is not None and session.start_time - last_session_end > 7200:
            cwd = home
        
        for cmd in session.commands:
            cmd_full = cmd.command.strip()
            subcommands = split_chained_commands(cmd_full)
            
            for subcmd in subcommands:
                # Must start with cd followed by space/tab/EOF
                if subcmd == "cd" or subcmd.startswith("cd ") or subcmd.startswith("cd\t"):
                    path = extract_cd_path(subcmd)
                    if path:
                        path = os.path.expandvars(path)
                        # Resolve path
                        resolved = None
                        if os.path.isabs(path) or path.startswith("~"):
                            resolved = os.path.abspath(os.path.expanduser(path))
                        else:
                            # Try relative to current simulated cwd
                            test_path = os.path.abspath(os.path.join(cwd, path))
                            if os.path.exists(test_path):
                                resolved = test_path
                            else:
                                # Try relative to any ancestor of the current cwd (handles missing cds)
                                ancestor = cwd
                                while ancestor and ancestor != home and ancestor != "/":
                                    ancestor = os.path.dirname(ancestor)
                                    test_path_ancestor = os.path.abspath(os.path.join(ancestor, path))
                                    if os.path.exists(test_path_ancestor):
                                        resolved = test_path_ancestor
                                        break
                                        
                                if not resolved:
                                    # Try relative to home directory
                                    test_path_home = os.path.abspath(os.path.join(home, path))
                                    if os.path.exists(test_path_home):
                                        resolved = test_path_home
                                    else:
                                        # Fallback: just join it relative to current cwd
                                        resolved = test_path
                                    
                        if resolved:
                            cwd = resolved
                        
        # The project path is the resolved cwd at the end of the session
        project_root = find_project_root(cwd)
        is_valid_project = project_root != home and project_root != "/"
        
        if is_valid_project:
            if project_root not in projects_dict:
                # Convert absolute project root back to a user-friendly path (using ~ if possible)
                display_path = project_root
                if project_root == home:
                    display_path = "~"
                elif project_root.startswith(home + "/"):
                    display_path = "~" + project_root[len(home):]
                    
                name = humanize_project_name(project_root)
                project = Project(
                    id=project_id_counter,
                    name=name,
                    path=display_path,
                    first_seen=session.start_time,
                    last_seen=session.end_time,
                    session_count=1,
                    total_time=session.duration_seconds
                )
                projects_dict[project_root] = project
                project_id_counter += 1
            else:
                project = projects_dict[project_root]
                project.first_seen = min(project.first_seen, session.start_time)
                project.last_seen = max(project.last_seen, session.end_time)
                project.session_count += 1
                project.total_time += session.duration_seconds
                
            # Link session and commands to project
            _assign_project_to_session(session, project, projects_dict)
        else:
            session.project_id = None
            for cmd in session.commands:
                cmd.project_id = None
        
        last_session_end = session.end_time
    
    # ── Pass 2: Command-based inference for "Other" sessions ──────────
    # Build a reverse lookup: abs_path -> project for known projects
    known_project_paths = {}  # abs_path -> Project
    for abs_root, project in projects_dict.items():
        known_project_paths[abs_root] = project
    
    if known_project_paths:
        for session in sorted_sessions:
            if session.project_id is not None:
                continue  # already assigned
            
            has_indicative_cmds = any(
                _is_project_indicative_command(cmd.command) for cmd in session.commands
            )
            
            if not has_indicative_cmds:
                continue
            
            # Strategy A: Check file path arguments against known project roots
            best_match = None
            best_score = 0
            
            for cmd in session.commands:
                file_args = _extract_file_args(cmd.command)
                for farg in file_args:
                    for abs_root, project in known_project_paths.items():
                        # Check if the file exists relative to a known project root
                        candidate = os.path.join(abs_root, farg)
                        if os.path.exists(candidate):
                            # Score by specificity (deeper paths = better match)
                            score = len(re.split(r'[\\/]', farg))
                            if score > best_score:
                                best_score = score
                                best_match = project
            
            if best_match:
                _assign_project_to_session(session, best_match, projects_dict)
                best_match.session_count += 1
                best_match.total_time += session.duration_seconds
                continue
            
            # Strategy B: If session has git commands, try to find which known project
            # had activity closest in time (within 1 hour before/after)
            if any(cmd.command.strip().startswith("git ") for cmd in session.commands):
                closest_project = None
                closest_gap = float("inf")
                
                for other_session in sorted_sessions:
                    if other_session.project_id is None or other_session is session:
                        continue
                    gap = min(
                        abs(session.start_time - other_session.end_time),
                        abs(other_session.start_time - session.end_time)
                    )
                    if gap < closest_gap and gap < 3600:  # within 1 hour
                        closest_gap = gap
                        closest_project_id = other_session.project_id
                        # Find the project object
                        for proj in projects_dict.values():
                            if proj.id == closest_project_id:
                                closest_project = proj
                                break
                
                if closest_project:
                    _assign_project_to_session(session, closest_project, projects_dict)
                    closest_project.session_count += 1
                    closest_project.total_time += session.duration_seconds

    # ── Pass 3: Neighbor propagation for remaining "Other" sessions ───
    # If an "Other" session is sandwiched between two sessions of the same project,
    # or immediately follows a known project session (within 2 hours), assign it.
    PROPAGATION_GAP_THRESHOLD = 7200  # 2 hours in seconds
    
    for i, session in enumerate(sorted_sessions):
        if session.project_id is not None:
            continue  # already assigned
        
        prev_project_id = None
        next_project_id = None
        prev_gap = float("inf")
        next_gap = float("inf")
        prev_project = None
        next_project = None
        
        # Look backward for the nearest assigned session
        for j in range(i - 1, -1, -1):
            if sorted_sessions[j].project_id is not None:
                prev_project_id = sorted_sessions[j].project_id
                prev_gap = session.start_time - sorted_sessions[j].end_time
                for proj in projects_dict.values():
                    if proj.id == prev_project_id:
                        prev_project = proj
                        break
                break
        
        # Look forward for the nearest assigned session
        for j in range(i + 1, len(sorted_sessions)):
            if sorted_sessions[j].project_id is not None:
                next_project_id = sorted_sessions[j].project_id
                next_gap = sorted_sessions[j].start_time - session.end_time
                for proj in projects_dict.values():
                    if proj.id == next_project_id:
                        next_project = proj
                        break
                break
        
        # Sandwich: same project on both sides, both within threshold
        if (prev_project_id is not None and next_project_id is not None
                and prev_project_id == next_project_id
                and prev_gap < PROPAGATION_GAP_THRESHOLD
                and next_gap < PROPAGATION_GAP_THRESHOLD):
            _assign_project_to_session(session, prev_project, projects_dict)
            prev_project.session_count += 1
            prev_project.total_time += session.duration_seconds
            continue
        
        # Proximity assignments:
        is_prev_valid = prev_project_id is not None and prev_gap < PROPAGATION_GAP_THRESHOLD
        is_next_valid = next_project_id is not None and next_gap < PROPAGATION_GAP_THRESHOLD

        if is_prev_valid and is_next_valid:
            if next_gap < prev_gap:
                _assign_project_to_session(session, next_project, projects_dict)
                next_project.session_count += 1
                next_project.total_time += session.duration_seconds
            else:
                _assign_project_to_session(session, prev_project, projects_dict)
                prev_project.session_count += 1
                prev_project.total_time += session.duration_seconds
            continue
        elif is_prev_valid:
            _assign_project_to_session(session, prev_project, projects_dict)
            prev_project.session_count += 1
            prev_project.total_time += session.duration_seconds
            continue
        elif is_next_valid:
            _assign_project_to_session(session, next_project, projects_dict)
            next_project.session_count += 1
            next_project.total_time += session.duration_seconds
            continue
    
    return list(projects_dict.values())

def split_chained_commands(cmd_str: str) -> List[str]:
    """Split a shell command by &&, ||, and ; while respecting single and double quotes."""
    commands = []
    current = []
    in_single = False
    in_double = False
    i = 0
    length = len(cmd_str)
    
    while i < length:
        c = cmd_str[i]
        if c == "'" and not in_double:
            in_single = not in_single
            current.append(c)
        elif c == '"' and not in_single:
            in_double = not in_double
            current.append(c)
        elif not in_single and not in_double:
            if c == ';':
                commands.append("".join(current))
                current = []
            elif c == '&' and i + 1 < length and cmd_str[i+1] == '&':
                commands.append("".join(current))
                current = []
                i += 1
            elif c == '|' and i + 1 < length and cmd_str[i+1] == '|':
                commands.append("".join(current))
                current = []
                i += 1
            else:
                current.append(c)
        else:
            current.append(c)
        i += 1
        
    if current:
        commands.append("".join(current))
        
    return [cmd.strip() for cmd in commands if cmd.strip()]
