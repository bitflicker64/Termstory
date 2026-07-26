import os
import json
import logging
import tempfile
import random
from typing import List, Any

if os.name == "nt":
    import msvcrt
else:
    import fcntl

logger = logging.getLogger(__name__)

def _acquire_lock(fd: int) -> None:
    """Acquire an exclusive file lock (cross-platform).

    On Windows, retry on transient errors caused by concurrent lock
    contention (`EACCES` and `EDEADLK`). Under high contention we back
    off with jitter to reduce thundering-herd retries.
    """
    if os.name == "nt":
        last_err = None
        for attempt in range(100):
            try:
                msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                return
            except OSError as e:
                last_err = e
                if e.errno not in (13, 36):  # EACCES / EDEADLK
                    raise
                import time
                delay = min(0.01 + (attempt * 0.005) + (random.random() * 0.01), 0.25)
                time.sleep(delay)
        raise last_err  # type: ignore[misc]
    else:
        fcntl.flock(fd, fcntl.LOCK_EX)

def _release_lock(fd: int) -> None:
    """Release the file lock."""
    try:
        if os.name == "nt":
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        # On Windows, unlocking an fd that never acquired the lock raises
        # PermissionError. Swallow it so cleanup cannot crash the caller.
        pass

def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base. Override wins for leaf values."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result

def get_app_dir(dir_type: str = "data") -> str:
    """Get the appropriate application directory.
    
    If ~/.termstory already exists, we use it for backward compatibility.
    Otherwise:
      - For "config": Use $XDG_CONFIG_HOME/termstory or ~/.config/termstory on Linux/macOS.
      - For "data" (or others): Use $XDG_DATA_HOME/termstory or ~/.local/share/termstory on Linux/macOS.
    """
    legacy_dir = os.path.expanduser("~/.termstory")
    if os.path.exists(legacy_dir):
        return legacy_dir
        
    if os.name != "nt":
        if dir_type == "config":
            xdg_config = os.environ.get("XDG_CONFIG_HOME")
            if xdg_config:
                return os.path.join(xdg_config, "termstory")
            return os.path.expanduser("~/.config/termstory")
        else: # "data" or "cache"
            xdg_data = os.environ.get("XDG_DATA_HOME")
            if xdg_data:
                return os.path.join(xdg_data, "termstory")
            return os.path.expanduser("~/.local/share/termstory")
            
    return legacy_dir

def get_history_files() -> List[str]:
    """Return a list of existing shell history file paths"""
    env_history = os.environ.get("HISTORY_FILES")
    if env_history:
        history_files = []
        separator = ";" if os.name == "nt" else ":"
        if "," in env_history:
            separator = ","
        parts = env_history.split(separator)
        for part in parts:
            part = part.strip()
            if part:
                expanded = os.path.realpath(os.path.abspath(os.path.expanduser(part)))
                if os.path.exists(expanded) and expanded not in history_files:
                    history_files.append(expanded)
        return history_files

    history_files = []
    
    # 1. Check HISTFILE env variable first
    histfile = os.environ.get("HISTFILE")
    if histfile:
        expanded = os.path.realpath(os.path.abspath(os.path.expanduser(histfile)))
        if os.path.exists(expanded) and expanded not in history_files:
            history_files.append(expanded)
            
    # 2. Check other common known paths
    candidate_paths = [
        "~/.zsh_history",
        "~/.bash_history",
        "~/.zhistory",
        "~/.histfile",
        "~/.local/share/fish/fish_history",
        "~/.local/share/powershell/PSReadLine/ConsoleHost_history.txt",
        "~/AppData/Roaming/Microsoft/Windows/PowerShell/PSReadLine/ConsoleHost_history.txt",
    ]
    for path in candidate_paths:
        expanded = os.path.realpath(os.path.abspath(os.path.expanduser(path)))
        if os.path.exists(expanded) and expanded not in history_files:
            history_files.append(expanded)
        
    return history_files

