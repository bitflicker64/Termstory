import os
import sys
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch

from termstory.stats import detect_project_language_from_files, _LANG_CACHE


@pytest.fixture(autouse=True)
def clear_cache():
    _LANG_CACHE.clear()


def test_detect_language_with_tilde_expansion(tmp_path, monkeypatch):
    monkeypatch.setattr("os.path.expanduser", lambda path: str(tmp_path) if path == "~" else path)
    proj_dir = tmp_path / "Projects" / "my-python-project"
    proj_dir.mkdir(parents=True)
    (proj_dir / "pyproject.toml").touch()
    result = detect_project_language_from_files("~/Projects/my-python-project")
    assert result == "Python"
    assert "~/Projects/my-python-project" in _LANG_CACHE or str(proj_dir) in _LANG_CACHE


def test_detect_language_absolute_path(tmp_path):
    proj_dir = tmp_path / "node-project"
    proj_dir.mkdir(parents=True)
    (proj_dir / "package.json").touch()
    result = detect_project_language_from_files(str(proj_dir))
    assert result == "JavaScript/TypeScript"


def test_detect_language_nonexistent_path():
    result = detect_project_language_from_files("/nonexistent/path/xyz")
    assert result is None


def test_detect_language_cache_hit(tmp_path):
    proj_dir = tmp_path / "rust-project"
    proj_dir.mkdir(parents=True)
    (proj_dir / "Cargo.toml").touch()
    result1 = detect_project_language_from_files(str(proj_dir))
    assert result1 == "Rust"
    (proj_dir / "Cargo.toml").unlink()
    result2 = detect_project_language_from_files(str(proj_dir))
    assert result2 == "Rust"


def test_detect_language_network_mount_blacklist():
    assert detect_project_language_from_files("/mnt/stale_nfs/project") is None
    assert detect_project_language_from_files("/Volumes/smb/share") is None
    assert detect_project_language_from_files("//Server/Share/project") is None


def test_detect_language_csharp_csproj(tmp_path):
    proj_dir = tmp_path / "csharp-project"
    proj_dir.mkdir(parents=True)
    (proj_dir / "myapp.csproj").touch()
    assert detect_project_language_from_files(str(proj_dir)) == "C#"


def test_detect_language_csharp_sln(tmp_path):
    _LANG_CACHE.clear()
    proj_dir = tmp_path / "csharp-sln"
    proj_dir.mkdir(parents=True)
    (proj_dir / "myapp.sln").touch()
    assert detect_project_language_from_files(str(proj_dir)) == "C#"


def test_detect_language_makefile(tmp_path):
    proj_dir = tmp_path / "c-project"
    proj_dir.mkdir(parents=True)
    (proj_dir / "Makefile").touch()
    assert detect_project_language_from_files(str(proj_dir)) == "C/C++"


def test_detect_language_empty_path():
    assert detect_project_language_from_files("") is None
    assert detect_project_language_from_files(None) is None


def test_detect_language_multiple_config_files(tmp_path):
    proj_dir = tmp_path / "multi-project"
    proj_dir.mkdir(parents=True)
    (proj_dir / "Cargo.toml").touch()
    (proj_dir / "package.json").touch()
    assert detect_project_language_from_files(str(proj_dir)) == "Rust"


def test_detect_language_java_gradle(tmp_path):
    proj_dir = tmp_path / "java-project"
    proj_dir.mkdir(parents=True)
    (proj_dir / "build.gradle").touch()
    assert detect_project_language_from_files(str(proj_dir)) == "Java/Kotlin"


def test_detect_language_php_composer(tmp_path):
    proj_dir = tmp_path / "php-project"
    proj_dir.mkdir(parents=True)
    (proj_dir / "composer.json").touch()
    assert detect_project_language_from_files(str(proj_dir)) == "PHP"


def test_detect_language_ruby_gemfile(tmp_path):
    proj_dir = tmp_path / "ruby-project"
    proj_dir.mkdir(parents=True)
    (proj_dir / "Gemfile").touch()
    assert detect_project_language_from_files(str(proj_dir)) == "Ruby"
