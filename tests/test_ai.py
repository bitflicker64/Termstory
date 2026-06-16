import json
import urllib.request
import urllib.error
from io import BytesIO
from termstory.ai import generate_ai_summary, generate_timeframe_summary

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

def test_ai_disabled():
    res = generate_ai_summary(["git status"], "key", "http://base", "model", "disabled")
    assert res is None

def test_ai_blacklisted():
    res = generate_ai_summary(["vault read secret"], "key", "http://base", "model", "groq")
    assert res == "Security/Authentication Operations"

def test_ai_success_groq(monkeypatch):
    called = []
    
    def mock_urlopen(req, timeout=None):
        called.append(req)
        # Assertions on the request
        assert req.get_header("Content-type") == "application/json"
        assert req.get_header("Authorization") == "Bearer test-key"
        assert req.full_url == "https://api.groq.com/openai/v1/chat/completions"
        
        # Verify body
        body = json.loads(req.data.decode("utf-8"))
        assert body["model"] == "llama3"
        assert "Translate the developer's raw shell commands and Git commits" in body["messages"][0]["content"]
        
        resp_payload = {
            "choices": [
                {
                    "message": {
                        "content": "Fixed integration tests"
                    }
                }
            ]
        }
        return MockResponse(json.dumps(resp_payload).encode("utf-8"))
        
    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    
    res = generate_ai_summary(
        ["pytest tests/"],
        "test-key",
        "https://api.groq.com/openai/v1",
        "llama3",
        "groq"
    )
    assert len(called) == 1
    assert res == "Fixed integration tests"

def test_ai_success_ollama(monkeypatch):
    called = []
    
    def mock_urlopen(req, timeout=None):
        called.append(req)
        assert req.get_header("Authorization") is None # No auth key for Ollama
        assert req.full_url == "http://localhost:11434/v1/chat/completions"
        
        resp_payload = {
            "choices": [
                {
                    "message": {
                        "content": "\"Run local migrations\"" # With quotes to test trimming
                    }
                }
            ]
        }
        return MockResponse(json.dumps(resp_payload).encode("utf-8"))
        
    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    
    res = generate_ai_summary(
        ["python3 manage.py migrate"],
        "",
        "http://localhost:11434/v1",
        "llama3",
        "ollama"
    )
    assert len(called) == 1
    assert res == "Run local migrations"

def test_ai_failure_exception(monkeypatch):
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


def test_ai_url_normalization(monkeypatch):
    test_urls = [
        "https://api.groq.com/openai/v1/",
        "https://api.groq.com/openai/v1",
        "https://api.groq.com/openai/v1/chat/completions",
        "https://api.groq.com/openai/v1/chat/completions/"
    ]
    
    for url in test_urls:
        called_url = []
        def mock_urlopen(req, timeout=None):
            called_url.append(req.full_url)
            resp_payload = {
                "choices": [{"message": {"content": "Tested URL normalization"}}]
            }
            return MockResponse(json.dumps(resp_payload).encode("utf-8"))
            
        monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
        res = generate_ai_summary(
            ["pytest tests/"],
            "test-key",
            url,
            "llama3",
            "groq"
        )
        assert res == "Tested URL normalization"
        assert len(called_url) == 1
        assert called_url[0] == "https://api.groq.com/openai/v1/chat/completions"


def test_ai_empty_or_blank_key(monkeypatch):
    blank_keys = ["", "   ", None]
    for key in blank_keys:
        called = []
        def mock_urlopen(req, timeout=None):
            called.append(req)
            assert req.get_header("Authorization") is None
            resp_payload = {
                "choices": [{"message": {"content": "Tested blank key"}}]
            }
            return MockResponse(json.dumps(resp_payload).encode("utf-8"))
            
        monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
        res = generate_ai_summary(
            ["pytest tests/"],
            key,
            "https://api.groq.com/openai/v1",
            "llama3",
            "groq"
        )
        assert res == "Tested blank key"
        assert len(called) == 1


def test_ai_invalid_url():
    # If base url is empty or None, it should return None immediately without making request
    res = generate_ai_summary(
        ["pytest tests/"],
        "test-key",
        "",
        "llama3",
        "groq"
    )
    assert res is None
    
    res2 = generate_ai_summary(
        ["pytest tests/"],
        "test-key",
        None,
        "llama3",
        "groq"
    )
    assert res2 is None


