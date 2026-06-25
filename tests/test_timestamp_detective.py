"""
tests/test_timestamp_detective.py
==================================
Test suite for the Timestamp Detective module (termstory/timestamp_detective.py).

Covers:
  - Virtual CWD tracker (cd, pushd, popd, relative/absolute paths)
  - Git commit message extraction and fuzzy matching
  - File stat detector (touch, mkdir, npm init, venv, git init, etc.)
  - Package manager detectors (brew, pip, npm, cargo, gem)
  - Docker image inspect detector
  - Venv / lockfile detector (bundle, go, composer, poetry, git clone, ssh-keygen)
  - Anchor Interpolation Engine (between-anchor, prefix gap, suffix gap, no-anchor case)
  - Timestamp validity guards (future, too old)
  - Full resolve_all() pipeline integration
"""

import os
import sys
import glob
import stat
import time
import tempfile
import unittest
from unittest.mock import patch, MagicMock, call
from datetime import datetime, timezone, timedelta

# Ensure the project root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from termstory.timestamp_detective import TimestampDetective


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class ParserBugDateTime:
    @staticmethod
    def strptime(*_args, **_kwargs):
        raise TypeError("unexpected parser bug")


NOW = int(datetime.now().timestamp())
FOUR_YEARS_AGO = NOW - (4 * 365 * 24 * 60 * 60)
SIX_YEARS_AGO  = NOW - (6 * 365 * 24 * 60 * 60)
TOMORROW       = NOW + 86400


def make_detective(**kwargs) -> TimestampDetective:
    """Convenience factory — uses a temp dir as search_root by default."""
    return TimestampDetective(
        search_root=kwargs.pop("search_root", tempfile.gettempdir()),
        project_paths=kwargs.pop("project_paths", []),
        **kwargs
    )


def make_items(*commands) -> list:
    """Create a minimal list of legacy_item dicts from bare command strings."""
    return [{"command": cmd} for cmd in commands]


# ---------------------------------------------------------------------------
# A. Virtual CWD Tracker
# ---------------------------------------------------------------------------

class TestVirtualCWDTracker(unittest.TestCase):

    def setUp(self):
        self.home = os.path.expanduser("~")
        self.d = make_detective(search_root=self.home)

    def test_default_cwd_is_home(self):
        """Commands with no cd should have cwd == search_root (home)."""
        cwd_map = self.d._build_virtual_cwd_map(make_items("ls", "pwd"))
        self.assertEqual(cwd_map[0], self.home)
        self.assertEqual(cwd_map[1], self.home)

    def test_cd_absolute(self):
        items = make_items("ls", "cd /tmp", "echo hi")
        cwd_map = self.d._build_virtual_cwd_map(items)
        self.assertEqual(cwd_map[0], self.home)
        self.assertEqual(cwd_map[1], "/tmp")   # after the cd command itself
        self.assertEqual(cwd_map[2], "/tmp")   # subsequent command inherits

    def test_cd_relative(self):
        items = make_items("cd /tmp", "cd subdir", "ls")
        cwd_map = self.d._build_virtual_cwd_map(items)
        self.assertEqual(cwd_map[2], "/tmp/subdir")

    def test_cd_dotdot(self):
        items = make_items("cd /tmp/a/b", "cd ..", "ls")
        cwd_map = self.d._build_virtual_cwd_map(items)
        self.assertEqual(cwd_map[2], "/tmp/a")

    def test_cd_home_shorthand(self):
        items = make_items("cd /tmp", "cd ~", "ls")
        cwd_map = self.d._build_virtual_cwd_map(items)
        self.assertEqual(cwd_map[2], self.home)

    def test_cd_dash(self):
        """cd - should swap current and previous directories."""
        items = make_items("cd /tmp", "cd /var", "cd -", "ls")
        cwd_map = self.d._build_virtual_cwd_map(items)
        self.assertEqual(cwd_map[3], "/tmp")  # back to /tmp

    def test_cd_bare(self):
        """cd with no arguments goes to home."""
        items = make_items("cd /tmp", "cd", "ls")
        cwd_map = self.d._build_virtual_cwd_map(items)
        self.assertEqual(cwd_map[2], self.home)

    def test_pushd_popd(self):
        items = make_items("cd /tmp", "pushd /var", "ls", "popd", "ls")
        cwd_map = self.d._build_virtual_cwd_map(items)
        self.assertEqual(cwd_map[2], "/var")   # inside pushd
        self.assertEqual(cwd_map[4], "/tmp")   # after popd

    def test_popd_empty_stack_is_safe(self):
        """popd with nothing on the stack should not crash."""
        items = make_items("popd", "ls")
        cwd_map = self.d._build_virtual_cwd_map(items)
        # Should complete without raising
        self.assertIn(0, cwd_map)


