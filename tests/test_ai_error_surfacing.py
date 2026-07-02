import json
import urllib.request
import urllib.error
from io import BytesIO
from termstory.ai import (
    generate_ai_summary,
    get_last_ai_error,
    clear_last_ai_error
)

import pytest

@pytest.fixture(autouse=True)
def mock_config_path(tmp_path, monkeypatch):
    config_file = tmp_path / "config.json"
    monkeypatch.setattr("termstory.config.get_config_path", lambda: str(config_file))

def test_get_and_clear_error():
    clear_last_ai_error()
    assert get_last_ai_error() is None

def test_invalid_url_sets_error():
    clear_last_ai_error()
    res = generate_ai_summary(
        ["pytest tests/"],
        "test-key",
        "",
        "llama3",
        "groq"
    )
    assert res is None
    assert get_last_ai_error() == "API Base URL is not configured or invalid."

def test_url_error_sets_error(monkeypatch):
    clear_last_ai_error()
    
    def mock_urlopen(req, timeout=None):
        raise urllib.error.URLError("Connection refused")
        
    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    
    res = generate_ai_summary(
        ["pytest tests/"],
        "test-key",
        "https://api.groq.com/openai/v1",
        "llama3",
        "groq"
    )
    assert res is None
    assert "Connection refused" in get_last_ai_error()

def test_http_error_json_body_parsing(monkeypatch):
    clear_last_ai_error()
    
    def mock_urlopen(req, timeout=None):
        # Create an HTTPError with a json body containing an error message
        body = json.dumps({
            "error": {
                "message": "Invalid API Key provided"
            }
        }).encode("utf-8")
        fp = BytesIO(body)
        raise urllib.error.HTTPError(
            url="https://api.groq.com/openai/v1",
            code=401,
            msg="Unauthorized",
            hdrs={},
            fp=fp
        )
        
    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    
    res = generate_ai_summary(
        ["pytest tests/"],
        "test-key",
        "https://api.groq.com/openai/v1",
        "llama3",
        "groq"
    )
    assert res is None
    assert get_last_ai_error() == "HTTP Error 401: Invalid API Key provided"

def test_http_error_non_json_body_parsing(monkeypatch):
    clear_last_ai_error()
    
    def mock_urlopen(req, timeout=None):
        # Create an HTTPError with a non-json text body
        body = b"Service Unavailable"
        fp = BytesIO(body)
        raise urllib.error.HTTPError(
            url="https://api.groq.com/openai/v1",
            code=503,
            msg="Service Unavailable",
            hdrs={},
            fp=fp
        )
        
    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    
    res = generate_ai_summary(
        ["pytest tests/"],
        "test-key",
        "https://api.groq.com/openai/v1",
        "llama3",
        "groq"
    )
    assert res is None
    assert get_last_ai_error() == "HTTP Error 503: Service Unavailable"


def test_concurrent_threads_share_latest_error(monkeypatch):
    import threading
    from termstory import ai

    fake_now = [1000.0]

    def fake_sleep(seconds):
        fake_now[0] += seconds

    monkeypatch.setattr(ai.time, "time", lambda: fake_now[0])
    monkeypatch.setattr(ai.time, "sleep", fake_sleep)
    
    first_error_recorded = threading.Event()
    second_error_recorded = threading.Event()
    thread_errors = {}
    
    def run_thread_a():
        clear_last_ai_error()
        ai._send_llm_request("prompt", "key", "", "model", "groq")
        first_error_recorded.set()
        second_error_recorded.wait()
        thread_errors["A"] = get_last_ai_error()
        
    def run_thread_b():
        def mock_urlopen(req, timeout=None):
            raise urllib.error.URLError("Connection refused")
            
        monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
        
        first_error_recorded.wait()
        ai._send_llm_request("prompt", "key", "https://api.groq.com/openai/v1", "model", "groq")
        thread_errors["B"] = get_last_ai_error()
        second_error_recorded.set()
        
    ta = threading.Thread(target=run_thread_a)
    tb = threading.Thread(target=run_thread_b)
    ta.start()
    tb.start()
    ta.join()
    tb.join()
    
    assert "Connection refused" in thread_errors["A"]
    assert "Connection refused" in thread_errors["B"]


