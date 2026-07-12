import json
import math
import urllib.request
import urllib.error
import sqlite3
import pytest
from collections import Counter
from typer.testing import CliRunner
from termstory.cli import app
from termstory.database import Database
from termstory.models import Project, Session, Command
from termstory.ask import (
    search_ask,
    generate_answer,
    _tokenize,
    _make_bigrams,
    _session_tokens,
    _query_terms,
    _bm25_score,
)


class MockResponse:
    def __init__(self, data, status_code=200):
        self.data = data
        self.status = status_code

    def read(self):
        return self.data

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


# ── Unit tests for helper functions ──────────────────────────────────────────

def test_tokenize_basic():
    assert _tokenize("Hello World") == ["hello", "world"]


def test_tokenize_empty():
    assert _tokenize("") == []
    assert _tokenize("   ") == []


def test_tokenize_special_chars():
    tokens = _tokenize("git commit --message='fix: bug'")
    assert "git" in tokens
    assert "commit" in tokens
    assert "fix" in tokens
    assert "bug" in tokens


def test_make_bigrams_basic():
    tokens = ["git", "push", "origin"]
    bigrams = _make_bigrams(tokens)
    assert bigrams == ["git_push", "push_origin"]


def test_make_bigrams_single_token():
    assert _make_bigrams(["solo"]) == []


def test_make_bigrams_empty():
    assert _make_bigrams([]) == []


def test_query_terms_includes_bigrams():
    words = ["npm", "deploy"]
    terms = _query_terms(words)
    assert "npm" in terms
    assert "deploy" in terms
    assert "npm_deploy" in terms


def test_query_terms_single_word_no_bigrams():
    terms = _query_terms(["docker"])
    assert terms == ["docker"]


def test_session_tokens_includes_project(tmp_path):
    s = Session(id=1, start_time=0, end_time=100, duration_seconds=100, project_id=1,
                commands=[Command(timestamp=0, command="git push", session_id=1, project_id=1)])
    project_map = {1: "MyProject"}
    tokens = _session_tokens(s, project_map)
    # Project name should be repeated for weighting
    assert tokens.count("myproject") >= 3


def test_session_tokens_includes_bigrams(tmp_path):
    s = Session(id=1, start_time=0, end_time=100, duration_seconds=100, project_id=1,
                commands=[Command(timestamp=0, command="npm run build", session_id=1, project_id=1)])
    project_map = {1: "Other"}
    tokens = _session_tokens(s, project_map)
    assert "npm_run" in tokens
    assert "run_build" in tokens


def test_bm25_score_zero_for_empty_doc():
    score = _bm25_score(["deploy"], Counter(), 0, 10.0, {"deploy": 1}, 5)
    assert score == 0.0


def test_bm25_score_higher_for_relevant_doc():
    # Document that contains the query term should score higher
    relevant = Counter({"deploy": 5, "website": 3, "production": 2})
    irrelevant = Counter({"python": 10, "test": 8})
    df = {"deploy": 1}
    N = 2
    avg_dl = 10.0

    s_relevant = _bm25_score(["deploy"], relevant, sum(relevant.values()), avg_dl, df, N)
    s_irrelevant = _bm25_score(["deploy"], irrelevant, sum(irrelevant.values()), avg_dl, df, N)
    assert s_relevant > s_irrelevant


def test_bm25_score_prefix_match():
    # 'dep' should match 'deploy' and 'deployment' via prefix
    counter = Counter({"deploy": 2, "deployment": 1, "other": 5})
    df = {"dep": 1}
    score = _bm25_score(["dep"], counter, sum(counter.values()), 8.0, df, 3)
    assert score > 0.0


def test_bm25_score_bigram_exact_match_only():
    counter = Counter({"npm_run": 2, "run_build": 1})
    df = {"npm_run": 1}
    score_match = _bm25_score(["npm_run"], counter, sum(counter.values()), 3.0, df, 2)
    score_no_match = _bm25_score(["npm_test"], counter, sum(counter.values()), 3.0, df, 2)
    assert score_match > 0.0
    assert score_no_match == 0.0


