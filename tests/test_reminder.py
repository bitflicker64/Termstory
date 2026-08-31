import os
import json
import time
from datetime import datetime
import pytest
from typer.testing import CliRunner

from termstory.cli import app
from termstory.database import Database
from termstory.models import Project, Session, Command
from termstory.reminder import (
    parse_reminder_text,
    add_reminder,
    complete_reminder,
    load_reminders,
    save_reminders,
    get_reminders_file_path,
    cluster_commands,
    generate_cluster_summary,
    _DEFAULT_CLUSTERING_THRESHOLD,
    consolidate_sleep_contexts
)


def _run_sleep_daemon_for_sigterm_test(data_dir: str) -> None:
    import threading
    from unittest.mock import patch
    import termstory.reminder as reminder

    def fake_sleep(_):
        threading.Event().wait()

    with patch.object(reminder, "get_app_dir", lambda name: data_dir):
        with patch("termstory.config.load_config", lambda: {"reminder_poll_interval": 300}):
            with patch("termstory.reminder.time.sleep", fake_sleep):
                with patch("termstory.database.Database.__init__", lambda self, path: None):
                    with patch("termstory.reminder.consolidate_sleep_contexts", return_value=None):
                        reminder.run_sleep_daemon("dummy_path")


def test_parse_reminder_text():
    # Success cases
    assert parse_reminder_text("remind me about fixing the bug in 3 days") == ("fixing the bug", 3)
    assert parse_reminder_text("remind me to write unit tests in 1 day") == ("write unit tests", 1)
    assert parse_reminder_text("about deploy code in 5 days") == ("deploy code", 5)
    assert parse_reminder_text("to code features in 0 days") == ("code features", 0)
    assert parse_reminder_text("finish project in 12 days") == ("finish project", 12)
    assert parse_reminder_text("   finish project   in   12   days   ") == ("finish project", 12)
    
    # Error cases
    with pytest.raises(ValueError, match="Could not parse reminder phrase"):
        parse_reminder_text("remind me about fixing the bug")
    with pytest.raises(ValueError, match="Could not parse reminder phrase"):
        parse_reminder_text("fixing the bug in days")
    with pytest.raises(ValueError, match="Could not parse reminder phrase"):
        parse_reminder_text("fixing the bug in -5 days")

def test_add_and_complete_reminder(tmp_path, monkeypatch):
    reminders_file = tmp_path / "reminders.json"
    monkeypatch.setattr("termstory.reminder.get_reminders_file_path", lambda: str(reminders_file))
    
    # Test setting reminder without DB
    rem1 = add_reminder("remind me about code review in 2 days")
    assert rem1["id"] == 1
    assert rem1["about"] == "code review"
    assert rem1["days"] == 2
    assert rem1["status"] == "pending"
    assert rem1["project_name"] == "Other"
    assert rem1["session_id"] is None
    
    # Verify file is saved
    reminders = load_reminders()
    assert len(reminders) == 1
    assert reminders[0]["about"] == "code review"
    
    # Test setting reminder with DB
    db_file = tmp_path / "test_reminder.db"
    db = Database(str(db_file))
    db.init_db()
    
    now = int(time.time())
    p = Project(id=1, name="termstory", path="~/projects/termstory", first_seen=now, last_seen=now, session_count=1, total_time=100)
    cmd = Command(timestamp=now, command="git commit", session_id=1, project_id=1)
    s = Session(id=1, start_time=now, end_time=now + 100, duration_seconds=100, project_id=1, commands=[cmd])
    db.save_data([p], [s], [cmd])
    
    rem2 = add_reminder("test parsing in 4 days", db=db)
    assert rem2["id"] == 2
    assert rem2["about"] == "test parsing"
    assert rem2["days"] == 4
    assert rem2["project_name"] == "termstory"
    assert rem2["session_id"] == 1
    
    # Test complete reminder
    assert complete_reminder(2) is True
    assert load_reminders()[1]["status"] == "completed"
    
    # Try completing non-existent
    assert complete_reminder(999) is False

def test_add_reminder_logs_warning_on_db_error(tmp_path, monkeypatch, caplog):
    """When the DB lookup raises, the reminder is still saved with defaults
    and a warning is logged. Regression test for issue #111."""
    reminders_file = tmp_path / "reminders.json"
    monkeypatch.setattr("termstory.reminder.get_reminders_file_path", lambda: str(reminders_file))

    class BrokenCursor:
        def execute(self, *args, **kwargs):
            raise RuntimeError("simulated DB failure")

    class BrokenConn:
        def cursor(self):
            return BrokenCursor()
        def close(self):
            pass

    class BrokenDB:
        def get_connection(self):
            return BrokenConn()

    with caplog.at_level("WARNING", logger="termstory.reminder"):
        rem = add_reminder("review code in 2 days", db=BrokenDB())

    # Reminder is still created with default fallback values
    assert rem["about"] == "review code"
    assert rem["days"] == 2
    assert rem["session_id"] is None
    assert rem["project_name"] == "Other"

    # Warning was emitted with the simulated error context
    assert any("add_reminder" in r.message and "simulated DB failure" in r.message
               for r in caplog.records)

