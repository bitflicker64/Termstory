import os
import json
import sys
from typing import List, Any

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

def load_config() -> dict:
    """Load configuration dictionary from disk, returning defaults and migrating legacy config if needed"""
    config_path = get_config_path()
    defaults = {
        "ai_enabled": False,
        "active_provider": "disabled",  # "groq", "openai", "ollama", "disabled"
        "request_timeout_seconds": 30,
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
        "tool_keywords": [
            "rustc", "cargo", "go", "python3", "python", "pip", "npm", "yarn",
            "node", "docker", "docker-compose", "kubectl", "pytest", "git",
            "clang", "gcc", "make", "cmake", "mvn", "gradle", "java", "sqlite3", "psql"
        ],
        "reminder_poll_interval": 300,
        "default_branch_names": ["main"]
    }
    
    config = {}

    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)

        except (
            json.JSONDecodeError,
            UnicodeDecodeError,
            OSError,
            ValueError,
            RecursionError,
        ) as e:
            print(
                f"Warning: config file '{config_path}' contains invalid data and will be ignored ({e}).",
                file=sys.stderr,
            )
            config = {}

        except OSError as e:
            print(
                f"Warning: could not read config file '{config_path}': {e}",
                file=sys.stderr,
            )
            config = {}

    if not isinstance(config, dict):
        config = {}
            
    # 2. Perform legacy key migrations
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
    
    if migrated or defaults_merged:
        save_config(config)
        
    return config


def save_config(config: dict) -> None:
    """Save configuration dictionary to disk"""
    config_path = get_config_path()
    try:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4)
    except Exception:
        pass