def test_ai_generate_executive_review(monkeypatch):
    called = []
    def mock_urlopen(req, timeout=None):
        called.append(req)
        assert req.get_header("Content-type") == "application/json"
        assert req.get_header("Authorization") == "Bearer test-key"
        
        # Verify body
        body = json.loads(req.data.decode("utf-8"))
        assert body["model"] == "gpt-4o"
        assert "Write a highly-personalized, modern engineering review" in body["messages"][0]["content"]
        
        resp_payload = {
            "choices": [
                {
                    "message": {
                        "content": "Worked on TermStory and added tests."
                    }
                }
            ]
        }
        return MockResponse(json.dumps(resp_payload).encode("utf-8"))
        
    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    
    res = generate_timeframe_summary(
        stats_summary="User worked 5h total. 100% on TermStory. 10 total Git commits.",
        api_key="test-key",
        api_base_url="https://api.openai.com/v1",
        model_name="gpt-4o",
        provider="openai"
    )
    assert len(called) == 1
    assert res == "Worked on TermStory and added tests."


def test_ai_summary_with_commits(monkeypatch):
    called = []
    
    def mock_urlopen(req, timeout=None):
        called.append(req)
        body = json.loads(req.data.decode("utf-8"))
        content = body["messages"][0]["content"]
        assert "Git Commit Messages:" in content
        assert "feat: added onboarding" in content
        assert "fix: layout alignment" in content
        
        resp_payload = {
            "choices": [{"message": {"content": "Summary with commits."}}]
        }
        return MockResponse(json.dumps(resp_payload).encode("utf-8"))
        
    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    
    res = generate_ai_summary(
        commands=["git commit -m 'feat: added onboarding'"],
        api_key="test-key",
        api_base_url="https://api.openai.com/v1",
        model_name="gpt-4o",
        provider="openai",
        commits=["feat: added onboarding", "fix: layout alignment"]
    )
    assert len(called) == 1
    assert res == "Summary with commits."


def test_daily_chronicle_prompt():
    from termstory.ai import generate_daily_chronicle_prompt
    from termstory.models import Session, Command, Project
    
    cmd = Command(timestamp=1780460000, command="vim main.py")
    s = Session(id=1, start_time=1780460000, end_time=1780461000, duration_seconds=1000, project_id=1, commands=[cmd])
    p = Project(id=1, name="TermStory", path="~/projects/termstory", first_seen=1780460000, last_seen=1780461000, session_count=1, total_time=1000)
    
    prompt = generate_daily_chronicle_prompt(
        github_username="@testuser",
        session_date="June 03, 2026",
        sessions=[s],
        projects=[p]
    )
    
    assert "@testuser" in prompt
    assert "June 03, 2026" in prompt
    assert "USE SECOND-PERSON" in prompt
    assert "DYNAMIC HANDLE" in prompt
    assert "INFER HUMANITY" in prompt
    assert "NO CORPORATE SLOP" in prompt