def test_cli_remind_commands(tmp_path, monkeypatch):
    reminders_file = tmp_path / "reminders.json"
    monkeypatch.setattr("termstory.reminder.get_reminders_file_path", lambda: str(reminders_file))
    
    db_file = tmp_path / "test_reminder_cli.db"
    monkeypatch.setattr("termstory.cli.get_db_path", lambda: str(db_file))
    monkeypatch.setattr("termstory.cli.get_history_files", lambda: [])
    
    runner = CliRunner()
    
    # Test empty list
    result = runner.invoke(app, ["remind"])
    assert result.exit_code == 0
    assert "No reminders found" in result.stdout
    
    # Test add reminder via phrase
    result = runner.invoke(app, ["remind", "remind me to fix issues in 5 days"])
    assert result.exit_code == 0
    assert "Reminder set successfully" in result.stdout
    assert "#1" in result.stdout
    assert "fix issues" in result.stdout
    assert "5 days" in result.stdout
    
    # Test add reminder via phrase with explicit days override
    result = runner.invoke(app, ["remind", "do task in 3 days", "--days", "1"])
    assert result.exit_code == 0
    assert "Reminder set successfully" in result.stdout
    assert "#2" in result.stdout
    assert "do task" in result.stdout
    assert "1 days" in result.stdout
    
    # Test list reminders
    result = runner.invoke(app, ["remind"])
    assert result.exit_code == 0
    assert "TermStory Reminders" in result.stdout
    assert "fix issues" in result.stdout
    assert "do task" in result.stdout
    
    # Test complete reminder
    result = runner.invoke(app, ["remind", "--complete", "1"])
    assert result.exit_code == 0
    assert "Marked reminder #1 as completed" in result.stdout
    
    # Test listing filters out completed by default
    result = runner.invoke(app, ["remind"])
    assert result.exit_code == 0
    assert "fix issues" not in result.stdout
    assert "do task" in result.stdout
    
    # Test listing showing completed
    result = runner.invoke(app, ["remind", "--show-completed"])
    assert result.exit_code == 0
    assert "fix issues" in result.stdout
    assert "Completed" in result.stdout
    assert "do task" in result.stdout
    
    # Test completing invalid
    result = runner.invoke(app, ["remind", "--complete", "999"])
    assert result.exit_code == 1
    assert "Reminder #999 not found" in result.stdout


def test_run_sleep_daemon_uses_configured_poll_interval(tmp_path, monkeypatch):
    from unittest.mock import patch, MagicMock
    import termstory.reminder

    monkeypatch.setattr("termstory.reminder.get_app_dir", lambda name: str(tmp_path))
    monkeypatch.setattr("termstory.config.load_config", lambda: {"reminder_poll_interval": 60})

    sleep_calls = []

    def fake_sleep(n):
        sleep_calls.append(n)
        raise SystemExit(0)

    mock_db = MagicMock()
    with patch("termstory.reminder.time.sleep", fake_sleep):
        with patch("termstory.database.Database.__init__", lambda self, path: None):
            with patch("termstory.reminder.consolidate_sleep_contexts", return_value=None):
                with pytest.raises(SystemExit):
                    termstory.reminder.run_sleep_daemon("dummy_path")

    assert sleep_calls == [60]


def test_run_sleep_daemon_accepts_float_poll_interval(tmp_path, monkeypatch):
    from unittest.mock import patch
    import termstory.reminder

    monkeypatch.setattr("termstory.reminder.get_app_dir", lambda name: str(tmp_path))
    monkeypatch.setattr("termstory.config.load_config", lambda: {"reminder_poll_interval": 60.0})

    sleep_calls = []

    def fake_sleep(n):
        sleep_calls.append(n)
        raise SystemExit(0)

    with patch("termstory.reminder.time.sleep", fake_sleep):
        with patch("termstory.database.Database.__init__", lambda self, path: None):
            with patch("termstory.reminder.consolidate_sleep_contexts", return_value=None):
                with pytest.raises(SystemExit):
                    termstory.reminder.run_sleep_daemon("dummy_path")

    assert sleep_calls == [60.0]


def test_run_sleep_daemon_rejects_bool_poll_interval(tmp_path, monkeypatch):
    from unittest.mock import patch
    import termstory.reminder

    monkeypatch.setattr("termstory.reminder.get_app_dir", lambda name: str(tmp_path))
    monkeypatch.setattr("termstory.config.load_config", lambda: {"reminder_poll_interval": True})

    sleep_calls = []

    def fake_sleep(n):
        sleep_calls.append(n)
        raise SystemExit(0)

    with patch("termstory.reminder.time.sleep", fake_sleep):
        with patch("termstory.database.Database.__init__", lambda self, path: None):
            with patch("termstory.reminder.consolidate_sleep_contexts", return_value=None):
                with pytest.raises(SystemExit):
                    termstory.reminder.run_sleep_daemon("dummy_path")

    assert sleep_calls == [300]


def test_run_sleep_daemon_cleanup_on_initialization_failure(tmp_path, monkeypatch):
    from unittest.mock import patch
    import termstory.reminder
    
    # Set get_app_dir("data") to tmp_path
    monkeypatch.setattr("termstory.reminder.get_app_dir", lambda name: str(tmp_path))
    pid_file = tmp_path / "sleep_daemon.pid"
    
    # Mock Database to raise an error during init
    def mock_db_init(self, db_path):
        raise ValueError("Initialization failure simulation")
        
    with patch("termstory.database.Database.__init__", mock_db_init):
        with pytest.raises(ValueError, match="Initialization failure simulation"):
            termstory.reminder.run_sleep_daemon("dummy_path")
            
    # The PID file should have been cleaned up and not exist on disk
    assert not pid_file.exists()


def test_run_sleep_daemon_sigterm_removes_pid_file(tmp_path):
    """Issue #427: SIGTERM must remove sleep_daemon.pid before exiting."""
    import multiprocessing
    import signal

    data_dir = str(tmp_path)
    pid_file = tmp_path / "sleep_daemon.pid"

    ctx = multiprocessing.get_context("spawn")
    proc = ctx.Process(target=_run_sleep_daemon_for_sigterm_test, args=(data_dir,))
    proc.start()
    try:
        deadline = time.time() + 5
        while time.time() < deadline:
            if pid_file.exists():
                break
            time.sleep(0.05)
        assert pid_file.exists()

        os.kill(proc.pid, signal.SIGTERM)
        proc.join(timeout=5)
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=5)
        assert proc.exitcode == 0
        assert not pid_file.exists()
    finally:
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=5)
            if proc.is_alive():
                proc.kill()
                proc.join(timeout=5)