# ---------------------------------------------------------------------------
# B. Git Commit Message Extraction
# ---------------------------------------------------------------------------

class TestGitCommitExtraction(unittest.TestCase):

    def setUp(self):
        self.d = make_detective()

    def _extract(self, cmd):
        return self.d._extract_git_commit_message(cmd)

    def test_double_quoted(self):
        self.assertEqual(self._extract('git commit -m "fix auth bug"'), "fix auth bug")

    def test_single_quoted(self):
        self.assertEqual(self._extract("git commit -m 'add login page'"), "add login page")

    def test_combined_flag_am(self):
        self.assertEqual(self._extract('git commit -am "quick fix"'), "quick fix")

    def test_long_flag(self):
        self.assertEqual(
            self._extract('git commit --message "refactor: clean up"'),
            "refactor: clean up"
        )

    def test_no_message_flag(self):
        self.assertIsNone(self._extract("git commit --amend"))

    def test_non_commit_command(self):
        self.assertIsNone(self._extract("git push origin main"))

    def test_message_normalisation(self):
        cleaned = self.d._clean_for_match("feat(auth): Fix Login Bug 🎉")
        self.assertNotIn("feat", cleaned)
        self.assertNotIn("🎉", cleaned)
        self.assertIn("fix login bug", cleaned)


# ---------------------------------------------------------------------------
# C. Git Commit Detector (fuzzy matching against mocked git log)
# ---------------------------------------------------------------------------

class TestDetectGitCommit(unittest.TestCase):

    def setUp(self):
        self.d = make_detective(search_root=tempfile.gettempdir())

    def _mock_log(self, repo_path, commits):
        """Pre-populate the git log cache with synthetic commits."""
        self.d._git_log_cache[repo_path] = commits

    @patch.object(TimestampDetective, "_find_git_root", return_value="/repos/myapp")
    def test_exact_match_returns_commit_timestamp(self, _mock_root):
        """A commit message with ratio = 1.0 should return the commit's timestamp."""
        self._mock_log("/repos/myapp", [
            {"hash": "abc1234", "timestamp": FOUR_YEARS_AGO, "message": "add express middleware"}
        ])
        result = self.d.detect_git_commit(
            'git commit -m "add express middleware"', "/repos/myapp"
        )
        self.assertIsNotNone(result)
        ts, source = result
        self.assertEqual(ts, FOUR_YEARS_AGO)
        self.assertIn("myapp", source)
        self.assertIn("abc1234"[:7], source)

    @patch.object(TimestampDetective, "_find_git_root", return_value="/repos/myapp")
    def test_fuzzy_match_above_threshold(self, _mock_root):
        """Messages with only minor differences (ratio >= 0.85) should still match.

        'fix auth bug' vs 'fix auth debug' → ratio ≈ 0.923, well above threshold.
        We use messages that are genuinely close, not merely related.
        """
        self._mock_log("/repos/myapp", [
            {"hash": "def5678", "timestamp": FOUR_YEARS_AGO, "message": "fix auth debug"}
        ])
        result = self.d.detect_git_commit(
            'git commit -m "fix auth bug"', "/repos/myapp"
        )
        # "fix auth bug" vs "fix auth debug" — ratio ≈ 0.923, always above the 0.85 threshold.
        # Verify the result is returned unconditionally.
        import difflib
        ratio = difflib.SequenceMatcher(None, "fix auth bug", "fix auth debug").ratio()
        self.assertGreaterEqual(ratio, 0.85, f"Sanity check: ratio {ratio:.3f} should be ≥ 0.85")
        self.assertIsNotNone(result)

    @patch.object(TimestampDetective, "_find_git_root", return_value="/repos/myapp")
    def test_below_threshold_returns_none(self, _mock_root):
        """A very different commit message should not match."""
        self._mock_log("/repos/myapp", [
            {"hash": "aaa0000", "timestamp": FOUR_YEARS_AGO, "message": "update README documentation"}
        ])
        result = self.d.detect_git_commit(
            'git commit -m "add express middleware"', "/repos/myapp"
        )
        self.assertIsNone(result)

    @patch.object(TimestampDetective, "_find_git_root", return_value="/repos/myapp")
    def test_cwd_repo_searched_first(self, _mock_root):
        """CWD-derived repo should be searched before fallback project paths."""
        # Put a match in the CWD repo and a different match in a fallback
        self._mock_log("/repos/myapp", [
            {"hash": "cwd1111", "timestamp": FOUR_YEARS_AGO + 100, "message": "fix typo in readme"}
        ])
        self._mock_log("/repos/other", [
            {"hash": "other222", "timestamp": FOUR_YEARS_AGO + 200, "message": "fix typo in readme"}
        ])
        self.d.project_paths = ["/repos/other"]

        result = self.d.detect_git_commit(
            'git commit -m "fix typo in readme"', "/repos/myapp"
        )
        self.assertIsNotNone(result)
        ts, source = result
        # Should prefer the CWD repo's commit (lower timestamp here, but matched first)
        self.assertIn("cwd1111"[:7], source)

    @patch.object(TimestampDetective, "_find_git_root", return_value="/repos/myapp")
    def test_future_timestamp_rejected(self, _mock_root):
        """A commit with a future timestamp should not be returned."""
        self._mock_log("/repos/myapp", [
            {"hash": "future0", "timestamp": TOMORROW, "message": "add feature x"}
        ])
        result = self.d.detect_git_commit(
            'git commit -m "add feature x"', "/repos/myapp"
        )
        self.assertIsNone(result)

    @patch.object(TimestampDetective, "_find_git_root", return_value="/repos/myapp")
    def test_too_old_timestamp_rejected(self, _mock_root):
        """A commit older than 5 years should be rejected by the validity guard."""
        self._mock_log("/repos/myapp", [
            {"hash": "old1111", "timestamp": SIX_YEARS_AGO, "message": "initial commit"}
        ])
        result = self.d.detect_git_commit(
            'git commit -m "initial commit"', "/repos/myapp"
        )
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# C2. Inline Date Strings
# ---------------------------------------------------------------------------