# ── Integration tests using in-memory database ────────────────────────────────

def test_search_ask_tfidf_and_ranking(tmp_path):
    db_file = tmp_path / "test_ask_search.db"
    db = Database(str(db_file))
    db.init_db()

    now = 1700000000

    p1 = Project(id=1, name="My Website Project", path="~/web", first_seen=now, last_seen=now, session_count=1, total_time=100)
    p2 = Project(id=2, name="Other CLI", path="~/cli", first_seen=now, last_seen=now, session_count=1, total_time=100)

    cmd1 = Command(timestamp=now, command="git push origin main", session_id=1, project_id=1)
    cmd2 = Command(timestamp=now + 50, command="npm run deploy", session_id=1, project_id=1)
    s1 = Session(id=1, start_time=now, end_time=now + 100, duration_seconds=100, project_id=1, commands=[cmd1, cmd2])
    s1.ai_summary = "Deploying website build to production"

    cmd3 = Command(timestamp=now, command="python3 test.py", session_id=2, project_id=2)
    s2 = Session(id=2, start_time=now, end_time=now + 100, duration_seconds=100, project_id=2, commands=[cmd3])
    s2.ai_summary = "Running python test cases"

    db.save_data([p1, p2], [s1, s2], [cmd1, cmd2, cmd3])
    db.save_session_ai_summary(1, s1.ai_summary)
    db.save_session_ai_summary(2, s2.ai_summary)

    results = search_ask("deploy", db)
    assert len(results) >= 1
    assert results[0].id == 1  # Session 1 has "deploy" and should be ranked first

    results = search_ask("website", db)
    assert len(results) >= 1
    assert results[0].id == 1


def test_search_ask_bigram_ranking(tmp_path):
    """Session with matching bigram (npm_deploy) should outscore generic single-word hits."""
    db_file = tmp_path / "test_ask_bigram.db"
    db = Database(str(db_file))
    db.init_db()

    now = 1700001000

    p1 = Project(id=1, name="frontend", path="~/frontend", first_seen=now, last_seen=now, session_count=1, total_time=200)
    p2 = Project(id=2, name="backend", path="~/backend", first_seen=now, last_seen=now, session_count=1, total_time=200)

    # Session 1: both words "npm" and "deploy" appear adjacently
    cmd1a = Command(timestamp=now, command="npm deploy", session_id=1, project_id=1)
    cmd1b = Command(timestamp=now + 10, command="npm run deploy", session_id=1, project_id=1)
    s1 = Session(id=1, start_time=now, end_time=now + 200, duration_seconds=200, project_id=1, commands=[cmd1a, cmd1b])
    s1.ai_summary = "npm deploy pipeline run"

    # Session 2: only "deploy" appears, "npm" is absent
    cmd2 = Command(timestamp=now, command="deploy.sh", session_id=2, project_id=2)
    s2 = Session(id=2, start_time=now, end_time=now + 200, duration_seconds=200, project_id=2, commands=[cmd2])
    s2.ai_summary = "Ran deploy script"

    db.save_data([p1, p2], [s1, s2], [cmd1a, cmd1b, cmd2])
    db.save_session_ai_summary(1, s1.ai_summary)
    db.save_session_ai_summary(2, s2.ai_summary)

    results = search_ask("npm deploy", db)
    assert len(results) >= 1
    assert results[0].id == 1  # bigram-boosted session should win


def test_search_ask_empty_query(tmp_path):
    db_file = tmp_path / "test_ask_empty.db"
    db = Database(str(db_file))
    db.init_db()
    assert search_ask("", db) == []
    assert search_ask("   ", db) == []


def test_search_ask_no_results(tmp_path):
    db_file = tmp_path / "test_ask_no_results.db"
    db = Database(str(db_file))
    db.init_db()

    now = 1700000000
    p = Project(id=1, name="SomeProject", path="~/some", first_seen=now, last_seen=now, session_count=1, total_time=100)
    cmd = Command(timestamp=now, command="ls -la", session_id=1, project_id=1)
    s = Session(id=1, start_time=now, end_time=now + 100, duration_seconds=100, project_id=1, commands=[cmd])
    db.save_data([p], [s], [cmd])

    results = search_ask("xyzzynonexistentterm99", db)
    assert results == []