def test_start_sleep_daemon_spawns_when_no_daemon_running(tmp_path, monkeypatch):
    from unittest.mock import MagicMock
    import subprocess
    import termstory.reminder as reminder

    monkeypatch.setattr(reminder, "get_app_dir", lambda name: str(tmp_path))
    pid_file = tmp_path / "sleep_daemon.pid"

    calls = []
    def fake_popen(*args, **kwargs):
        calls.append(args)
        return MagicMock()
    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    reminder.start_sleep_daemon("dummy.db")

    assert len(calls) == 1
    # The PID file is created with our PID as a placeholder for the daemon.
    assert pid_file.read_text().strip() == str(os.getpid())


def test_start_sleep_daemon_defers_to_running_daemon(tmp_path, monkeypatch):
    import subprocess
    import termstory.reminder as reminder

    monkeypatch.setattr(reminder, "get_app_dir", lambda name: str(tmp_path))
    pid_file = tmp_path / "sleep_daemon.pid"
    # Our own process is a live PID, standing in for a running daemon. Patch
    # os.kill so the liveness probe always reports the process alive (also
    # keeps this deterministic on platforms where os.kill(pid, 0) misbehaves).
    pid_file.write_text(str(os.getpid()))
    monkeypatch.setattr(reminder.os, "kill", lambda pid, sig: None)

    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: calls.append(args))

    reminder.start_sleep_daemon("dummy.db")

    assert calls == []
    # The running daemon's PID file is left untouched.
    assert pid_file.read_text().strip() == str(os.getpid())


def test_start_sleep_daemon_reclaims_stale_pid_file(tmp_path, monkeypatch):
    import subprocess
    import termstory.reminder as reminder

    monkeypatch.setattr(reminder, "get_app_dir", lambda name: str(tmp_path))
    pid_file = tmp_path / "sleep_daemon.pid"
    # A stale PID file left behind by a daemon that died without cleaning up.
    pid_file.write_text("999999999")

    def dead_process(pid, sig):
        raise ProcessLookupError(pid, "no such process")
    monkeypatch.setattr(reminder.os, "kill", dead_process)

    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: calls.append(args))

    reminder.start_sleep_daemon("dummy.db")

    assert len(calls) == 1
    # The stale PID file was reclaimed and now holds our live PID.
    assert pid_file.read_text().strip() == str(os.getpid())


def test_start_sleep_daemon_concurrent_invocations_spawn_once(tmp_path, monkeypatch):
    import threading
    from unittest.mock import MagicMock
    import subprocess
    import termstory.reminder as reminder

    monkeypatch.setattr(reminder, "get_app_dir", lambda name: str(tmp_path))
    # Loser invocations probe the winner's PID via os.kill(pid, 0). Patch it to
    # always report "alive" so they defer; this also keeps the test deterministic
    # on platforms where the real os.kill misbehaves when called from threads.
    monkeypatch.setattr(reminder.os, "kill", lambda pid, sig: None)

    calls = []
    lock = threading.Lock()
    def fake_popen(*args, **kwargs):
        time.sleep(0.05)  # Widen the race window after the winner claims the PID file.
        with lock:
            calls.append(args)
        return MagicMock()
    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    barrier = threading.Barrier(8)
    def worker():
        barrier.wait()
        reminder.start_sleep_daemon("dummy.db")

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Exactly one invocation wins the atomic claim and spawns the daemon.
    assert len(calls) == 1


def test_start_sleep_daemon_creator_fails_before_pid_publication(tmp_path, monkeypatch):
    """Regression: a caller that wins the PID-file claim but fails before
    publishing its PID must not let a concurrent invocation delete/reclaim the
    now-empty claim while it is still fresh.

    If the empty claim were treated as stale, it would be deleted and a second
    caller could acquire it and spawn a duplicate daemon (the
    ``ValueError -> os.remove() -> second claimant`` race). The empty claim may
    only be reclaimed after it has sat stale beyond the bounded threshold.

    This test is fully deterministic: it drives ``time.time`` and
    ``os.path.getmtime`` directly rather than depending on real process
    scheduling.
    """
    import subprocess
    import termstory.reminder as reminder

    monkeypatch.setattr(reminder, "get_app_dir", lambda name: str(tmp_path))
    pid_file = tmp_path / "sleep_daemon.pid"

    # A published PID is treated as pointing at a live daemon (our own process).
    monkeypatch.setattr(reminder.os, "kill", lambda pid, sig: None)

    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: calls.append(args))

    # Deterministic clock: we control both "now" and the PID file's mtime.
    held_now = [100.0]
    held_mtime = [99.0]
    monkeypatch.setattr(reminder.time, "time", lambda: held_now[0])
    monkeypatch.setattr(reminder.os.path, "getmtime", lambda path: held_mtime[0])

    # 1) A creator atomically creates the PID-file claim but fails before
    #    publishing a PID (simulated by creating an empty claim directly).
    fd = os.open(str(pid_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666)
    os.close(fd)
    assert pid_file.read_text().strip() == ""

    # 2) A concurrent invocation sees the fresh empty claim (age 1s, below the
    #    stale threshold). It must defer - NOT spawn and NOT delete the claim.
    reminder.start_sleep_daemon("dummy.db")
    assert calls == []
    assert pid_file.exists()  # the in-progress claim is left intact

    # 3) Time passes beyond the bounded stale threshold; the empty claim is now
    #    genuinely abandoned.
    held_now[0] = held_mtime[0] + reminder._SLEEP_DAEMON_CLAIM_STALE_SECONDS + 1.0

    # 4) A replacement claimant reclaims the abandoned empty claim and spawns
    #    exactly one daemon.
    reminder.start_sleep_daemon("dummy.db")
    assert len(calls) == 1
    # The reclaimed claim now points at the replacement owner (our PID).
    assert pid_file.read_text().strip() == str(os.getpid())


def test_start_sleep_daemon_reclaims_empty_claim_removed_concurrently(tmp_path, monkeypatch):
    """A stale empty claim whose file disappears during reclaim handling must
    not error or terminate the claim; it simply retries the atomic claim."""
    import subprocess
    import termstory.reminder as reminder

    monkeypatch.setattr(reminder, "get_app_dir", lambda name: str(tmp_path))
    pid_file = tmp_path / "sleep_daemon.pid"

    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: calls.append(args))

    # The claim is deemed stale (dead PID).
    def dead_process(pid, sig):
        raise ProcessLookupError(pid, "no such process")
    monkeypatch.setattr(reminder.os, "kill", dead_process)

    pid_file.write_text("999999999")

    # While reclaiming, another invocation removes the file between our stale
    # inspection and os.remove() - i.e. os.remove raises FileNotFoundError and
    # the file is genuinely gone. The claim must not error or claim ownership
    # incorrectly; the retry atomically re-acquires the (now absent) file.
    real_remove = reminder.os.remove
    attempts = {"count": 0}
    def flaky_remove(path):
        attempts["count"] += 1
        if attempts["count"] == 1:
            # The other invocation already deleted the file.
            real_remove(path)
            raise FileNotFoundError()
        return real_remove(path)
    monkeypatch.setattr(reminder.os, "remove", flaky_remove)

    reminder.start_sleep_daemon("dummy.db")

    assert len(calls) == 1
    assert pid_file.read_text().strip() == str(os.getpid())


