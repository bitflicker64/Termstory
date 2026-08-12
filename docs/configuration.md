## 16. Configuration

Config lives at `~/.termstory/config.json`:

```json
{
    "ai_enabled": true,
    "active_provider": "groq",
    "request_timeout_seconds": 30,
    "max_query_log": 10000,
    "has_seen_onboarding": true,
    "providers": {
        "groq": {
            "api_key": "gsk_...",
            "api_base_url": "https://api.groq.com/openai/v1",
            "model_name": "llama-3.1-8b-instant"
        },
        "openai": {
            "api_key": "sk-proj-...",
            "api_base_url": "https://api.openai.com/v1",
            "model_name": "gpt-4o-mini"
        },
        "ollama": {
            "api_key": "",
            "api_base_url": "http://localhost:11434/v1",
            "model_name": "llama3"
        },
        "custom": {
            "api_key": "",
            "api_base_url": "http://localhost:8080/v1",
            "model_name": "my-model"
        }
    }
}
```

All settings are editable via `termstory config set <dot.path> <value>`.

Key configuration parameters:
- `ai_enabled` (bool): Toggle AI summaries on/off.
- `active_provider` (string): Set to `"groq"`, `"openai"`, `"ollama"`, `"custom"`, or `"disabled"`.
- `request_timeout_seconds` (int): HTTP request timeout (in seconds) for LLM API calls. Defaults to `30`.
- `max_query_log` (int): Maximum number of captured database query profiler entries before older entries are trimmed. Defaults to `10000`.
- `providers.<name>.<param>`: Provider-specific endpoints, API keys, and model names.
- `project_root_cache_ttl` (int, seconds): How long resolved project-root results are cached before being re-checked against the filesystem. A shorter value detects newly created/removed project markers (`.git`, `package.json`, ...) sooner but at the cost of more frequent filesystem walks. Defaults to `60`.

---