def test_search_ask_project_name_matching(tmp_path):
    db_file = tmp_path / "test_ask_proj.db"
    db = Database(str(db_file))
    db.init_db()

    now = 1700000000
    p = Project(id=1, name="SpecialSecretProject", path="~/secret", first_seen=now, last_seen=now, session_count=1, total_time=100)
    cmd = Command(timestamp=now, command="ls", session_id=1, project_id=1)
    s = Session(id=1, start_time=now, end_time=now + 100, duration_seconds=100, project_id=1, commands=[cmd])
    db.save_data([p], [s], [cmd])

    results = search_ask("SpecialSecretProject", db)
    assert len(results) == 1
    assert results[0].id == 1


def test_search_ask_fts5_fallback(tmp_path, monkeypatch):
    db_file = tmp_path / "test_ask_fallback.db"
    db = Database(str(db_file))
    db.init_db()

    now = 1700000000
    p = Project(id=1, name="Fallback Test Project", path="~/fallback", first_seen=now, last_seen=now, session_count=1, total_time=100)
    cmd = Command(timestamp=now, command="docker-compose up -d", session_id=1, project_id=1)
    s = Session(id=1, start_time=now, end_time=now + 100, duration_seconds=100, project_id=1, commands=[cmd])
    db.save_data([p], [s], [cmd])

    original_get_connection = db.get_connection

    def get_broken_connection():
        conn = original_get_connection()
        original_cursor = conn.cursor

        def broken_cursor(*args, **kwargs):
            cursor = original_cursor(*args, **kwargs)
            original_execute = cursor.execute

            def broken_execute(sql, *exec_args):
                if "MATCH" in sql:
                    raise sqlite3.OperationalError("Mocked FTS5 error")
                return original_execute(sql, *exec_args)

            cursor.execute = broken_execute
            return cursor

        conn.cursor = broken_cursor
        return conn

    monkeypatch.setattr(db, "get_connection", get_broken_connection)

    results = search_ask("docker-compose", db)
    assert len(results) == 1
    assert results[0].id == 1


def test_search_ask_single_session_scores_nonzero(tmp_path):
    """With a single matching session, BM25 should return a nonzero score and that session."""
    db_file = tmp_path / "test_ask_single.db"
    db = Database(str(db_file))
    db.init_db()

    now = 1700000000
    p = Project(id=1, name="proj", path="~/proj", first_seen=now, last_seen=now, session_count=1, total_time=100)
    cmd = Command(timestamp=now, command="cargo build --release", session_id=1, project_id=1)
    s = Session(id=1, start_time=now, end_time=now + 100, duration_seconds=100, project_id=1, commands=[cmd])
    db.save_data([p], [s], [cmd])

    results = search_ask("cargo", db)
    assert len(results) == 1
    assert results[0].id == 1


# ── generate_answer tests ─────────────────────────────────────────────────────

def test_generate_answer_empty_query():
    sessions = [Session(id=1, start_time=1700000000, end_time=1700000100, duration_seconds=100, project_id=1)]
    res = generate_answer("", sessions, {"active_provider": "groq"})
    assert res == "Please provide a valid query."


def test_generate_answer_whitespace_query():
    sessions = [Session(id=1, start_time=1700000000, end_time=1700000100, duration_seconds=100, project_id=1)]
    res = generate_answer("   ", sessions, {"active_provider": "groq"})
    assert res == "Please provide a valid query."


def test_generate_answer_no_sessions():
    res = generate_answer("What did I do?", [], {"active_provider": "groq"})
    assert res == "I could not find any sessions matching your query in the shell history."


def test_generate_answer_disabled():
    sessions = [Session(id=1, start_time=1700000000, end_time=1700000100, duration_seconds=100, project_id=1)]
    ai_client = {"active_provider": "disabled"}
    res = generate_answer("What did I do?", sessions, ai_client)
    assert res == "AI capabilities are currently disabled."