def test_start_sleep_daemon_reclaim_never_deletes_replacement_claim(tmp_path, monkeypatch):
    """Regression: a stale claim whose file is reclaimed by another invocation
    between our stale inspection and our removal attempt must never be deleted.

    Without the compare-and-remove protocol the stale-reclaim path removed the
    PID file blindly, so if a concurrent caller reacquired the stale file and
    published its own live PID in the window between ``_claim_is_stale`` and
    ``os.remove``, that replacement's live claim would be destroyed - allowing
    a further caller to spawn a duplicate daemon.

    This test is deterministic: it flips the PID file to a live replacement at
    the exact moment ``_remove_claim_safely`` inspects it, and asserts the
    replacement is not deleted and no extra daemon is spawned.
    """
    import subprocess
    import termstory.reminder as reminder

    monkeypatch.setattr(reminder, "get_app_dir", lambda name: str(tmp_path))
    pid_file = tmp_path / "sleep_daemon.pid"

    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: calls.append(args))

    # The loser's liveness probe reports the stale owner dead, so the stale
    # claim is reclaimable, but reports the replacement live, so we defer to
    # it once it owns the file.
    def fake_kill(pid, sig):
        if pid == 999999999:
            raise ProcessLookupError(pid, "no such process")
    monkeypatch.setattr(reminder.os, "kill", fake_kill)

    # The initially claimed file holds a dead PID -> stale and reclaimable.
    pid_file.write_text("999999999")

    real_pid_from_file = reminder._pid_from_file
    state = {"reads": 0, "created_replacement": False}

    def fake_pid_from_file(path):
        state["reads"] += 1
        if state["reads"] == 3:
            # The moment _remove_claim_safely re-reads the file, another
            # invocation has reclaimed it and published its own live PID.
            with open(path, "w") as f:
                f.write(str(os.getpid()))
            state["created_replacement"] = True
        return real_pid_from_file(path)

    monkeypatch.setattr(reminder, "_pid_from_file", fake_pid_from_file)

    reminder.start_sleep_daemon("dummy.db")

    # The replacement was created exactly during our removal attempt.
    assert state["created_replacement"] is True
    # We deferred to the replacement owner instead of deleting or spawning.
    assert calls == []
    # The replacement's live claim is intact.
    assert pid_file.read_text().strip() == str(os.getpid())


def test_add_reminder_explicit_days_prefix_suffix_stripping(tmp_path, monkeypatch):
    reminders_file = tmp_path / "reminders.json"
    monkeypatch.setattr("termstory.reminder.get_reminders_file_path", lambda: str(reminders_file))
    
    # Prefix and suffix stripping with explicit days
    rem = add_reminder("remind me about code review in 2 days", days=5)
    assert rem["about"] == "code review"
    assert rem["days"] == 5
    
    rem2 = add_reminder("remind me to write tests", days=1)
    assert rem2["about"] == "write tests"
    
    rem3 = add_reminder("deploy application in 3 days", days=10)
    assert rem3["about"] == "deploy application"


def test_add_reminder_days_validation(tmp_path, monkeypatch):
    reminders_file = tmp_path / "reminders.json"
    monkeypatch.setattr("termstory.reminder.get_reminders_file_path", lambda: str(reminders_file))
    
    # Test invalid types
    with pytest.raises(TypeError, match="Days must be an integer."):
        add_reminder("do something", days=2.5)
    
    with pytest.raises(TypeError, match="Days must be an integer."):
        add_reminder("do something", days="5")

    with pytest.raises(TypeError, match="Days must be an integer."):
        add_reminder("do something", days=True)

    # Test invalid boundary values
    with pytest.raises(ValueError, match="Days must be between 0 and 3650."):
        add_reminder("do something", days=-1)
    
    with pytest.raises(ValueError, match="Days must be between 0 and 3650."):
        add_reminder("do something", days=3651)

     # Test parsed phrase that yields an invalid range
    with pytest.raises(ValueError, match="Days must be between 0 and 3650."):
        add_reminder("do something in 4000 days")


def _fake_get_embeddings(monkeypatch, mapping):
    import termstory.rag as rag

    def fake(texts, model_name="all-MiniLM-L6-v2"):
        return [mapping[t] for t in texts]

    monkeypatch.setattr(rag, "get_embeddings", fake)
    monkeypatch.setattr(rag, "SENTENCE_TRANSFORMERS_AVAILABLE", True)