def get_db_path() -> str:
    """Return the path to the sqlite database, creating parent directories if needed"""
    env_path = os.environ.get("DB_PATH")
    if env_path:
        expanded_path = os.path.realpath(os.path.abspath(os.path.expanduser(env_path)))
        parent_dir = os.path.dirname(expanded_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        return expanded_path

    db_dir = get_app_dir("data")
    os.makedirs(db_dir, exist_ok=True)
    return os.path.join(db_dir, "termstory.db")

def get_config_path() -> str:
    """Return the path to the config JSON file"""
    db_dir = get_app_dir("config")
    os.makedirs(db_dir, exist_ok=True)
    return os.path.join(db_dir, "config.json")

def get_config_lock_path() -> str:
    """Return the path to the config lock file"""
    db_dir = get_app_dir("config")
    os.makedirs(db_dir, exist_ok=True)
    return os.path.join(db_dir, "config.lock")


def translate_legacy_key(config: dict, key: str) -> str:
    """Translate a legacy flat config key to the new nested dot path structure."""
    if key == "groq_api_key":
        return "providers.groq.api_key"
    if key == "ai_provider":
        return "active_provider"
    if key == "model_name":
        provider = config.get("active_provider") or "groq"
        if provider == "disabled":
            provider = "groq"
        return f"providers.{provider}.model_name"
    if key == "api_base_url":
        provider = config.get("active_provider") or "groq"
        if provider == "disabled":
            provider = "groq"
        return f"providers.{provider}.api_base_url"
    return key

def get_config_value(config: dict, path: str) -> Any:
    """Retrieve a configuration value using a dot-separated path (e.g. 'providers.groq.api_key')"""
    path = translate_legacy_key(config, path)
    parts = path.split(".")
    curr = config
    for part in parts:
        if isinstance(curr, dict) and part in curr:
            curr = curr[part]
        else:
            return None
    return curr

def set_config_value(config: dict, path: str, value: Any) -> None:
    """Set a configuration value using a dot-separated path, creating parent dicts if needed"""
    path = translate_legacy_key(config, path)
    parts = path.split(".")
    curr = config
    for part in parts[:-1]:
        if part not in curr or not isinstance(curr[part], dict):
            curr[part] = {}
        curr = curr[part]
    curr[parts[-1]] = value

def _open_lock_file(lock_path: str) -> int:
    """Open the lock file, retrying on transient permission errors (Windows)."""
    fd = None
    last_err = None
    for attempt in range(100):
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
            return fd
        except OSError as e:
            last_err = e
            if e.errno != 13:  # EACCES / Permission denied
                raise
            import time
            delay = min(0.01 + (attempt * 0.005) + (random.random() * 0.01), 0.25)
            time.sleep(delay)
    raise last_err  # type: ignore[misc]


def _atomic_write_config(config_path: str, config: dict, lock_fd: int) -> None:
    """Write config to disk atomically using an already-acquired lock_fd.

    The caller is responsible for holding and releasing the lock around this call.
    """
    tmp_path = None
    try:
        dir_name = os.path.dirname(config_path)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)

        fd_tmp, tmp_path = tempfile.mkstemp(
            dir=dir_name or ".", prefix=".config.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd_tmp, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=4)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, config_path)
            tmp_path = None
        except Exception as e:
            logger.error("Failed to write config file '%s': %s", config_path, e)
        finally:
            if tmp_path is not None and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
    except OSError as e:
        logger.error("Failed to write config file '%s': %s", config_path, e)


def load_config() -> dict:
    """Load configuration dictionary from disk, returning defaults and migrating legacy config if needed"""
    config_path = get_config_path()
    lock_path = get_config_lock_path()
    defaults = {
        "ai_enabled": False,
        "active_provider": "disabled",  # "groq", "openai", "ollama", "disabled"
        "request_timeout_seconds": 30,
        "ai_max_failures": 3,
        "ai_cooldown_seconds": 60.0,
        "providers": {
            "groq": {
                "api_key": "",
                "api_base_url": "https://api.groq.com/openai/v1",
                "model_name": "llama-3.1-8b-instant"
            },
            "openai": {
                "api_key": "",
                "api_base_url": "https://api.openai.com/v1",
                "model_name": "gpt-4o-mini"
            },
            "ollama": {
                "api_key": "",
                "api_base_url": "http://localhost:11434/v1",
                "model_name": "llama3"
            }
        },
        "has_seen_onboarding": False,
        "has_seen_timestamp_prompt": False,
        "has_seen_onboarding_reminder": False,
        "max_history_age": 5,
        "max_query_log": 10000,
        "db_timeout": 30.0,
        "tool_keywords": [
            "rustc", "cargo", "go", "python3", "python", "pip", "npm", "yarn",
            "node", "docker", "docker-compose", "kubectl", "pytest", "git",
            "clang", "gcc", "make", "cmake", "mvn", "gradle", "java", "sqlite3", "psql"
        ],
        "reminder_poll_interval": 300,
        "clustering_threshold": 0.6,
        "default_branch_names": ["main"],
        "project_roots": [
    "~/Projects",
    "~/src",
    "~/Developer",
    "~/Code",
    "~/Work",
    "~",
],
        "nfs_timeout_cache_ttl": 60,
    }

    # The shared lock covers the entire read→migrate→save sequence so that
    # concurrent readers and writers (and concurrent migrations) cannot
    # interleave and overwrite each other.
    fd = None
    try:
        fd = _open_lock_file(lock_path)
        _acquire_lock(fd)
    except OSError as e:
        logger.warning("Could not acquire config lock: %s", e)
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        return dict(defaults)

    try:
        config = {}

        if os.path.exists(config_path):
            f = None
            fd_config = None
            try:
                fd_config = os.open(config_path, os.O_RDONLY)
                f = os.fdopen(fd_config, "r", encoding="utf-8")
                config = json.load(f)
            except (
                json.JSONDecodeError,
                UnicodeDecodeError,
                ValueError,
                RecursionError,
            ) as e:
                logger.warning(
                    "Config file '%s' contains invalid data and will be ignored (%s).",
                    config_path,
                    e,
                )
                config = {}
            except OSError as e:
                logger.warning(
                    "Could not read config file '%s': %s",
                    config_path,
                    e,
                )
                config = {}
            finally:
                if f is not None:
                    try:
                        f.close()
                    except OSError:
                        pass
                elif fd_config is not None:
                    try:
                        os.close(fd_config)
                    except OSError:
                        pass

        if not isinstance(config, dict):
            config = {}

        # 2. Perform legacy key migrations (while holding the shared lock)
        migrated = False
        if "ai_provider" in config:
            config["active_provider"] = config.pop("ai_provider")
            migrated = True
        if "groq_api_key" in config:
            if "providers" not in config:
                config["providers"] = {}
            if "groq" not in config["providers"]:
                config["providers"]["groq"] = {}
            config["providers"]["groq"]["api_key"] = config.pop("groq_api_key")
            migrated = True

        if "api_base_url" in config:
            val = config.pop("api_base_url")
            prov = config.get("active_provider") or "groq"
            if prov == "disabled":
                prov = "groq"
            if "providers" not in config:
                config["providers"] = {}
            if prov not in config["providers"]:
                config["providers"][prov] = {}
            config["providers"][prov]["api_base_url"] = val
            migrated = True

        if "model_name" in config:
            val = config.pop("model_name")
            prov = config.get("active_provider") or "groq"
            if prov == "disabled":
                prov = "groq"
            if "providers" not in config:
                config["providers"] = {}
            if prov not in config["providers"]:
                config["providers"][prov] = {}
            config["providers"][prov]["model_name"] = val
            migrated = True

        # 3. Recursively merge defaults
        def merge_defaults(tgt: dict, src: dict) -> bool:
            changed = False
            for k, v in src.items():
                if k not in tgt:
                    tgt[k] = json.loads(json.dumps(v))
                    changed = True
                elif isinstance(v, dict) and isinstance(tgt[k], dict):
                    if merge_defaults(tgt[k], v):
                        changed = True
            return changed

        defaults_merged = merge_defaults(config, defaults)

        # 4. Persist any changes atomically while still holding the same lock.
        #    _atomic_write_config reuses the already-acquired lock_fd, so we
        #    do not attempt to re-acquire it here.
        if migrated or defaults_merged:
            _atomic_write_config(config_path, config, fd)

    finally:
        if fd is not None:
            try:
                _release_lock(fd)
            except OSError:
                pass
            try:
                os.close(fd)
            except OSError:
                pass

    return config


def save_config(config: dict) -> None:
    """Save configuration dictionary to disk atomically"""
    config_path = get_config_path()
    lock_path = get_config_lock_path()
    fd = None
    try:
        fd = _open_lock_file(lock_path)
        _acquire_lock(fd)

        dir_name = os.path.dirname(config_path)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)
        
        # Re-read existing config to prevent lost updates from concurrent migrations
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                config = _deep_merge(existing, config)
            except (json.JSONDecodeError, OSError):
                pass
        
        tmp_path = None
        fd_tmp, tmp_path = tempfile.mkstemp(
            dir=dir_name or ".", prefix=".config.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd_tmp, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=4)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, config_path)
            tmp_path = None
        except Exception as e:
            logger.error("Failed to write config file '%s': %s", config_path, e)
        finally:
            if tmp_path is not None and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
    except OSError as e:
        logger.error("Failed to acquire lock or write config file '%s': %s", config_path, e)
    finally:
        if fd is not None:
            try:
                _release_lock(fd)
            except OSError:
                pass
            try:
                os.close(fd)
            except OSError:
                pass