def test_http_error_empty_body_fallback_to_reason(monkeypatch):
    clear_last_ai_error()
    
    def mock_urlopen(req, timeout=None):
        # Create an HTTPError with an empty body
        fp = BytesIO(b"")
        raise urllib.error.HTTPError(
            url="https://api.groq.com/openai/v1",
            code=403,
            msg="Forbidden Request",
            hdrs={},
            fp=fp
        )
        
    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    
    res = generate_ai_summary(
        ["pytest tests/"],
        "test-key",
        "https://api.groq.com/openai/v1",
        "llama3",
        "groq"
    )
    assert res is None
    # e.reason for HTTPError returns the HTTP status message (msg parameter, here "Forbidden Request")
    assert get_last_ai_error() == "HTTP Error 403: Forbidden Request"


def test_http_error_whitespace_normalization(monkeypatch):
    clear_last_ai_error()
    
    def mock_urlopen(req, timeout=None):
        # Create an HTTPError with messy newlines and extra spaces in json error message
        body = json.dumps({
            "error": {
                "message": "  Something \n   went \n\t wrong.  "
            }
        }).encode("utf-8")
        fp = BytesIO(body)
        raise urllib.error.HTTPError(
            url="https://api.groq.com/openai/v1",
            code=500,
            msg="Internal Server Error",
            hdrs={},
            fp=fp
        )
        
    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    
    res = generate_ai_summary(
        ["pytest tests/"],
        "test-key",
        "https://api.groq.com/openai/v1",
        "llama3",
        "groq"
    )
    assert res is None
    assert get_last_ai_error() == "HTTP Error 500: Something went wrong."

def test_get_project_context_logs_warning_on_db_error(monkeypatch, caplog):
    """When the DB lookup raises in _get_project_context_from_db,
    the function returns None and logs a warning. Regression test for issue #109."""
    from termstory.ai import _get_project_context_from_db

    class BrokenCursor:
        def execute(self, *args, **kwargs):
            raise RuntimeError("simulated DB failure in fetch")

    class BrokenConn:
        def cursor(self):
            return BrokenCursor()
        def close(self):
            pass

    class BrokenDB:
        def get_connection(self):
            return BrokenConn()

    monkeypatch.setattr("termstory.database.Database", lambda *args, **kwargs: BrokenDB())
    monkeypatch.setattr("termstory.config.get_db_path", lambda: "/tmp/fake.db")

    with caplog.at_level("WARNING", logger="termstory.ai"):
        result = _get_project_context_from_db("my-project")

    assert result is None
    assert any("_get_project_context_from_db" in r.message and "simulated DB failure" in str(r.exc_info[1])
               for r in caplog.records if r.exc_info)


def test_get_all_active_project_contexts_logs_warning_on_db_error(monkeypatch, caplog):
    """When the DB lookup raises in _get_all_active_project_contexts,
    the function returns an empty list and logs a warning. Regression test for issue #109."""
    from termstory.ai import _get_all_active_project_contexts

    class BrokenCursor:
        def execute(self, *args, **kwargs):
            raise RuntimeError("simulated DB failure in fetchall")

    class BrokenConn:
        def cursor(self):
            return BrokenCursor()
        def close(self):
            pass

    class BrokenDB:
        def get_connection(self):
            return BrokenConn()

    monkeypatch.setattr("termstory.database.Database", lambda *args, **kwargs: BrokenDB())
    monkeypatch.setattr("termstory.config.get_db_path", lambda: "/tmp/fake.db")

    with caplog.at_level("WARNING", logger="termstory.ai"):
        result = _get_all_active_project_contexts()

    assert result == []
    assert any("_get_all_active_project_contexts" in r.message and "simulated DB failure" in str(r.exc_info[1])
               for r in caplog.records if r.exc_info)