def test_cluster_commands_merges_above_threshold(monkeypatch):
    embeddings = {
        "git status": [1.0, 0.0],
        "git status -s": [0.99, 0.14107],  # cos sim with [1,0] ≈ 0.99
    }
    _fake_get_embeddings(monkeypatch, embeddings)

    clusters = cluster_commands(list(embeddings.keys()), threshold=0.6)

    assert len(clusters) == 1
    assert set(clusters[0]) == set(embeddings.keys())


def test_cluster_commands_splits_below_threshold(monkeypatch):
    embeddings = {
        "git status": [1.0, 0.0],
        "docker ps": [0.0, 1.0],  # cos sim = 0.0, well below 0.6
    }
    _fake_get_embeddings(monkeypatch, embeddings)

    clusters = cluster_commands(list(embeddings.keys()), threshold=0.6)

    assert len(clusters) == 2


def test_cluster_commands_respects_explicit_threshold_override(monkeypatch):
    embeddings = {
        "a": [1.0, 0.0],
        "b": [0.7, 0.7141],  # cos sim ≈ 0.7 — merges at 0.6, splits at 0.9
    }
    _fake_get_embeddings(monkeypatch, embeddings)

    # Lower threshold (0.5): merges
    merged = cluster_commands(list(embeddings.keys()), threshold=0.5)
    assert len(merged) == 1

    # Higher threshold (0.9): splits
    split = cluster_commands(list(embeddings.keys()), threshold=0.9)
    assert len(split) == 2


def test_cluster_commands_reads_threshold_from_config_when_unset(monkeypatch):
    embeddings = {
        "a": [1.0, 0.0],
        "b": [0.7, 0.7141],  # cos sim ≈ 0.7
    }
    _fake_get_embeddings(monkeypatch, embeddings)

    # Config sets a high threshold (0.9) — must split even though the old
    # hardcoded 0.6 default would have merged these.
    monkeypatch.setattr(
        "termstory.reminder.load_config",
        lambda: {"clustering_threshold": 0.9},
    )
    clusters = cluster_commands(list(embeddings.keys()))
    assert len(clusters) == 2


def test_cluster_commands_falls_back_to_default_when_config_raises(monkeypatch):
    embeddings = {
        "git status": [1.0, 0.0],
        "git status -s": [0.99, 0.14107],
    }
    _fake_get_embeddings(monkeypatch, embeddings)

    monkeypatch.setattr(
        "termstory.reminder.load_config",
        lambda: (_ for _ in ()).throw(OSError("no config")),
    )
    clusters = cluster_commands(list(embeddings.keys()))
    # _DEFAULT_CLUSTERING_THRESHOLD (0.6) merges these — fallback succeeded
    assert len(clusters) == 1
    assert _DEFAULT_CLUSTERING_THRESHOLD == 0.6


def test_consolidate_sleep_contexts_reads_config_once_not_per_chunk(tmp_path, monkeypatch):
    import termstory.rag as rag

    db_file = tmp_path / "test_consolidate.db"
    db = Database(str(db_file))
    db.init_db()

    now = int(time.time())
    p = Project(id=1, name="termstory", path="~/projects/termstory", first_seen=now, last_seen=now, session_count=1, total_time=100)

    # 3 chunks separated by >= 1800s idle gaps, 2 commands each.
    commands = []
    for chunk_idx in range(3):
        base = now - (3 - chunk_idx) * 3600
        commands.append(Command(timestamp=base, command=f"git status {chunk_idx}", session_id=1, project_id=1))
        commands.append(Command(timestamp=base + 10, command=f"git log {chunk_idx}", session_id=1, project_id=1))

    s = Session(id=1, start_time=commands[0].timestamp, end_time=commands[-1].timestamp, duration_seconds=3600, project_id=1, commands=commands)
    db.save_data([p], [s], commands)

    # Force the embeddings path (not the verb-fallback path) so cluster_commands
    # actually reaches the threshold-resolution / load_config() call.
    def fake_get_embeddings(texts, model_name="all-MiniLM-L6-v2"):
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr(rag, "get_embeddings", fake_get_embeddings)
    monkeypatch.setattr(rag, "SENTENCE_TRANSFORMERS_AVAILABLE", True)

    call_count = [0]
    real_defaults = {"clustering_threshold": 0.6}

    def counting_load_config():
        call_count[0] += 1
        return real_defaults

    monkeypatch.setattr("termstory.reminder.load_config", counting_load_config)

    consolidate_sleep_contexts(db, force=True)

    assert call_count[0] == 1, (
        f"load_config() was called {call_count[0]} times for 3 chunks — "
        "expected exactly 1 (resolved once per run, not once per chunk)"
    )


def _patch_cluster_llm(monkeypatch, provider="groq"):
    """Point generate_cluster_summary at a fake provider and capture the outbound
    LLM prompt via _send_llm_request — the request boundary it actually uses."""
    config = {
        "active_provider": provider,
        "providers": {
            provider: {
                "api_key": "test-key",
                "api_base_url": "https://api.example.test/v1",
                "model_name": "test-model",
            }
        },
    }
    monkeypatch.setattr("termstory.config.load_config", lambda: config)

    calls = []

    def fake_send_llm_request(prompt, *args, **kwargs):
        calls.append({"prompt": prompt})
        return "Shipped the feature"

    monkeypatch.setattr("termstory.ai._send_llm_request", fake_send_llm_request)
    return calls