def test_ai_summary_max_tokens_and_timeout(monkeypatch):
    """generate_ai_summary should request max_tokens=500 and the configured timeout."""
    captured = {}

    def mock_urlopen(req, timeout=None):
        captured["timeout"] = timeout
        captured["body"] = json.loads(req.data.decode("utf-8"))
        resp_payload = {"choices": [{"message": {"content": "ok"}}]}
        return MockResponse(json.dumps(resp_payload).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    monkeypatch.setattr("termstory.config.load_config", lambda: {"request_timeout_seconds": 42})

    res = generate_ai_summary(
        ["pytest tests/"], "test-key", "https://api.groq.com/openai/v1", "llama3", "groq"
    )
    assert res == "ok"
    assert captured["body"]["max_tokens"] == 500
    assert captured["timeout"] == 42


def test_ai_summary_command_truncation(monkeypatch):
    """More than MAX_COMMANDS_PER_PROMPT commands should be truncated to the most recent 80."""
    captured = {}

    def mock_urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        resp_payload = {"choices": [{"message": {"content": "ok"}}]}
        return MockResponse(json.dumps(resp_payload).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

    commands = [f"echo cmd-{i}" for i in range(200)]
    res = generate_ai_summary(
        commands, "test-key", "https://api.groq.com/openai/v1", "llama3", "groq"
    )
    assert res == "ok"
    content = captured["body"]["messages"][0]["content"]
    # Oldest commands should be dropped, most-recent kept
    assert "echo cmd-199" in content
    assert "echo cmd-120" in content
    assert "echo cmd-119" not in content
    assert "echo cmd-0\n" not in content


def test_timeframe_summary_max_tokens(monkeypatch):
    captured = {}

    def mock_urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        captured["timeout"] = timeout
        resp_payload = {"choices": [{"message": {"content": "ok"}}]}
        return MockResponse(json.dumps(resp_payload).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

    res = generate_timeframe_summary(
        stats_summary="stats", api_key="k", api_base_url="https://api.openai.com/v1",
        model_name="gpt-4o", provider="openai"
    )
    assert res == "ok"
    assert captured["body"]["max_tokens"] == 1500
    # Falls back to default 30 when config lacks the key
    assert captured["timeout"] == 30


def test_daily_chronicle_session_truncation():
    """generate_daily_chronicle_prompt should cap sessions at the 20 most recent."""
    from termstory.ai import generate_daily_chronicle_prompt
    from termstory.models import Session, Command, Project

    sessions = []
    base = 1780460000
    for i in range(30):
        start = base + i * 10000
        cmd = Command(timestamp=start, command=f"vim file_{i}.py")
        sessions.append(Session(
            id=i, start_time=start, end_time=start + 1000, duration_seconds=1000,
            project_id=1, commands=[cmd]
        ))
    p = Project(id=1, name="TermStory", path="~/p", first_seen=base,
                last_seen=base, session_count=30, total_time=1000)

    prompt = generate_daily_chronicle_prompt("@u", "June 03, 2026", sessions, [p])
    # Only the 20 most recent sessions (10..29) should appear
    assert "vim file_29.py" in prompt
    assert "vim file_10.py" in prompt
    assert "vim file_9.py" not in prompt
    assert "vim file_0.py" not in prompt


def test_ai_explicit_timeout_override(monkeypatch):
    """Calling generate_ai_summary with a custom timeout should override any config defaults."""
    captured = {}

    def mock_urlopen(req, timeout=None):
        captured["timeout"] = timeout
        resp_payload = {"choices": [{"message": {"content": "ok"}}]}
        return MockResponse(json.dumps(resp_payload).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    # Even if config sets it to 42, the explicit call parameter should win
    monkeypatch.setattr("termstory.config.load_config", lambda: {"request_timeout_seconds": 42})

    res = generate_ai_summary(
        ["pytest tests/"], "test-key", "https://api.groq.com/openai/v1", "llama3", "groq",
        timeout=15.0
    )
    assert res == "ok"
    assert captured["timeout"] == 15.0

def test_ai_retry_logic(monkeypatch):
    import time
    called = []
    
    virtual_time = 1700000000.0
    def mock_time():
        nonlocal virtual_time
        return virtual_time
    def mock_sleep(seconds):
        nonlocal virtual_time
        virtual_time += seconds
        
    monkeypatch.setattr(time, "time", mock_time)
    monkeypatch.setattr(time, "sleep", mock_sleep)
    
    def mock_urlopen(req, timeout=None):
        called.append(len(called))
        if len(called) < 3:
            raise urllib.error.URLError("Connection reset")
        resp_payload = {
            "choices": [{"message": {"content": "Success on 3rd attempt"}}]
        }
        return MockResponse(json.dumps(resp_payload).encode("utf-8"))
        
    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    
    res = generate_ai_summary(
        ["pytest tests/"],
        "test-key",
        "https://api.groq.com/openai/v1",
        "llama3",
        "groq"
    )
    assert len(called) == 3
    assert res == "Success on 3rd attempt"


def test_ai_escaping(monkeypatch):
    def mock_urlopen(req, timeout=None):
        resp_payload = {
            "choices": [{"message": {"content": "[red] tag\n├─ 🔨 Built: feature"}}]
        }
        return MockResponse(json.dumps(resp_payload).encode("utf-8"))
    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    
    from termstory.ai import generate_ai_summary
    res = generate_ai_summary(
        ["pytest tests/"],
        "test-key",
        "https://api.groq.com/openai/v1",
        "llama3",
        "groq"
    )
    assert r"\[red]" in res


def test_generate_executive_review_alias(monkeypatch):
    called = []
    def mock_urlopen(req, timeout=None):
        called.append(req)
        resp_payload = {
            "choices": [{"message": {"content": "Executive review output."}}]
        }
        return MockResponse(json.dumps(resp_payload).encode("utf-8"))
    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    
    from termstory.ai import generate_executive_review
    res = generate_executive_review(
        stats_summary="Stats",
        api_key="test-key",
        api_base_url="https://api.openai.com/v1",
        model_name="gpt-4o",
        provider="openai"
    )
    assert len(called) == 1
    assert res == "Executive review output."