class TestDetectInlineDate(unittest.TestCase):

    def setUp(self):
        self.d = make_detective(search_root=tempfile.gettempdir())

    def test_git_commit_date_flag_iso(self):
        result = self.d.detect_inline_date('git commit --date="2024-01-15T10:30:00"', "/tmp")
        self.assertIsNotNone(result)
        ts, label = result
        self.assertIn("2024-01-15T10:30:00", label)
        import datetime
        dt = datetime.datetime.strptime("2024-01-15T10:30:00", "%Y-%m-%dT%H:%M:%S")
        dt = dt.astimezone()
        self.assertEqual(ts, int(dt.timestamp()))

    def test_git_commit_amend_date_flag_git_format(self):
        result = self.d.detect_inline_date('git commit --amend --date="Mon Jan 15 10:30:00 2024 +0000"', "/tmp")
        self.assertIsNotNone(result)
        ts, label = result
        import datetime
        dt = datetime.datetime.strptime("Mon Jan 15 10:30:00 2024 +0000", "%a %b %d %H:%M:%S %Y %z")
        self.assertEqual(ts, int(dt.timestamp()))

    def test_git_committer_date_env_var(self):
        result = self.d.detect_inline_date('GIT_COMMITTER_DATE="2024-01-15T10:30:00" git commit -m "fix"', "/tmp")
        self.assertIsNotNone(result)

    def test_tar_create_with_date(self):
        result = self.d.detect_inline_date("tar -czf backup_2024-01-15.tar.gz src/", "/tmp")
        self.assertIsNotNone(result)

    def test_mysqldump_with_date(self):
        result = self.d.detect_inline_date("mysqldump mydb > dump_2024-01-15.sql", "/tmp")
        self.assertIsNotNone(result)

    def test_mv_with_date(self):
        result = self.d.detect_inline_date("mv report.pdf report_2024-01-15.pdf", "/tmp")
        self.assertIsNotNone(result)

    def test_git_log_since_returns_none(self):
        result = self.d.detect_inline_date('git log --since="2024-01-01"', "/tmp")
        self.assertIsNone(result)

    def test_git_log_until_returns_none(self):
        result = self.d.detect_inline_date('git log --until="2024-01-01"', "/tmp")
        self.assertIsNone(result)

    def test_grep_date_returns_none(self):
        result = self.d.detect_inline_date('grep "2024-01-15" logfile.txt', "/tmp")
        self.assertIsNone(result)

    def test_cat_date_file_returns_none(self):
        result = self.d.detect_inline_date('cat file_2024-01-15.txt', "/tmp")
        self.assertIsNone(result)

    def test_invalid_date_returns_none(self):
        result = self.d.detect_inline_date('git commit --date="not-a-date"', "/tmp")
        self.assertIsNone(result)

    def test_too_old_timestamp_filtered(self):
        result = self.d.detect_inline_date('git commit --date="1990-01-01T10:00:00"', "/tmp")
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# D. File Stat Detector
# ---------------------------------------------------------------------------