def test_generate_cluster_summary_redacts_secrets_from_llm_prompt(monkeypatch):
    """Regression test for issue #449: secrets must be scrubbed by
    sanitize_session_commands() before the cluster prompt reaches the LLM."""
    calls = _patch_cluster_llm(monkeypatch)

    result = generate_cluster_summary(
        [
            "git status",
            # Not blacklisted (no 'aws configure'), but carries a fake AWS key
            # that redact_command() must turn into [REDACTED_AWS_KEY].
            "aws s3 ls --access-key AKIAIOSFODNN7EXAMPLE",
        ]
    )

    assert len(calls) == 1
    sent_prompt = calls[0]["prompt"]
    assert "AKIAIOSFODNN7EXAMPLE" not in sent_prompt
    assert "[REDACTED_AWS_KEY]" in sent_prompt
    assert "git status" in sent_prompt
    assert result == "Shipped the feature"


def test_generate_cluster_summary_blacklisted_commands_never_reach_llm(monkeypatch):
    """A cluster containing a blacklisted command (e.g. vault) must return the
    standard redaction marker and must not trigger any LLM request."""
    calls = _patch_cluster_llm(monkeypatch)

    result = generate_cluster_summary(
        [
            "cd project",
            "vault read secret/data/prod",
        ]
    )

    assert result == "[REDACTED: Security/Authentication Operations]"
    assert calls == []


def test_generate_cluster_summary_keeps_normal_commands_in_prompt(monkeypatch):
    """Ordinary (non-blacklisted) commands must still be summarized by the LLM
    path with their sanitized text present in the prompt."""
    calls = _patch_cluster_llm(monkeypatch)

    result = generate_cluster_summary(
        [
            "docker compose up -d",
            "pytest tests/ -q",
        ]
    )

    assert len(calls) == 1
    sent_prompt = calls[0]["prompt"]
    assert "- docker compose up -d" in sent_prompt
    assert "- pytest tests/ -q" in sent_prompt
    assert result == "Shipped the feature"


def test_generate_cluster_summary_disabled_provider_stays_local(monkeypatch):
    """provider == "disabled" keeps the original local fallback behavior: no
    sanitization/request logic and definitely no LLM call."""
    config = {"active_provider": "disabled"}
    monkeypatch.setattr("termstory.config.load_config", lambda: config)
    calls = []
    monkeypatch.setattr(
        "termstory.ai._send_llm_request",
        lambda *args, **kwargs: calls.append(args),
    )

    result = generate_cluster_summary(["git status", "docker ps"])

    assert result == "Worked on commands: git, docker"
    assert calls == []


# ---------------------------------------------------------------------------
# Regression tests for issue #468: recover failed background consolidation
# without losing work.
#
# Persistence/checkpoint model being tested (from termstory/database.py and
# termstory/reminder.py):
#   - rem_sleep_consolidation has NO uniqueness constraint on
#     (start_time, end_time); save_consolidated_context() is a plain INSERT,
#     so writing the same window twice would duplicate rows.
#   - There is no separate watermark column: the next consolidation run's
#     eligibility boundary is MAX(end_time) over persisted rows, and eligible
#     commands are strictly those newer than it. The tests below therefore
#     verify retryability through that exact boundary.
# ---------------------------------------------------------------------------

def _sleep_checkpoint(db):
    """Return the consolidation checkpoint (MAX(end_time)) for assertions."""
    conn = db.get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(end_time) FROM rem_sleep_consolidation")
        row = cursor.fetchone()
        return row[0] if row and row[0] is not None else 0
    finally:
        conn.close()


def _seed_three_clusters(tmp_path):
    """Create a Database containing one idle chunk with three orthogonally
    embeddable two-command clusters (git -> docker -> npm) in chronological
    order. Returns (db, now, command-timestamp dict per tool family)."""
    db_file = tmp_path / "test_468.db"
    db = Database(str(db_file))
    db.init_db()

    now = int(time.time())
    t_git_a, t_git_b = now - 5900, now - 5890
    t_docker_a, t_docker_b = now - 5800, now - 5790
    t_npm_a, t_npm_b = now - 5700, now - 5690

    commands = [
        Command(timestamp=t_git_a, command="git alpha-one", session_id=1, project_id=1),
        Command(timestamp=t_git_b, command="git alpha-two", session_id=1, project_id=1),
        Command(timestamp=t_docker_a, command="docker beta-one", session_id=1, project_id=1),
        Command(timestamp=t_docker_b, command="docker beta-two", session_id=1, project_id=1),
        Command(timestamp=t_npm_a, command="npm gamma-one", session_id=1, project_id=1),
        Command(timestamp=t_npm_b, command="npm gamma-two", session_id=1, project_id=1),
    ]
    p = Project(id=1, name="termstory", path="~/projects/termstory",
                first_seen=now - 6000, last_seen=now, session_count=1, total_time=310)
    s = Session(id=1, start_time=t_git_a, end_time=t_npm_b, duration_seconds=210,
                project_id=1, commands=commands)
    db.save_data([p], [s], commands)
    return db, now, {
        "git": (t_git_a, t_git_b),
        "docker": (t_docker_a, t_docker_b),
        "npm": (t_npm_a, t_npm_b),
    }


def _fake_three_cluster_embeddings(monkeypatch):
    """Force the embeddings clustering path with three orthogonal groups so
    cluster_commands() deterministically yields one cluster per tool family."""
    import termstory.rag as rag

    mapping = {
        "git alpha-one": [1.0, 0.0, 0.0],
        "git alpha-two": [1.0, 0.0, 0.0],
        "docker beta-one": [0.0, 1.0, 0.0],
        "docker beta-two": [0.0, 1.0, 0.0],
        "npm gamma-one": [0.0, 0.0, 1.0],
        "npm gamma-two": [0.0, 0.0, 1.0],
    }

    def fake_get_embeddings(texts, model_name="all-MiniLM-L6-v2"):
        return [list(mapping[t]) for t in texts]

    monkeypatch.setattr(rag, "get_embeddings", fake_get_embeddings)
    monkeypatch.setattr(rag, "SENTENCE_TRANSFORMERS_AVAILABLE", True)


