import os
import re
import subprocess
from typing import List, Dict

def is_git_repo(path: str) -> bool:
    """Check if the directory path is a valid git repository worktree"""
    abs_path = os.path.abspath(os.path.expanduser(path))
    if not os.path.exists(abs_path) or not os.path.isdir(abs_path):
        return False
    try:
        # Check if the folder is inside a git work tree
        res = subprocess.run(
            ["git", "-C", abs_path, "rev-parse", "--is-inside-work-tree"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            check=False,
            timeout=10
        )
        # Returning True only when both the process succeeded and git reports
        # that we are inside a work tree. `rev-parse --is-inside-work-tree`
        # exits 0 and prints "false" for a .git directory or a bare repo.
        return res.returncode == 0 and res.stdout.strip() == "true"
    except Exception:
        return False

def clean_commit_message(message: str) -> str:
    """Clean commit message for display by removing emojis, JIRA codes, PR numbers, and prefixes"""
    if not message or not isinstance(message, str):
        return ""
        
    msg = message.strip()
    
    # 1. Strip raw emojis and miscellaneous symbols first (so they don't block start-of-line patterns)
    emoji_pattern = re.compile(
        '['
        '\U0001f600-\U0001f64f'  # emoticons
        '\U0001f300-\U0001f5ff'  # symbols & pictographs
        '\U0001f680-\U0001f6ff'  # transport & map symbols
        '\U0001f1e0-\U0001f1ff'  # flags
        '\U00002700-\U000027bf'  # dingbats
        '\U00002600-\U000026ff'  # misc symbols
        '\U0001f900-\U0001f9ff'  # supplemental symbols
        '\U0001fa70-\U0001faff'  # pictographs extended
        ']+', flags=re.UNICODE
    )
    msg = emoji_pattern.sub('', msg)
    msg = msg.strip()
    
    # 2. Strip :emoji: shorthand patterns
    msg = re.sub(r':[a-zA-Z0-9_\-+]+:', '', msg)
    msg = msg.strip()
    
    # 3. Strip any JIRA/issue reference like [ABC-123] or ABC-123: at the start.
    #    Requiring the colon keeps ordinary hyphenated identifiers such as
    #    SHA-1, UTF-8, and CVE-2024 from being mistaken for an issue key.
    msg = re.sub(r'^\[[A-Za-z]+-\d+\]\s*', '', msg)
    msg = re.sub(r'^[A-Za-z]+-\d+:\s*', '', msg)
    
    # 4. Strip conventional commit prefixes (e.g. feat: fix: chore: etc. with case-insensitive flag at the start, supporting breaking changes and revert)
    msg = re.sub(r'(?i)^(feat|fix|chore|docs|refactor|test|style|ci|perf|build|revert)(?:\([a-zA-Z0-9_\-\/]+\))?!?:\s*', '', msg)
    
    # 5. Strip PR references at the end, e.g. '(#3044)' or ' #3044'
    msg = re.sub(r'\s*\(#\d+\)\s*$', '', msg)
    msg = re.sub(r'\s*#\d+\s*$', '', msg)
    
    # Strip extra whitespace and capitalize first letter
    msg = msg.strip()
    if msg:
        msg = msg[0].upper() + msg[1:]
        
    return msg

def get_project_commits(project_path: str, since_ts: int, timeout: int = 10) -> List[Dict]:
    """Get recent commits for a project since a specific Unix timestamp"""
    abs_path = os.path.abspath(os.path.expanduser(project_path))
    if not is_git_repo(abs_path):
        return []
        
    try:
        # Run git log with since timestamp filter
        # %H: commit hash
        # %ct: committer date (Unix timestamp)
        # %s: commit message
        res = subprocess.run(
            [
                "git", "-C", abs_path, "log", 
                f"--since={since_ts}", 
                "--pretty=format:%H|%ct|%s"
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            check=False,
            timeout=timeout
        )
        if res.returncode != 0:
            return []
            
        commits = []
        for line in res.stdout.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            parts = line.split("|", 2)
            if len(parts) < 3:
                continue
            h, ts_str, raw_msg = parts
            try:
                ts = int(ts_str)
            except ValueError:
                continue
                
            cleaned = clean_commit_message(raw_msg)
            commits.append({
                "hash": h,
                "timestamp": ts,
                "message": raw_msg,
                "cleaned_message": cleaned
            })
        return commits
    except Exception:
        return []


def get_timeframe_git_stats(project_paths: List[str], since_ts: int, until_ts: int) -> Dict:
    """Collect aggregate git additions/deletions and branch merges for a list of project paths."""
    total_additions = 0
    total_deletions = 0
    merged_branches = []
    
    for path in project_paths:
        abs_path = os.path.abspath(os.path.expanduser(path))
        if not is_git_repo(abs_path):
            continue
            
        # 1. Get additions and deletions
        try:
            res = subprocess.run(
                [
                    "git", "-C", abs_path, "log", 
                    f"--since={since_ts}", f"--until={until_ts}",
                    "--numstat", "--pretty=format:"
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
                check=False,
                timeout=10
            )
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    parts = line.strip().split()
                    if len(parts) >= 2:
                        try:
                            add = int(parts[0])
                            del_ = int(parts[1])
                            total_additions += add
                            total_deletions += del_
                        except ValueError:
                            pass # Handles binary files marked with '-'
        except Exception:
            pass
            
        # 2. Get merges and branch names
        try:
            res = subprocess.run(
                [
                    "git", "-C", abs_path, "log", 
                    f"--since={since_ts}", f"--until={until_ts}",
                    "--merges", "--pretty=format:%s"
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
                check=False,
                timeout=10
            )
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    m = re.search(r"Merge branch '([^']+)'", line)
                    if m:
                        merged_branches.append(m.group(1))
                        continue
                    m = re.search(r"Merge pull request #\d+ from \S+/(\S+)", line)
                    if m:
                        merged_branches.append(m.group(1))
                        continue
                    m = re.search(r"Merge branch '(\S+)'", line)
                    if m:
                        merged_branches.append(m.group(1))
        except Exception:
            pass
            
    return {
        "additions": total_additions,
        "deletions": total_deletions,
        "merged_branches": list(dict.fromkeys(merged_branches))
    }

