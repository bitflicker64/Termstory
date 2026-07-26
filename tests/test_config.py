import os
import json
import multiprocessing
from unittest.mock import patch

from termstory.config import load_config, save_config


def test_load_config_corrupted_file(tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text("{corrupted_json: [")

    with patch("termstory.config.get_config_path", return_value=str(config_file)):
        config = load_config()
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
    config_file = tmp_path / "config.json"
    lock_file = tmp_path / "config.lock"
    with patch("termstory.config.get_config_path", return_value=str(config_file)), \
         patch("termstory.config.get_config_lock_path", return_value=str(lock_file)):
        save_config({"test": "data"})  # Should silently pass

def test_env_var_overrides(tmp_path, monkeypatch):
    from termstory.config import get_db_path, get_history_files

    db_file = tmp_path / "custom_db.db"
    monkeypatch.setenv("DB_PATH", str(db_file))
    assert get_db_path() == os.path.realpath(str(db_file))

    hist_file_1 = tmp_path / "hist1"
    hist_file_2 = tmp_path / "hist2"
    hist_file_1.write_text("cmd1\n")
    hist_file_2.write_text("cmd2\n")

    monkeypatch.setenv("HISTORY_FILES", f"{hist_file_1},{hist_file_2}")
    files = get_history_files()
    assert len(files) == 2
    assert os.path.realpath(str(hist_file_1)) in files
    assert os.path.realpath(str(hist_file_2)) in files


# ---------------------------------------------------------------------------
# Issue #338: Concurrency regression tests
# ---------------------------------------------------------------------------


def _reader_worker(config_path, lock_path, queue, iterations):
    import termstory.config as cfg_mod
    cfg_mod.get_config_path = lambda: config_path
    cfg_mod.get_config_lock_path = lambda: lock_path

    for _ in range(iterations):
        try:
            config = cfg_mod.load_config()
            if not isinstance(config, dict):
                queue.put("corrupted")
                return
            if not isinstance(config.get("ai_enabled"), bool):
                queue.put("corrupted")
                return
            if not isinstance(config.get("has_seen_onboarding"), bool):
                queue.put("corrupted")
                return
        except (json.JSONDecodeError, OSError, RecursionError, ValueError):
            queue.put("corrupted")
            return

    queue.put("ok")


def _writer_worker(config_path, lock_path, queue, iterations):
    import termstory.config as cfg_mod
    cfg_mod.get_config_path = lambda: config_path
    cfg_mod.get_config_lock_path = lambda: lock_path

    states = [
        {"ai_enabled": True,  "has_seen_onboarding": True},
        {"ai_enabled": False, "has_seen_onboarding": False},
        {"ai_enabled": True,  "has_seen_onboarding": False},
        {"ai_enabled": False, "has_seen_onboarding": True},
        {"ai_enabled": True,  "has_seen_onboarding": True},
    ]

    for i in range(iterations):
        state = states[i % len(states)]
        cfg_mod.save_config(state)

    queue.put("ok")


def _combined_reader_worker(config_path, lock_path, queue, iterations):
    import termstory.config as cfg_mod
    cfg_mod.get_config_path = lambda: config_path
    cfg_mod.get_config_lock_path = lambda: lock_path

    for _ in range(iterations):
        try:
            config = cfg_mod.load_config()
            if not isinstance(config, dict):
                queue.put("corrupted")
                return
            if not isinstance(config.get("ai_enabled"), (bool, type(None))):
                queue.put("corrupted")
                return
            if not isinstance(config.get("has_seen_onboarding"), (bool, type(None))):
                queue.put("corrupted")
                return
        except (json.JSONDecodeError, OSError, RecursionError, ValueError):
            queue.put("corrupted")
            return

    queue.put("ok")


def _migration_reader(path, lk_path, queue, iterations):
    import termstory.config as cfg_mod
    cfg_mod.get_config_path = lambda: path
    cfg_mod.get_config_lock_path = lambda: lk_path

    for _ in range(iterations):
        try:
            config = cfg_mod.load_config()
            assert isinstance(config, dict)
            assert config.get("active_provider") == "groq"
            assert config.get("providers", {}).get("groq", {}).get("api_key") == "secret-key-1"
        except (json.JSONDecodeError, OSError, RecursionError, ValueError, AssertionError):
            queue.put("corrupted")
            return

    queue.put("ok")


class TestConcurrentConfigAccess:
    """Regression tests for Issue #338."""

    def test_concurrent_readers_never_observe_invalid_json(self, tmp_path):
        ctx = multiprocessing.get_context("spawn")
        config_path = str(tmp_path / "config.json")
        lock_path = str(tmp_path / "config.lock")

        with open(config_path, "w", encoding="utf-8") as fh:
            json.dump({"ai_enabled": True, "has_seen_onboarding": True}, fh)

        num_writers = 2
        num_readers = 2
        iterations = 200

        write_queue = ctx.Queue()
        read_queue = ctx.Queue()

        writers = [
            ctx.Process(target=_writer_worker, args=(config_path, lock_path, write_queue, iterations))
            for _ in range(num_writers)
        ]
        readers = [
            ctx.Process(target=_reader_worker, args=(config_path, lock_path, read_queue, iterations * 10))
            for _ in range(num_readers)
        ]

        for p in readers:
            p.start()
        for p in writers:
            p.start()

        for p in writers:
            p.join(timeout=30)
            assert not p.exitcode, "writer process crashed"

        # Give readers extra time to finish so their results land in the queue
        for p in readers:
            p.join(timeout=30)

        reader_results = []
        while not read_queue.empty():
            reader_results.append(read_queue.get_nowait())

        assert len(reader_results) == num_readers, (
            f"Expected {num_readers} reader results, got {len(reader_results)}"
        )
        assert all(r == "ok" for r in reader_results), (
            f"Concurrent readers observed corrupted config: {reader_results}"
        )

        with open(config_path, "r", encoding="utf-8") as fh:
            final = json.load(fh)
        assert isinstance(final, dict)

    def test_concurrent_writers_do_not_corrupt_config(self, tmp_path):
        ctx = multiprocessing.get_context("spawn")
        config_path = str(tmp_path / "config.json")
        lock_path = str(tmp_path / "config.lock")

        with open(config_path, "w", encoding="utf-8") as fh:
            json.dump({"ai_enabled": False, "has_seen_onboarding": False}, fh)

        num_writers = 4
        iterations = 200

        write_queue = ctx.Queue()
        writers = [
            ctx.Process(target=_writer_worker, args=(config_path, lock_path, write_queue, iterations))
            for _ in range(num_writers)
        ]

        for p in writers:
            p.start()
        for p in writers:
            p.join(timeout=30)
            assert not p.exitcode, "writer process crashed"

        write_results = []
        while not write_queue.empty():
            write_results.append(write_queue.get_nowait())
        assert len(write_results) == num_writers
        assert all(r == "ok" for r in write_results)

        with open(config_path, "r", encoding="utf-8") as fh:
            final = json.load(fh)
        assert isinstance(final, dict)

    def test_specific_keys_preserved_after_concurrent_access(self, tmp_path):
        ctx = multiprocessing.get_context("spawn")
        config_path = str(tmp_path / "config.json")
        lock_path = str(tmp_path / "config.lock")

        seed = {"ai_enabled": True, "has_seen_onboarding": True}
        with open(config_path, "w", encoding="utf-8") as fh:
            json.dump(seed, fh)

        num_writers = 4
        num_readers = 2
        iterations = 200

        write_queue = ctx.Queue()
        read_queue = ctx.Queue()

        writers = [
            ctx.Process(target=_writer_worker, args=(config_path, lock_path, write_queue, iterations))
            for _ in range(num_writers)
        ]
        readers = [
            ctx.Process(target=_combined_reader_worker, args=(config_path, lock_path, read_queue, iterations * 10))
            for _ in range(num_readers)
        ]

        for p in readers:
            p.start()
        for p in writers:
            p.start()

        for p in writers:
            p.join(timeout=30)
            assert not p.exitcode, "writer process crashed"
        for p in readers:
            p.join(timeout=30)

        reader_results = []
        while not read_queue.empty():
            reader_results.append(read_queue.get_nowait())
        assert len(reader_results) == num_readers
        assert all(r == "ok" for r in reader_results)

        with open(config_path, "r", encoding="utf-8") as fh:
            parsed = json.load(fh)
        assert isinstance(parsed, dict)
        assert "ai_enabled" in parsed
        assert "has_seen_onboarding" in parsed
        assert isinstance(parsed["ai_enabled"], (bool, type(None)))
        assert isinstance(parsed["has_seen_onboarding"], (bool, type(None)))

    def test_config_json_is_always_valid_json(self, tmp_path):
        ctx = multiprocessing.get_context("spawn")
        config_path = str(tmp_path / "config.json")
        lock_path = str(tmp_path / "config.lock")

        with open(config_path, "w", encoding="utf-8") as fh:
            json.dump({"ai_enabled": False}, fh)

        iterations = 300
        write_queue = ctx.Queue()
        writers = [
            ctx.Process(target=_writer_worker, args=(config_path, lock_path, write_queue, iterations))
            for _ in range(3)
        ]

        for p in writers:
            p.start()
        for p in writers:
            p.join(timeout=30)
            assert not p.exitcode, "writer process crashed"

        with patch("termstory.config.get_config_path", return_value=config_path):
            final = load_config()
        assert isinstance(final, dict)
        assert "ai_enabled" in final
        assert "has_seen_onboarding" in final

    def test_concurrent_migration_does_not_lose_updates(self, tmp_path):
        ctx = multiprocessing.get_context("spawn")
        config_path = str(tmp_path / "config.json")
        lock_path = str(tmp_path / "config.lock")

        seed = {"ai_provider": "groq", "groq_api_key": "secret-key-1"}
        with open(config_path, "w", encoding="utf-8") as fh:
            json.dump(seed, fh)

        num_readers = 3
        iterations = 50
        queue = ctx.Queue()
        readers = [
            ctx.Process(target=_migration_reader, args=(config_path, lock_path, queue, iterations))
            for _ in range(num_readers)
        ]

        for p in readers:
            p.start()
        for p in readers:
            p.join(timeout=30)
            assert not p.exitcode, "migration reader process crashed"

        results = []
        while not queue.empty():
            results.append(queue.get_nowait())
        assert len(results) == num_readers
        assert all(r == "ok" for r in results), f"Concurrent migration lost updates: {results}"

        with open(config_path, "r", encoding="utf-8") as fh:
            parsed = json.load(fh)
        assert isinstance(parsed, dict)
        assert parsed.get("active_provider") == "groq"
        assert parsed.get("providers", {}).get("groq", {}).get("api_key") == "secret-key-1"