def _summary_stub(failing_prefixes):
    """Build a generate_cluster_summary stand-in raising for clusters whose
    first command starts with any prefix in failing_prefixes."""
    def fake_generate_cluster_summary(cluster):
        if any(cluster[0].startswith(prefix) for prefix in failing_prefixes):
            raise TimeoutError("simulated LLM request timeout")
        return "SUMMARY-" + cluster[0].split()[0]
    return fake_generate_cluster_summary


def test_consolidate_preserves_successful_clusters_when_one_fails(tmp_path, monkeypatch):
    """A+B (issue #468): with clusters A(ok) -> B(raises) -> C(ok) in one
    chunk, A's summary survives, B is not marked consolidated, processing
    continues past B, and the checkpoint stays below B's commands."""
    db, now, ts = _seed_three_clusters(tmp_path)
    _fake_three_cluster_embeddings(monkeypatch)
    # git succeeds, docker raises, npm must still be processed afterwards.
    seen_after_failure = []
    base_stub = _summary_stub(["docker"])

    def stub_and_track(cluster):
        result = base_stub(cluster)
        seen_after_failure.append(cluster[0].split()[0])
        return result

    monkeypatch.setattr(
        "termstory.reminder.generate_cluster_summary", stub_and_track
    )

    count = consolidate_sleep_contexts(db, force=True)

    # C was processed after B's failure (processing did not abort at B).
    assert "npm" in seen_after_failure

    # Only the safe prefix (git cluster) was persisted; docker/npm withheld.
    assert count == 1
    contexts = db.get_consolidated_contexts()
    assert len(contexts) == 1
    ctx = contexts[0]
    assert ctx["start_time"] == ts["git"][0]
    assert ctx["end_time"] == ts["git"][1]  # row anchored below the failure
    assert ctx["commands"] == ["git alpha-one", "git alpha-two"]
    assert "SUMMARY-git" in ctx["summary"]

    # B is not falsely marked consolidated: its own and C's summary strings
    # are absent from persisted rows, and their commands are excluded too.
    assert "SUMMARY-docker" not in ctx["summary"]
    assert "SUMMARY-npm" not in ctx["summary"]
    assert "docker beta-one" not in ctx["commands"]

    # Checkpoint did NOT advance past the failed cluster's commands.
    assert _sleep_checkpoint(db) == ts["git"][1]


def test_consolidate_failure_before_all_successes_persists_nothing(tmp_path, monkeypatch):
    """B (issue #468): when the failing cluster precedes every successful one
    chronologically, nothing can be safely persisted without advancing the
    checkpoint over the failed work — so the whole chunk stays retryable."""
    db, now, ts = _seed_three_clusters(tmp_path)
    _fake_three_cluster_embeddings(monkeypatch)
    monkeypatch.setattr(
        "termstory.reminder.generate_cluster_summary", _summary_stub(["git"])
    )

    count = consolidate_sleep_contexts(db, force=True)

    # Successful sibling summaries were computed but withheld rather than
    # persisted ahead of the failure; they will be retried together with it.
    assert count == 0
    assert len(db.get_consolidated_contexts()) == 0

    # Nothing was persisted => checkpoint untouched => ALL work (including
    # the successful clusters' commands) remains eligible next run.
    assert _sleep_checkpoint(db) == 0


def test_failed_cluster_remains_retryable_on_next_run(tmp_path, monkeypatch):
    """B (issue #468): after a partial failure, the NEXT consolidation run
    must actually discover and persist the previously failed work through the
    MAX(end_time)-based eligibility boundary."""
    db, now, ts = _seed_three_clusters(tmp_path)
    _fake_three_cluster_embeddings(monkeypatch)

    monkeypatch.setattr(
        "termstory.reminder.generate_cluster_summary", _summary_stub(["docker"])
    )
    first_count = consolidate_sleep_contexts(db, force=True)
    assert first_count == 1
    assert _sleep_checkpoint(db) == ts["git"][1]

    # Failed cluster's commands sit strictly above the checkpoint: they were
    # not consumed by the failed/partial first run.
    checkpoint = _sleep_checkpoint(db)
    assert ts["docker"][0] > checkpoint
    assert ts["npm"][1] > checkpoint

    # Retry with all clusters succeeding.
    monkeypatch.setattr(
        "termstory.reminder.generate_cluster_summary", _summary_stub([])
    )
    second_count = consolidate_sleep_contexts(db, force=True)
    assert second_count == 1

    contexts = db.get_consolidated_contexts()
    assert len(contexts) == 2
    retried = [c for c in contexts if c["start_time"] == ts["docker"][0]]
    assert len(retried) == 1
    retried_ctx = retried[0]
    assert retried_ctx["start_time"] == ts["docker"][0]
    assert retried_ctx["end_time"] == ts["npm"][1]
    assert set(retried_ctx["commands"]) == {
        "docker beta-one", "docker beta-two", "npm gamma-one", "npm gamma-two",
    }
    assert "SUMMARY-docker" in retried_ctx["summary"]
    assert "SUMMARY-npm" in retried_ctx["summary"]

    # Every command is covered by exactly two ordered, non-overlapping
    # windows: run 1's safe prefix plus run 2's recovery window.
    windows = sorted((c["start_time"], c["end_time"]) for c in contexts)
    assert windows == [
        (ts["git"][0], ts["git"][1]),
        (ts["docker"][0], ts["npm"][1]),
    ]


