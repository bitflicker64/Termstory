## 11. AI Client

`ai.py` interfaces with any OpenAI-compatible LLM endpoint using **only Python's standard library** — no `requests`, no `openai-python`.

- **Transport:** `urllib.request.Request` with JSON payload
- **URL normalization:** Strips trailing slashes, auto-appends `/chat/completions`
- **Keyless mode:** Skips `Authorization: Bearer` header if API key is empty (Ollama compatibility)
- **Timeout:** 15 seconds to prevent blocking the TUI thread
- **Background execution:** All AI calls run in Textual `@work` async workers — UI never freezes

### Supported Providers

| Provider | Default model | Notes |
|---|---|---|
| **Groq** | `llama-3.1-8b-instant` | Fast, free tier available |
| **OpenAI** | `gpt-4o-mini` | Requires API key |
| **NVIDIA** | `meta/llama-3.1-8b-instruct` | NVIDIA NIM (`integrate.api.nvidia.com`), requires API key |
| **Ollama** | `llama3` | Fully local, no key needed |
| **Custom** | any | Any OpenAI-compatible endpoint |

Example (NVIDIA):

```bash
termstory config set active_provider nvidia
termstory config set providers.nvidia.api_key <your_nvapi_key>
termstory config set ai_enabled true
```

### Pre-LLM Sanitization

Every LLM call is preceded by a local sanitization pass through `termstory/sanitizer.py`.
No raw session data reaches `_send_llm_request()` directly.

**Commands** go through `sanitize_session_commands()`:
1. If any command matches a blacklist pattern (`vault`, `aws configure`, `gh auth`, raw token strings, etc.), the function returns `(None, True)` — the calling code replaces the entire COMMANDS block with `"[REDACTED: Security/Authentication Operations]"`.
2. Otherwise, every command string is run through `redact_command()` (named-prefix patterns → entropy heuristic → custom user rules) and the sanitized list is embedded in the prompt.

**Git commit messages** pass through `redact_command()` individually before being appended to the prompt — the same regex pipeline used for commands, applied to commit text.

This is enforced across all three AI-facing surfaces: `generate_ai_summary()`, `generate_daily_chronicle_prompt()` (both in `ai.py`), and `generate_answer()` (in `ask.py`). See `docs/privacy.md` for the complete redaction rule table.