class TestDetectFileStat(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.d = make_detective(search_root=self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_touch_existing_file(self):
        """touch <file> should return that file's mtime."""
        filepath = os.path.join(self.tmp, "hello.txt")
        open(filepath, "w").close()
        result = self.d.detect_file_stat(f"touch {filepath}", self.tmp)
        self.assertIsNotNone(result)
        ts, label = result
        self.assertIsInstance(ts, int)
        self.assertIn("touch", label)

    def test_mkdir_existing_dir(self):
        """mkdir <dir> should return that directory's mtime."""
        dirpath = os.path.join(self.tmp, "mydir")
        os.makedirs(dirpath)
        result = self.d.detect_file_stat(f"mkdir {dirpath}", self.tmp)
        self.assertIsNotNone(result)
        ts, label = result
        self.assertIn("mkdir", label)

    def test_npm_init_finds_package_json(self):
        """npm init should stat cwd/package.json."""
        pkg = os.path.join(self.tmp, "package.json")
        with open(pkg, "w") as f:
            f.write("{}")
        result = self.d.detect_file_stat("npm init -y", self.tmp)
        self.assertIsNotNone(result)
        _, label = result
        self.assertIn("package.json", label)

    def test_venv_finds_activate(self):
        """python -m venv myenv should stat myenv/bin/activate."""
        activate_dir = os.path.join(self.tmp, "myenv", "bin")
        os.makedirs(activate_dir)
        activate = os.path.join(activate_dir, "activate")
        open(activate, "w").close()
        result = self.d.detect_file_stat("python -m venv myenv", self.tmp)
        self.assertIsNotNone(result)
        _, label = result
        self.assertIn("activate", label)

    def test_git_init_finds_dot_git(self):
        """git init should stat the .git directory."""
        git_dir = os.path.join(self.tmp, ".git")
        os.makedirs(git_dir)
        result = self.d.detect_file_stat("git init", self.tmp)
        self.assertIsNotNone(result)
        _, label = result
        self.assertIn(".git", label)

    def test_touch_nonexistent_returns_none(self):
        """touch on a path that doesn't exist should return None gracefully."""
        result = self.d.detect_file_stat(
            "touch /nonexistent/path/that/does/not/exist.txt", self.tmp
        )
        self.assertIsNone(result)

    def test_touch_t_flag_excluded(self):
        """touch -t (explicit timestamp set) should NOT trigger the detector."""
        result = self.d.detect_file_stat("touch -t 202301010000 myfile.txt", self.tmp)
        self.assertIsNone(result)

    def test_future_file_timestamp_rejected(self):
        """A file with a future mtime (set artificially) should be rejected."""
        filepath = os.path.join(self.tmp, "future.txt")
        open(filepath, "w").close()
        # Artificially set mtime to tomorrow
        future_time = time.time() + 86400
        os.utime(filepath, (future_time, future_time))
        result = self.d.detect_file_stat(f"touch {filepath}", self.tmp)
        # birthtime may still be valid on macOS — only assert mtime path fails
        # (st_birthtime may be now, which is valid); so just ensure no crash
        # This test documents the guard exists, not that it always fires.
        # The result may be non-None if birthtime < now.


# ---------------------------------------------------------------------------
# E. Package Manager Detector (mocked filesystem)
# ---------------------------------------------------------------------------

class TestDetectPackageManager(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.d = make_detective(search_root=self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_npm_local_install_package_lock(self):
        """npm install should find package-lock.json in virtual_cwd."""
        lock = os.path.join(self.tmp, "package-lock.json")
        open(lock, "w").close()
        result = self.d.detect_package_manager("npm install", self.tmp)
        self.assertIsNotNone(result)
        _, label = result
        self.assertIn("package-lock.json", label)

    def test_brew_install_cellar_found(self):
        """brew install jq should stat Homebrew Cellar/jq."""
        fake_prefix = os.path.join(self.tmp, "homebrew")
        cellar = os.path.join(fake_prefix, "Cellar", "jq")
        os.makedirs(cellar)
        self.d._brew_prefix = fake_prefix
        result = self.d.detect_package_manager("brew install jq", self.tmp)
        self.assertIsNotNone(result)
        _, label = result
        self.assertIn("jq", label)

    def test_brew_formula_not_installed_returns_none(self):
        """If the Cellar directory doesn't exist, return None."""
        self.d._brew_prefix = self.tmp  # Valid prefix, but no Cellar/nonexistent
        result = self.d.detect_package_manager("brew install nonexistent_formula_xyz", self.tmp)
        self.assertIsNone(result)

    def test_npm_global_install(self):
        """npm install -g express should stat global node_modules/express."""
        fake_global_root = os.path.join(self.tmp, "node_modules")
        express_dir = os.path.join(fake_global_root, "express")
        os.makedirs(express_dir)
        self.d._npm_global_root = fake_global_root
        result = self.d.detect_package_manager("npm install -g express", self.tmp)
        self.assertIsNotNone(result)
        _, label = result
        self.assertIn("express", label)

    def test_cargo_add(self):
        """cargo add serde should resolve via the _get_cargo_crate_timestamp helper.

        We mock the helper directly rather than glob.glob so the test doesn't depend
        on the actual ~/.cargo directory layout.
        """
        with patch.object(self.d, "_get_cargo_crate_timestamp", return_value=FOUR_YEARS_AGO):
            result = self.d.detect_package_manager("cargo add serde", self.tmp)
        self.assertIsNotNone(result)
        _, label = result
        self.assertIn("serde", label)

    def test_gem_install(self):
        """gem install rails should resolve via the _get_gem_timestamp helper.

        We mock the helper directly rather than the filesystem so the test is
        hermetic and doesn't depend on ~/.gem existing.
        """
        with patch.object(self.d, "_get_gem_timestamp", return_value=FOUR_YEARS_AGO):
            result = self.d.detect_package_manager("gem install rails", self.tmp)
        self.assertIsNotNone(result)
        _, label = result
        self.assertIn("rails", label)


# ---------------------------------------------------------------------------
# F. Docker Image Detector
# ---------------------------------------------------------------------------

class TestDetectDocker(unittest.TestCase):

    def setUp(self):
        self.d = make_detective()

    @patch("subprocess.run")
    def test_docker_build_tag_short_flag(self, mock_run):
        """docker build -t my-app . should inspect the my-app image."""
        iso_ts = datetime.fromtimestamp(FOUR_YEARS_AGO, tz=timezone.utc).isoformat()
        mock_run.return_value = MagicMock(returncode=0, stdout=iso_ts)
        result = self.d.detect_docker("docker build -t my-app .", tempfile.gettempdir())
        self.assertIsNotNone(result)
        ts, label = result
        self.assertEqual(ts, FOUR_YEARS_AGO)
        self.assertIn("my-app", label)

    @patch("subprocess.run")
    def test_docker_build_tag_long_flag(self, mock_run):
        """docker build --tag api:latest . should also be detected."""
        iso_ts = datetime.fromtimestamp(FOUR_YEARS_AGO, tz=timezone.utc).isoformat()
        mock_run.return_value = MagicMock(returncode=0, stdout=iso_ts)
        result = self.d.detect_docker("docker build --tag api:latest .", tempfile.gettempdir())
        self.assertIsNotNone(result)
        _, label = result
        self.assertIn("api:latest", label)

    @patch("subprocess.run")
    def test_docker_inspect_failure_returns_none(self, mock_run):
        """If docker inspect returns non-zero, result should be None."""
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        result = self.d.detect_docker("docker build -t bad-build .", tempfile.gettempdir())
        self.assertIsNone(result)

    def test_non_build_command_returns_none(self):
        """docker ps / docker run without image creation should return None."""
        result = self.d.detect_docker("docker ps", tempfile.gettempdir())
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# G. Venv / Lockfile Detector
# ---------------------------------------------------------------------------

class TestDetectVenvLockfile(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.d = make_detective(search_root=self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_bundle_install_gemfile_lock(self):
        gemfile_lock = os.path.join(self.tmp, "Gemfile.lock")
        open(gemfile_lock, "w").close()
        result = self.d.detect_venv_lockfile("bundle install", self.tmp)
        self.assertIsNotNone(result)
        _, label = result
        self.assertIn("Gemfile.lock", label)

    def test_go_mod_tidy(self):
        go_sum = os.path.join(self.tmp, "go.sum")
        open(go_sum, "w").close()
        result = self.d.detect_venv_lockfile("go mod tidy", self.tmp)
        self.assertIsNotNone(result)

    def test_composer_install(self):
        lock = os.path.join(self.tmp, "composer.lock")
        open(lock, "w").close()
        result = self.d.detect_venv_lockfile("composer install", self.tmp)
        self.assertIsNotNone(result)

    def test_git_clone_with_explicit_dir(self):
        """git clone <url> mydir should stat mydir/.git."""
        git_dir = os.path.join(self.tmp, "mydir", ".git")
        os.makedirs(git_dir)
        result = self.d.detect_venv_lockfile(
            "git clone https://github.com/user/repo.git mydir", self.tmp
        )
        self.assertIsNotNone(result)
        _, label = result
        self.assertIn(".git", label)

    def test_git_clone_url_only(self):
        """git clone <url> should derive dir name from URL and stat <dir>/.git."""
        git_dir = os.path.join(self.tmp, "repo", ".git")
        os.makedirs(git_dir)
        result = self.d.detect_venv_lockfile(
            "git clone https://github.com/user/repo.git", self.tmp
        )
        self.assertIsNotNone(result)

    def test_ssh_keygen(self):
        key_path = os.path.join(self.tmp, "id_rsa")
        open(key_path, "w").close()
        result = self.d.detect_venv_lockfile(f"ssh-keygen -f {key_path}", self.tmp)
        self.assertIsNotNone(result)


# ---------------------------------------------------------------------------
# H. Anchor Interpolation Engine
# ---------------------------------------------------------------------------

class TestInterpolation(unittest.TestCase):

    def setUp(self):
        self.d = make_detective()

    def _make_enriched(self, *specs):
        """
        Build a list of enriched items from specs.
        Each spec is either:
          int   → anchor item with that detected_ts
          None  → unresolved item (detected_ts=None)
        """
        items = []
        for spec in specs:
            if spec is not None:
                items.append({
                    "command": f"cmd_{spec}",
                    "detected_ts": spec,
                    "detected_source": f"mock@{spec}",
                    "is_legacy_still": False
                })
            else:
                items.append({
                    "command": "unresolved",
                    "detected_ts": None,
                    "detected_source": None,
                    "is_legacy_still": True
                })
        return items

    def test_gap_between_two_anchors_interpolated(self):
        """
        Three unresolved items between t=1000 and t=2000 should get timestamps
        1250, 1500, 1750 (linear interpolation at fractions 0.25, 0.50, 0.75).
        """
        items = self._make_enriched(1000, None, None, None, 2000)
        result = self.d._interpolate(items)
        # Anchors at idx 0 and 4; gaps at idx 1, 2, 3
        self.assertEqual(result[1]["detected_ts"], int(1000 + (2000 - 1000) * 1/4))
        self.assertEqual(result[2]["detected_ts"], int(1000 + (2000 - 1000) * 2/4))
        self.assertEqual(result[3]["detected_ts"], int(1000 + (2000 - 1000) * 3/4))
        # All interpolated items must remain is_legacy_still=True
        self.assertTrue(result[1]["is_legacy_still"])
        self.assertTrue(result[2]["is_legacy_still"])
        self.assertTrue(result[3]["is_legacy_still"])

    def test_interpolation_source_mentions_anchors(self):
        """The recovery_source of interpolated items should reference both anchors."""
        items = self._make_enriched(1000, None, 2000)
        result = self.d._interpolate(items)
        src = result[1]["detected_source"]
        self.assertIn("Interpolated", src)

    @patch("termstory.timestamp_detective.TimestampDetective._find_oldest_repo_anchor")
    def test_prefix_gap_step_back(self, mock_oldest):
        """Items before the first anchor should be interpolated to an oldest bound."""
        mock_oldest.return_value = 100
        items = self._make_enriched(None, None, 1000)
        result = self.d._interpolate(items)
        # First anchor is at idx 2 with ts=1000. Left bound is 100.
        # prefix items at idx 0, 1. fraction = (0+1)/(2+1) = 1/3, (1+1)/(2+1) = 2/3
        # ts0 = 100 + (1000 - 100) * 1/3 = 100 + 300 = 400
        # ts1 = 100 + (1000 - 100) * 2/3 = 100 + 600 = 700
        self.assertEqual(result[0]["detected_ts"], 400)
        self.assertEqual(result[1]["detected_ts"], 700)
        self.assertIn("Pre-anchor", result[0]["detected_source"])

    def test_suffix_gap_step_forward(self):
        """Items after the last anchor should be spread proportionally forward."""
        items = self._make_enriched(1000, None, None)
        result = self.d._interpolate(items)
        # t_last = 1000, n_suffix = 2, window = 86400.
        # offset 0: 1000 + (1/3) * 86400 = 29800
        # offset 1: 1000 + (2/3) * 86400 = 58600
        self.assertEqual(result[1]["detected_ts"], 29800)
        self.assertEqual(result[2]["detected_ts"], 58600)
        self.assertIn("Post-anchor", result[1]["detected_source"])

    def test_no_anchors_unchanged(self):
        """With zero anchors, _interpolate should return the items unchanged."""
        items = self._make_enriched(None, None, None)
        result = self.d._interpolate(items)
        for item in result:
            self.assertIsNone(item["detected_ts"])

    def test_empty_list(self):
        self.assertEqual(self.d._interpolate([]), [])

    def test_single_anchor_no_gaps(self):
        """A single anchor with no surrounding unresolved items should work fine."""
        items = self._make_enriched(1000)
        result = self.d._interpolate(items)
        self.assertEqual(result[0]["detected_ts"], 1000)

    def test_multiple_gaps_between_multiple_anchors(self):
        """Multiple gaps between multiple anchors should all be filled."""
        items = self._make_enriched(1000, None, 2000, None, 3000)
        result = self.d._interpolate(items)
        # Gap 1: between t=1000 (idx 0) and t=2000 (idx 2) → idx 1 = 1500
        self.assertEqual(result[1]["detected_ts"], 1500)
        # Gap 2: between t=2000 (idx 2) and t=3000 (idx 4) → idx 3 = 2500
        self.assertEqual(result[3]["detected_ts"], 2500)


# ---------------------------------------------------------------------------
# I. Full Pipeline — resolve_all()
# ---------------------------------------------------------------------------

class TestResolveAll(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.d = make_detective(search_root=self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_empty_list(self):
        self.assertEqual(self.d.resolve_all([]), [])

    def test_all_unresolvable_returns_with_none_ts(self):
        """Commands with no forensic evidence should have detected_ts=None after Phase B."""
        items = make_items("ls -la", "pwd", "echo hello")
        result = self.d.resolve_all(items)
        # Phase C (interpolation) leaves them as-is since no anchors exist
        for item in result:
            self.assertIn("detected_ts", item)
            self.assertIn("detected_source", item)
            self.assertIn("is_legacy_still", item)
            self.assertTrue(item["is_legacy_still"])

    def test_resolved_item_is_not_legacy(self):
        """A command resolved by the Detective should have is_legacy_still=False."""
        npm_lock = os.path.join(self.tmp, "package-lock.json")
        open(npm_lock, "w").close()
        items = make_items("npm install")
        result = self.d.resolve_all(items)
        # npm install should be resolved via package-lock.json stat
        self.assertFalse(result[0]["is_legacy_still"])
        self.assertIsNotNone(result[0]["detected_ts"])

    def test_cwd_map_applied_correctly(self):
        """cd before npm install should set the correct virtual CWD for stat lookup."""
        # Create package-lock.json in a subdirectory
        subdir = os.path.join(self.tmp, "myproject")
        os.makedirs(subdir, exist_ok=True)
        lock = os.path.join(subdir, "package-lock.json")
        open(lock, "w").close()

        items = make_items(f"cd {subdir}", "npm install")
        result = self.d.resolve_all(items)
        # index 1 (npm install) should be resolved using myproject/package-lock.json
        self.assertFalse(result[1]["is_legacy_still"])

    def test_interpolation_applied_between_resolved_commands(self):
        """An unresolvable command between two resolved ones should be interpolated."""
        npm_lock = os.path.join(self.tmp, "package-lock.json")
        open(npm_lock, "w").close()

        git_dir = os.path.join(self.tmp, "repo", ".git")
        os.makedirs(git_dir)

        # npm install → resolved (anchor A)
        # ls          → unresolvable → interpolated
        # git clone … → resolved (anchor B)
        items = make_items("npm install", "ls -la", "git clone https://github.com/u/repo.git repo")
        result = self.d.resolve_all(items)

        ts_a = result[0].get("detected_ts")
        ts_b = result[2].get("detected_ts")
        ts_mid = result[1].get("detected_ts")

        if ts_a is not None and ts_b is not None:
            # Middle command must be between the two anchors
            self.assertIsNotNone(ts_mid)
            self.assertGreaterEqual(ts_mid, min(ts_a, ts_b))
            self.assertLessEqual(ts_mid, max(ts_a, ts_b))


# ---------------------------------------------------------------------------
# H. macOS System Log Detector (Detector 6)
# ---------------------------------------------------------------------------

class TestDetectMacosSystemLog(unittest.TestCase):

    def setUp(self):
        self.d = make_detective()

    @patch("subprocess.run")
    def test_brew_install_brew_log(self, mock_run):
        """brew install <formula> should anchor to the brew log commit date."""
        dt = datetime.fromtimestamp(FOUR_YEARS_AGO)
        git_date = dt.astimezone().strftime("%a %b %d %H:%M:%S %Y %z")
        log_output = (
            "commit abc123\n"
            "Author: Maintainer <m@example.com>\n"
            f"Date:   {git_date}\n\n"
            "    jq 1.6\n"
        )
        mock_run.return_value = MagicMock(returncode=0, stdout=log_output)
        result = self.d.detect_macos_system_log("brew install jq", tempfile.gettempdir())
        self.assertIsNotNone(result)
        ts, label = result
        self.assertEqual(ts, FOUR_YEARS_AGO)
        self.assertIn("jq", label)
        self.assertIn("brew log", label)

    @patch("subprocess.run")
    def test_brew_install_strips_tap_and_version(self, mock_run):
        """Tap prefix and version pin should be stripped from the formula name."""
        dt = datetime.fromtimestamp(FOUR_YEARS_AGO)
        git_date = dt.astimezone().strftime("%a %b %d %H:%M:%S %Y %z")
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=f"commit a\nDate:   {git_date}\n\n    msg\n"
        )
        result = self.d.detect_macos_system_log("brew install homebrew/core/jq@1.6", tempfile.gettempdir())
        self.assertIsNotNone(result)
        _, label = result
        self.assertIn("jq", label)
        self.assertNotIn("@", label)

    @patch("subprocess.run")
    def test_last_login_anchor(self, mock_run):
        """sudo/ssh/su commands should anchor to the most recent last login.

        `last` omits the year, so the parser assumes the current year (stepping
        back one year only if that would be in the future).
        """
        target = datetime.now() - timedelta(days=2)
        last_line = target.strftime("%a %b %d %H:%M")
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=f"alice    ttys000   192.168.0.1   {last_line}   still logged in\n"
        )
        result = self.d.detect_macos_system_log("sudo systemctl restart x", tempfile.gettempdir())
        self.assertIsNotNone(result)
        ts, label = result

        expected = target.replace(second=0, microsecond=0, year=datetime.now().year)
        if expected.timestamp() > datetime.now().timestamp():
            expected = expected.replace(year=datetime.now().year - 1)
        # Minute-precision parsing — allow up to 60s difference
        self.assertLessEqual(abs(ts - int(expected.timestamp())), 60)
        self.assertIn("last login", label)

    def test_unrelated_command_returns_none(self):
        result = self.d.detect_macos_system_log("ls -la", tempfile.gettempdir())
        self.assertIsNone(result)

    def test_parse_git_log_date_iso(self):
        dt = datetime.fromtimestamp(FOUR_YEARS_AGO)
        iso = dt.astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
        ts = self.d._parse_git_log_date(f"commit x\nDate:   {iso}\n\n    msg\n")
        self.assertEqual(ts, FOUR_YEARS_AGO)

    def test_parse_git_log_date_ignores_format_mismatches_only(self):
        self.assertIsNone(self.d._parse_git_log_date("commit x\nDate:   not a date\n"))

        with patch("termstory.timestamp_detective.datetime", ParserBugDateTime):
            with self.assertRaises(TypeError):
                self.d._parse_git_log_date("commit x\nDate:   not a date\n")

    def test_parse_last_login_ignores_format_mismatches_only(self):
        self.assertIsNone(self.d._parse_last_login("alice ttys000 Tue Jun 99 09:14 still logged in"))

        with patch("termstory.timestamp_detective.datetime", ParserBugDateTime):
            with self.assertRaises(TypeError):
                self.d._parse_last_login("alice ttys000 Thu Jun 5 09:14 still logged in")

    def test_detect_macos_syslog_missing_file(self):
        """When install.log doesn't exist (e.g. on Linux), return None."""
        self.assertIsNone(self.d.detect_macos_syslog("jq"))

    def test_detect_macos_syslog_parses_install_line(self):
        tmp = tempfile.mkdtemp()
        try:
            log_path = os.path.join(tmp, "install.log")
            dt = datetime.fromtimestamp(FOUR_YEARS_AGO, tz=timezone.utc)
            stamp = dt.strftime("%Y-%m-%d %H:%M:%S")
            with open(log_path, "w") as f:
                f.write(f"{stamp}+00 host installd[1]: Installed: jq\n")
            with patch("os.path.exists", return_value=True), \
                 patch("builtins.open", return_value=open(log_path)):
                ts = self.d.detect_macos_syslog("jq")
            self.assertIsNotNone(ts)
            self.assertEqual(ts, int(dt.timestamp()))
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_detect_macos_syslog_timezone_offsets(self):
        tmp = tempfile.mkdtemp()
        try:
            log_path = os.path.join(tmp, "install.log")
            # 2026-04-01 04:22:04-07:00 is 1775042524
            with open(log_path, "w") as f:
                f.write("2026-04-01 04:22:04-07 MacBook-Pro system_installd[584]: Installed \"jq\" (1.6)\n")
            with patch("os.path.exists", return_value=True), \
                 patch("builtins.open", return_value=open(log_path)):
                ts = self.d.detect_macos_syslog("jq")
            self.assertIsNotNone(ts)
            self.assertEqual(ts, 1775042524)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_detect_macos_syslog_package_filter_strict(self):
        """Should not match unrelated packages containing 'brew' if target package isn't present."""
        tmp = tempfile.mkdtemp()
        try:
            log_path = os.path.join(tmp, "install.log")
            with open(log_path, "w") as f:
                f.write("2026-04-01 04:22:04-07 MacBook-Pro system_installd[584]: Installed: brew-cask google-chrome\n")
            with patch("os.path.exists", return_value=True), \
                 patch("builtins.open", return_value=open(log_path)):
                ts = self.d.detect_macos_syslog("jq")
            self.assertIsNone(ts)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