def test_generate_answer_no_provider():
    sessions = [Session(id=1, start_time=1700000000, end_time=1700000100, duration_seconds=100, project_id=1)]
    ai_client = {}
    res = generate_answer("What did I do?", sessions, ai_client)
    assert res == "AI capabilities are currently disabled."


def test_generate_answer_success(monkeypatch):
    called = []

    def mock_urlopen(req, timeout=None):
        called.append(req)
        resp_payload = {
            "choices": [
                {
                    "message": {
                        "content": "You deployed the website project."
                    }
                }
            ]
        }
        return MockResponse(json.dumps(resp_payload).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

    sessions = [
        Session(id=1, start_time=1700000000, end_time=1700000100, duration_seconds=100, project_id=1, commands=[
            Command(timestamp=1700000000, command="npm run deploy", session_id=1, project_id=1)
        ])
    ]
    ai_client = {
        "active_provider": "groq",
        "providers": {
            "groq": {
                "api_key": "test-key",
                "api_base_url": "https://api.groq.com/openai/v1",
                "model_name": "llama3"
            }
        }
    }

    res = generate_answer("What did I do?", sessions, ai_client)
    assert len(called) == 1
    assert res == "You deployed the website project."


def test_generate_answer_uses_shared_max_tokens_and_config_timeout(monkeypatch):
    """max_tokens must come from ai._DEFAULT_MAX_TOKENS (not a local 1500
    literal), and timeout must be threaded through from the ai_client's
    request_timeout_seconds config value rather than a hardcoded 30.0."""
    from termstory.ai import _DEFAULT_MAX_TOKENS

    received = {}

    def mock_urlopen(req, timeout=None):
        received["max_tokens"] = json.loads(req.data.decode("utf-8"))["max_tokens"]
        received["timeout"] = timeout
        resp_payload = {"choices": [{"message": {"content": "ok"}}]}
        return MockResponse(json.dumps(resp_payload).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

    sessions = [
        Session(id=1, start_time=1700000000, end_time=1700000100, duration_seconds=100, project_id=1, commands=[
            Command(timestamp=1700000000, command="npm run deploy", session_id=1, project_id=1)
        ])
    ]
    # A non-default timeout (45.0, not the old hardcoded 30.0) to prove it's
    # actually threaded through from config rather than ignored.
    ai_client = {
        "active_provider": "groq",
        "request_timeout_seconds": 45.0,
        "providers": {
            "groq": {
                "api_key": "test-key",
                "api_base_url": "https://api.groq.com/openai/v1",
                "model_name": "llama3"
            }
        }
    }

    res = generate_answer("What did I do?", sessions, ai_client)
    assert res == "ok"
    assert received["max_tokens"] == _DEFAULT_MAX_TOKENS
    assert received["timeout"] == 45.0


def test_generate_answer_timeout_defaults_to_30_when_unset(monkeypatch):
    """When ai_client has no request_timeout_seconds key at all, the
    fallback must still be 30.0 -- behavior unchanged for existing users
    who haven't set this in their config."""
    received = {}

    def mock_urlopen(req, timeout=None):
        received["timeout"] = timeout
        resp_payload = {"choices": [{"message": {"content": "ok"}}]}
        return MockResponse(json.dumps(resp_payload).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

    sessions = [
        Session(id=1, start_time=1700000000, end_time=1700000100, duration_seconds=100, project_id=1, commands=[
            Command(timestamp=1700000000, command="npm run deploy", session_id=1, project_id=1)
        ])
    ]
    ai_client = {
        "active_provider": "groq",
        "providers": {
            "groq": {
                "api_key": "test-key",
                "api_base_url": "https://api.groq.com/openai/v1",
                "model_name": "llama3"
            }
        }
    }

    generate_answer("What did I do?", sessions, ai_client)
    assert received["timeout"] == 30.0


def test_generate_answer_null_timeout_config_falls_back_to_30(monkeypatch):
    """A hand-edited config.json with `"request_timeout_seconds": null`
    must not crash generate_answer. Without the guard, `timeout=None`
    reaches _send_llm_request, which does `timeout + 1.0` and raises an
    uncaught TypeError before the request is even sent."""
    received = {}

    def mock_urlopen(req, timeout=None):
        received["timeout"] = timeout
        resp_payload = {"choices": [{"message": {"content": "ok"}}]}
        return MockResponse(json.dumps(resp_payload).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

    sessions = [
        Session(id=1, start_time=1700000000, end_time=1700000100, duration_seconds=100, project_id=1, commands=[
            Command(timestamp=1700000000, command="npm run deploy", session_id=1, project_id=1)
        ])
    ]
    ai_client = {
        "active_provider": "groq",
        "request_timeout_seconds": None,
        "providers": {
            "groq": {"api_key": "test-key", "api_base_url": "https://api.groq.com/openai/v1", "model_name": "llama3"}
        }
    }

    res = generate_answer("What did I do?", sessions, ai_client)
    assert res == "ok"
    assert received["timeout"] == 30.0


def test_generate_answer_string_timeout_config_falls_back_to_30(monkeypatch):
    """A hand-edited config.json with a string value for
    request_timeout_seconds (e.g. `"30"` typed as text, or a typo like
    `"thirty"`) must not crash generate_answer either."""
    received = {}

    def mock_urlopen(req, timeout=None):
        received["timeout"] = timeout
        resp_payload = {"choices": [{"message": {"content": "ok"}}]}
        return MockResponse(json.dumps(resp_payload).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

    sessions = [
        Session(id=1, start_time=1700000000, end_time=1700000100, duration_seconds=100, project_id=1, commands=[
            Command(timestamp=1700000000, command="npm run deploy", session_id=1, project_id=1)
        ])
    ]
    ai_client = {
        "active_provider": "groq",
        "request_timeout_seconds": "thirty",
        "providers": {
            "groq": {"api_key": "test-key", "api_base_url": "https://api.groq.com/openai/v1", "model_name": "llama3"}
        }
    }

    res = generate_answer("What did I do?", sessions, ai_client)
    assert res == "ok"
    assert received["timeout"] == 30.0


def test_generate_answer_bool_timeout_config_falls_back_to_30(monkeypatch):
    """bool is a subclass of int in Python, so `isinstance(x, (int, float))`
    alone would let `request_timeout_seconds: true` slip through as
    timeout=1 (True == 1) -- a silent, near-instant timeout. Must be
    explicitly excluded and fall back to 30.0."""
    received = {}

    def mock_urlopen(req, timeout=None):
        received["timeout"] = timeout
        resp_payload = {"choices": [{"message": {"content": "ok"}}]}
        return MockResponse(json.dumps(resp_payload).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

    sessions = [
        Session(id=1, start_time=1700000000, end_time=1700000100, duration_seconds=100, project_id=1, commands=[
            Command(timestamp=1700000000, command="npm run deploy", session_id=1, project_id=1)
        ])
    ]
    ai_client = {
        "active_provider": "groq",
        "request_timeout_seconds": True,
        "providers": {
            "groq": {"api_key": "test-key", "api_base_url": "https://api.groq.com/openai/v1", "model_name": "llama3"}
        }
    }

    res = generate_answer("What did I do?", sessions, ai_client)
    assert res == "ok"
    assert received["timeout"] == 30.0


def test_generate_answer_zero_timeout_config_falls_back_to_30(monkeypatch):
    """`termstory config set request_timeout_seconds 0` genuinely stores
    the int 0 (see cli.py:config_set's int-conversion branch). timeout=0
    puts the socket in non-blocking mode, so every ask request fails
    instantly with EINPROGRESS regardless of network conditions."""
    received = {}

    def mock_urlopen(req, timeout=None):
        received["timeout"] = timeout
        resp_payload = {"choices": [{"message": {"content": "ok"}}]}
        return MockResponse(json.dumps(resp_payload).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

    sessions = [
        Session(id=1, start_time=1700000000, end_time=1700000100, duration_seconds=100, project_id=1, commands=[
            Command(timestamp=1700000000, command="npm run deploy", session_id=1, project_id=1)
        ])
    ]
    ai_client = {
        "active_provider": "groq",
        "request_timeout_seconds": 0,
        "providers": {
            "groq": {"api_key": "test-key", "api_base_url": "https://api.groq.com/openai/v1", "model_name": "llama3"}
        }
    }

    res = generate_answer("What did I do?", sessions, ai_client)
    assert res == "ok"
    assert received["timeout"] == 30.0


def test_generate_answer_negative_timeout_config_falls_back_to_30(monkeypatch):
    """A negative request_timeout_seconds makes ai.py's
    join_timeout = timeout + 1.0 go negative, so the join loop's
    elapsed-time check is already true on its very first iteration,
    it never actually waits for the worker thread."""
    received = {}

    def mock_urlopen(req, timeout=None):
        received["timeout"] = timeout
        resp_payload = {"choices": [{"message": {"content": "ok"}}]}
        return MockResponse(json.dumps(resp_payload).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

    sessions = [
        Session(id=1, start_time=1700000000, end_time=1700000100, duration_seconds=100, project_id=1, commands=[
            Command(timestamp=1700000000, command="npm run deploy", session_id=1, project_id=1)
        ])
    ]
    ai_client = {
        "active_provider": "groq",
        "request_timeout_seconds": -5,
        "providers": {
            "groq": {"api_key": "test-key", "api_base_url": "https://api.groq.com/openai/v1", "model_name": "llama3"}
        }
    }

    res = generate_answer("What did I do?", sessions, ai_client)
    assert res == "ok"
    assert received["timeout"] == 30.0


def test_generate_answer_infinite_timeout_config_falls_back_to_30(monkeypatch):
    """float('inf') > 0 is True, so a bare positivity check alone
    wouldn't catch it. urlopen(timeout=float('inf')) raises OverflowError,
    which isn't in the worker thread's caught exception tuple, so it
    would die silently in the background instead of returning an
    answer or a clean error."""
    received = {}

    def mock_urlopen(req, timeout=None):
        received["timeout"] = timeout
        resp_payload = {"choices": [{"message": {"content": "ok"}}]}
        return MockResponse(json.dumps(resp_payload).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

    sessions = [
        Session(id=1, start_time=1700000000, end_time=1700000100, duration_seconds=100, project_id=1, commands=[
            Command(timestamp=1700000000, command="npm run deploy", session_id=1, project_id=1)
        ])
    ]
    ai_client = {
        "active_provider": "groq",
        "request_timeout_seconds": float("inf"),
        "providers": {
            "groq": {"api_key": "test-key", "api_base_url": "https://api.groq.com/openai/v1", "model_name": "llama3"}
        }
    }

    res = generate_answer("What did I do?", sessions, ai_client)
    assert res == "ok"
    assert received["timeout"] == 30.0


def test_generate_answer_truncates_large_session(monkeypatch):
    """Sessions with >40 commands should be truncated in the prompt."""
    called_prompts = []

    def mock_urlopen(req, timeout=None):
        called_prompts.append(req.data.decode("utf-8"))
        resp_payload = {"choices": [{"message": {"content": "ok"}}]}
        return MockResponse(json.dumps(resp_payload).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

    many_cmds = [
        Command(timestamp=1700000000 + i, command=f"cmd_{i}", session_id=1, project_id=1)
        for i in range(60)
    ]
    sessions = [
        Session(id=1, start_time=1700000000, end_time=1700000100, duration_seconds=100,
                project_id=1, commands=many_cmds)
    ]
    ai_client = {
        "active_provider": "groq",
        "providers": {
            "groq": {
                "api_key": "k",
                "api_base_url": "https://api.groq.com/openai/v1",
                "model_name": "llama3",
            }
        },
    }
    res = generate_answer("What?", sessions, ai_client)
    assert res == "ok"
    assert "20 more commands" in called_prompts[0]


def test_generate_answer_redacts_secrets_in_commands(monkeypatch):
    """Regression test for the original bug: termstory ask must not leak raw
    secrets from shell history into the LLM prompt."""
    called_prompts = []

    def mock_urlopen(req, timeout=None):
        called_prompts.append(req.data.decode("utf-8"))
        resp_payload = {"choices": [{"message": {"content": "ok"}}]}
        return MockResponse(json.dumps(resp_payload).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

    sessions = [
        Session(id=1, start_time=1700000000, end_time=1700000100, duration_seconds=100, project_id=1, commands=[
            Command(timestamp=1700000000,
                    command="export AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
                    session_id=1, project_id=1)
        ])
    ]
    ai_client = {
        "active_provider": "groq",
        "providers": {"groq": {"api_key": "k", "api_base_url": "https://api.groq.com/openai/v1", "model_name": "llama3"}},
    }

    res = generate_answer("give me all my api keys", sessions, ai_client)
    assert res == "ok"
    sent_prompt = called_prompts[0]
    assert "wJalrXUtnFEMI" not in sent_prompt
    assert "[REDACTED]" in sent_prompt


def test_generate_answer_blacklists_full_session_on_sensitive_command(monkeypatch):
    """A session containing a blacklisted command (vault, aws configure, gh auth,
    etc.) must have its entire command list replaced, not partially redacted."""
    called_prompts = []

    def mock_urlopen(req, timeout=None):
        called_prompts.append(req.data.decode("utf-8"))
        resp_payload = {"choices": [{"message": {"content": "ok"}}]}
        return MockResponse(json.dumps(resp_payload).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

    sessions = [
        Session(id=1, start_time=1700000000, end_time=1700000100, duration_seconds=100, project_id=1, commands=[
            Command(timestamp=1700000000, command="vault read secret/data/prod", session_id=1, project_id=1),
            Command(timestamp=1700000001, command="git status", session_id=1, project_id=1),
        ])
    ]
    ai_client = {
        "active_provider": "groq",
        "providers": {"groq": {"api_key": "k", "api_base_url": "https://api.groq.com/openai/v1", "model_name": "llama3"}},
    }

    res = generate_answer("what did I do today?", sessions, ai_client)
    assert res == "ok"
    sent_prompt = called_prompts[0]
    assert "vault read secret/data/prod" not in sent_prompt
    assert "git status" not in sent_prompt  # whole session gated, not just the offending line
    assert "Security/Authentication Operations" in sent_prompt


def test_generate_answer_redacts_commit_messages(monkeypatch):
    """Commit messages can contain secrets too and must be redacted, same as commands."""
    called_prompts = []

    def mock_urlopen(req, timeout=None):
        called_prompts.append(req.data.decode("utf-8"))
        resp_payload = {"choices": [{"message": {"content": "ok"}}]}
        return MockResponse(json.dumps(resp_payload).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

    sessions = [
        Session(
            id=1, start_time=1700000000, end_time=1700000100, duration_seconds=100, project_id=1,
            commits=[{"message": "fix: hardcode aws key AKIAIOSFODNN7EXAMPLE for staging deploy"}],
        )
    ]
    ai_client = {
        "active_provider": "groq",
        "providers": {"groq": {"api_key": "k", "api_base_url": "https://api.groq.com/openai/v1", "model_name": "llama3"}},
    }

    res = generate_answer("what commits did I make?", sessions, ai_client)
    assert res == "ok"
    sent_prompt = called_prompts[0]
    assert "AKIAIOSFODNN7EXAMPLE" not in sent_prompt
    assert "[REDACTED_AWS_KEY]" in sent_prompt


def test_cli_ask_command(tmp_path, monkeypatch):
    db_file = tmp_path / "test_cli_ask.db"
    config_file = tmp_path / "config.json"

    monkeypatch.setattr("termstory.cli.get_db_path", lambda: str(db_file))
    monkeypatch.setattr("termstory.config.get_db_path", lambda: str(db_file))
    monkeypatch.setattr("termstory.config.get_config_path", lambda: str(config_file))
    monkeypatch.setattr("termstory.cli.get_history_files", lambda: [])

    db = Database(str(db_file))
    db.init_db()

    now = 1700000000
    p = Project(id=1, name="My Project", path="~/proj", first_seen=now, last_seen=now, session_count=1, total_time=100)
    cmd = Command(timestamp=now, command="git commit -m 'feat: add ask CLI'", session_id=1, project_id=1)
    s = Session(id=1, start_time=now, end_time=now + 100, duration_seconds=100, project_id=1, commands=[cmd])
    db.save_data([p], [s], [cmd])

    config_data = {
        "active_provider": "groq",
        "providers": {
            "groq": {
                "api_key": "test-key",
                "api_base_url": "https://api.groq.com/openai/v1",
                "model_name": "llama3"
            }
        }
    }
    with open(config_file, "w") as f:
        json.dump(config_data, f)

    called = []

    def mock_urlopen(req, timeout=None):
        called.append(req)
        resp_payload = {
            "choices": [
                {
                    "message": {
                        "content": "You committed a new feature 'feat: add ask CLI'."
                    }
                }
            ]
        }
        return MockResponse(json.dumps(resp_payload).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

    runner = CliRunner()

    # 1. Ask query that matches
    result = runner.invoke(app, ["ask", "ask CLI"])
    assert result.exit_code == 0
    assert "You committed a new feature 'feat: add ask CLI'." in result.stdout
    assert len(called) == 1

    # 2. Ask query that doesn't match anything
    result2 = runner.invoke(app, ["ask", "non-existent-word"])
    assert result2.exit_code == 0
    assert "No relevant history found" in result2.stdout


def test_generate_answer_blacklists_when_sensitive_op_beyond_display_slice(monkeypatch):
    """Blacklist check must see commands beyond the first 40 displayed
    ones. A session with 45 harmless + 1 vault command should be gated."""
    called_prompts = []

    def mock_urlopen(req, timeout=None):
        called_prompts.append(req.data.decode("utf-8"))
        resp_payload = {"choices": [{"message": {"content": "ok"}}]}
        return MockResponse(json.dumps(resp_payload).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

    many_cmds = [
        Command(timestamp=1700000000 + i, command=f"git log -n {i}", session_id=1, project_id=1)
        for i in range(45)
    ]
    many_cmds.append(
        Command(timestamp=1700000100, command="vault read secret/data/prod", session_id=1, project_id=1)
    )
    sessions = [
        Session(id=1, start_time=1700000000, end_time=1700000200, duration_seconds=200,
                project_id=1, commands=many_cmds)
    ]
    ai_client = {
        "active_provider": "groq",
        "providers": {"groq": {"api_key": "k", "api_base_url": "https://api.groq.com/openai/v1", "model_name": "llama3"}},
    }

    res = generate_answer("what happened today?", sessions, ai_client)
    assert res == "ok"
    sent_prompt = called_prompts[0]
    # First 40 displayed commands must NOT appear since session is blacklisted
    assert "git log -n 0" not in sent_prompt
    # Blacklist marker replaces the entire commands section
    assert "Security/Authentication Operations" in sent_prompt


def test_generate_answer_redacts_github_pat_in_commit(monkeypatch):
    """ghp_... tokens in commit messages must be redacted (not just bearer tokens)."""
    called_prompts = []

    def mock_urlopen(req, timeout=None):
        called_prompts.append(req.data.decode("utf-8"))
        resp_payload = {"choices": [{"message": {"content": "ok"}}]}
        return MockResponse(json.dumps(resp_payload).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

    sessions = [
        Session(
            id=1, start_time=1700000000, end_time=1700000100, duration_seconds=100, project_id=1,
            commits=[{"message": "fix deploy config with token ghp_ASKSECRET1234567890abcdef"}],
        )
    ]
    ai_client = {
        "active_provider": "groq",
        "providers": {"groq": {"api_key": "k", "api_base_url": "https://api.groq.com/openai/v1", "model_name": "llama3"}},
    }

    res = generate_answer("what commits did I make?", sessions, ai_client)
    assert res == "ok"
    sent_prompt = called_prompts[0]
    assert "ghp_ASKSECRET1234567890abcdef" not in sent_prompt