def test_retry_is_idempotent_and_never_duplicates_context(tmp_path, monkeypatch):
    """C (issue #468): a retry after recovery must not duplicate already-
    persisted context, despite rem_sleep_consolidation having no uniqueness
    constraint on (start_time, end_time)."""
    db, now, ts = _seed_three_clusters(tmp_path)
    _fake_three_cluster_embeddings(monkeypatch)

    # Run 1: docker fails; only git's window persists.
    monkeypatch.setattr(
        "termstory.reminder.generate_cluster_summary", _summary_stub(["docker"])
    )
    consolidate_sleep_contexts(db, force=True)

    # Run 2: recovery — previously failed work is consolidated.
    monkeypatch.setattr(
        "termstory.reminder.generate_cluster_summary", _summary_stub([])
    )
    consolidate_sleep_contexts(db, force=True)
    contexts_after_recovery = db.get_consolidated_contexts()
    rows_after_recovery = len(contexts_after_recovery)
    checkpoint_after_recovery = _sleep_checkpoint(db)

    # Run 3: a further forced consolidation finds nothing new and must not
    # rewrite or re-insert anything.
    third_count = consolidate_sleep_contexts(db, force=True)
    assert third_count == 0

    contexts_final = db.get_consolidated_contexts()
    assert len(contexts_final) == rows_after_recovery
    assert sorted((c["start_time"], c["end_time"]) for c in contexts_final) == \
        sorted((c["start_time"], c["end_time"]) for c in contexts_after_recovery)
    assert _sleep_checkpoint(db) == checkpoint_after_recovery == ts["npm"][1]

    conn = db.get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM rem_sleep_consolidation")
        assert cursor.fetchone()[0] == rows_after_recovery
    finally:
        conn.close()


def test_llm_provider_timeout_is_isolated_to_one_cluster(tmp_path, monkeypatch):
    """D (issue #468): through the real config->provider->_send_llm_request
    path, a realistic timeout exception raised for one cluster's request is
    isolated: sibling clusters' requests still go out, their LLM responses
    persist, and the timed-out cluster's commands stay retryable."""
    db, now, ts = _seed_three_clusters(tmp_path)
    _fake_three_cluster_embeddings(monkeypatch)

    # Provider enabled; requests flow through termstory.ai._send_llm_request.
    monkeypatch.setattr(
        "termstory.config.load_config",
        lambda: {
            "active_provider": "groq",
            "providers": {
                "groq": {
                    "api_key": "test-key",
                    "api_base_url": "http://localhost:9/v1",
                    "model_name": "test-model",
                }
            },
        },
    )

    llm_calls = []

    def fake_send_llm_request(prompt, *args, **kwargs):
        llm_calls.append(prompt)
        if "docker beta-one" in prompt:
            # Realistic provider-side hard failure (what urllib surfaces when
            # a socket read aborts mid-request).
            raise TimeoutError("The read operation timed out")
        return "llm-summary-" + prompt.split("- ")[1].split()[0]

    monkeypatch.setattr(
        "termstory.ai._send_llm_request", fake_send_llm_request
    )

    count = consolidate_sleep_contexts(db, force=True)

    # All three clusters were attempted despite the middle one timing out.
    assert len(llm_calls) == 3
    assert sum(1 for p in llm_calls if "docker beta-one" in p) == 1

    # The leading successful cluster persisted; the failed one did not.
    assert count == 1
    contexts = db.get_consolidated_contexts()
    assert len(contexts) == 1
    ctx = contexts[0]
    assert ctx["end_time"] == ts["git"][1]
    assert ctx["commands"] == ["git alpha-one", "git alpha-two"]
    assert "llm-summary-git" in ctx["summary"]
    assert _sleep_checkpoint(db) == ts["git"][1]


def test_consolidate_all_success_persists_exactly_once(tmp_path, monkeypatch):
    """F (issue #468): with every cluster succeeding, the chunk is persisted
    exactly once through the original single-write path: one row spanning the
    full window, all summaries joined, checkpoint at the true end, and a
    further forced run adds nothing (no duplicate persistence)."""
    db, now, ts = _seed_three_clusters(tmp_path)
    _fake_three_cluster_embeddings(monkeypatch)
    # Exercise the real config -> provider -> LLM path for every cluster.
    monkeypatch.setattr(
        "termstory.config.load_config",
        lambda: {
            "active_provider": "groq",
            "providers": {
                "groq": {
                    "api_key": "test-key",
                    "api_base_url": "http://localhost:9/v1",
                    "model_name": "test-model",
                }
            },
        },
    )
    llm_calls = []

    def fake_send_llm_request(prompt, *args, **kwargs):
        llm_calls.append(prompt)
        return "llm-summary-" + prompt.split("- ")[1].split()[0]

    monkeypatch.setattr(
        "termstory.ai._send_llm_request", fake_send_llm_request
    )

    count = consolidate_sleep_contexts(db, force=True)

    assert count == 1  # single persisted chunk, unchanged meaning
    assert len(llm_calls) == 3
    contexts = db.get_consolidated_contexts()
    assert len(contexts) == 1
    ctx = contexts[0]
    assert ctx["start_time"] == ts["git"][0]
    assert ctx["end_time"] == ts["npm"][1]
    assert ctx["commands"] == [
        "git alpha-one", "git alpha-two",
        "docker beta-one", "docker beta-two",
        "npm gamma-one", "npm gamma-two",
    ]
    assert (
        "llm-summary-git" in ctx["summary"]
        and "llm-summary-docker" in ctx["summary"]
        and "llm-summary-npm" in ctx["summary"]
    )
    assert _sleep_checkpoint(db) == ts["npm"][1]

    # An additional forced run must not re-insert or rewrite anything.
    assert consolidate_sleep_contexts(db, force=True) == 0
    assert len(db.get_consolidated_contexts()) == 1


def test_generate_cluster_summary_disabled_provider_empty_cluster(monkeypatch):
    """G (issue #468): an empty cluster under the disabled provider keeps the
    existing 'Idle session' fallback instead of turning into a failure."""
    monkeypatch.setattr(
        "termstory.config.load_config",
        lambda: {"active_provider": "disabled"},
    )
    assert generate_cluster_summary([]) == "Idle session"
