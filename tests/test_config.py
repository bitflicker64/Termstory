import os
import json
import pytest
from unittest.mock import patch, mock_open

from termstory.config import load_config, save_config, get_config_path

def test_api_base_url_is_configurable_per_provider(tmp_path, monkeypatch):
    """#152: the api_base_url for every provider must come from config and
    be overridable via ``termstory config set providers.<p>.api_base_url``.
    Hardcoded fallbacks (the old behaviour in cli.py / tui.py) silently
    ignored user customisations.
    """
    config_file = tmp_path / "config.json"
    config_data = {
        "active_provider": "groq",
        "providers": {
            "groq": {
                "api_key": "fake-key",
                # User overrides Groq to point at a self-hosted proxy
                "api_base_url": "https://my-proxy.example.com/openai/v1",
                "model_name": "llama-3.1-8b-instant",
            },
        },
    }
    config_file.write_text(json.dumps(config_data))

    with patch("termstory.config.get_config_path", return_value=str(config_file)):
        config = load_config()

        # get_config_value must use dot-path traversal (dict.get() does NOT)
        from termstory.config import get_config_value

        url = get_config_value(config, "providers.groq.api_base_url")
        assert url == "https://my-proxy.example.com/openai/v1", (
            "Configured api_base_url was not returned by get_config_value() "
            "(regression of #152 dot-notation lookup bug)"
        )

        # Other providers must still return their defaults after merge
        openai_url = get_config_value(config, "providers.openai.api_base_url")
        assert openai_url == "https://api.openai.com/v1"

        ollama_url = get_config_value(config, "providers.ollama.api_base_url")
        assert ollama_url == "http://localhost:11434/v1"

        # The helper that ai-aware callers use (cli.get_ai_provider_settings)
        # must surface the custom URL, not a hardcoded fallback.
        from termstory.cli import get_ai_provider_settings

        provider, _api_key, base_url, _model = get_ai_provider_settings(config)
        assert provider == "groq"
        assert base_url == "https://my-proxy.example.com/openai/v1"


def test_load_config_corrupted_file(tmp_path):
    # Mock get_config_path to point to our tmp_path
    config_file = tmp_path / "config.json"
    
    # Write corrupted JSON
    config_file.write_text("{corrupted_json: [")
    
    with patch("termstory.config.get_config_path", return_value=str(config_file)):
        config = load_config()
        
        # It should fallback to defaults
        assert config["ai_enabled"] is False
        assert config["active_provider"] == "disabled"
        assert config["providers"]["groq"]["model_name"] == "llama-3.1-8b-instant"

def test_load_config_missing_file(tmp_path):
    config_file = tmp_path / "config.json"
    
    with patch("termstory.config.get_config_path", return_value=str(config_file)):
        config = load_config()
        
        assert config["ai_enabled"] is False
        assert config["active_provider"] == "disabled"
        assert config["max_history_age"] == 5
        assert config["max_query_log"] == 10000

def test_save_config_error_handling(tmp_path):
    # Write failures must surface so callers can report them.
    with patch("termstory.config.get_config_path", return_value="/invalid/path/that/does/not/exist/config.json"):
        with pytest.raises(OSError):
            save_config({"test": "data"})


@pytest.mark.skipif(os.geteuid() == 0, reason="chmod is a no-op for root")
def test_save_config_readonly_dir_raises(tmp_path, monkeypatch):
    """A read-only config directory must not look like a successful save."""
    config_dir = tmp_path / "termstory"
    config_dir.mkdir()
    config_file = config_dir / "config.json"
    monkeypatch.setattr("termstory.config.get_config_path", lambda: str(config_file))
    os.chmod(config_dir, 0o555)
    try:
        with pytest.raises(OSError):
            save_config({"ai_enabled": True})
        assert not config_file.exists()
    finally:
        os.chmod(config_dir, 0o755)

def test_env_var_overrides(tmp_path, monkeypatch):
    from termstory.config import get_db_path, get_history_files
    
    # Test DB_PATH override
    db_file = tmp_path / "custom_db.db"
    monkeypatch.setenv("DB_PATH", str(db_file))
    assert get_db_path() == os.path.realpath(str(db_file))
    
    # Test HISTORY_FILES override
    hist_file_1 = tmp_path / "hist1"
    hist_file_2 = tmp_path / "hist2"
    hist_file_1.write_text("cmd1\n")
    hist_file_2.write_text("cmd2\n")
    
    monkeypatch.setenv("HISTORY_FILES", f"{hist_file_1},{hist_file_2}")
    files = get_history_files()
    assert len(files) == 2
    assert os.path.realpath(str(hist_file_1)) in files
    assert os.path.realpath(str(hist_file_2)) in files


def test_atomic_write_text_writes_through_symlinks(tmp_path):
    """A config symlinked into a dotfiles repo must stay a symlink after a write."""
    from termstory.config import atomic_write_text

    target = tmp_path / "real.yaml"
    target.write_text("old\n")
    link = tmp_path / "link.yaml"
    link.symlink_to(target)

    atomic_write_text(str(link), "new\n")

    assert link.is_symlink(), "atomic write replaced the symlink with a regular file"
    assert target.read_text() == "new\n"


def test_atomic_write_text_preserves_existing_mode(tmp_path):
    """mkstemp() creates 0600; an existing file must not be narrowed on every write."""
    from termstory.config import atomic_write_text

    path = tmp_path / "config.yaml"
    path.write_text("old\n")
    os.chmod(path, 0o644)

    atomic_write_text(str(path), "new\n")

    assert oct(path.stat().st_mode & 0o777) == "0o644"
    assert path.read_text() == "new\n"


def test_atomic_write_text_new_file_is_private(tmp_path):
    """A file created from scratch keeps mkstemp's restrictive default."""
    from termstory.config import atomic_write_text

    path = tmp_path / "nested" / ".env"
    atomic_write_text(str(path), "SECRET=1\n")

    assert path.read_text() == "SECRET=1\n"
    assert oct(path.stat().st_mode & 0o777) == "0o600"
