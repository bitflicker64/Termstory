## 10. Privacy Sanitizer

All data passes through `sanitizer.py` **locally** before any AI call. Nothing sensitive ever leaves your machine.

### Session Blacklist

If any command in a session matches these patterns, the entire COMMANDS block for that session is replaced with `"[REDACTED: Security/Authentication Operations]"` in the LLM context — no raw commands leave the machine. Session metadata (date, project name, cached AI summary) remains visible.

```python
BLACKLIST_PATTERNS = [
    r'\bvault\b',
    r'\baws\s+configure\b',
    r'\bgh\s+auth\b',
    r'\bkubectl\s+.*?\bcreate\s+secret\b',
    # Raw token strings (these trigger if they appear literally in a command)
    r'\bgithub_pat_[a-zA-Z0-9_]+\b',
    r'\bsk_live_[a-zA-Z0-9_]+\b',
    r'\bnpm_[a-zA-Z0-9]{36}\b',
    r'\bsk-(?:proj-|ant-api03-)?[a-zA-Z0-9]{20,}\b',
]
```

### Redaction Rules

| Type | Pattern | Replacement |
|---|---|---|
| Private keys | `-----BEGIN ... PRIVATE KEY-----` | `[REDACTED_PRIVATE_KEY]` |
| AWS access keys | `AKIA[A-Z0-9]{16}`, `ASIA[A-Z0-9]{16}` | `[REDACTED_AWS_KEY]` |
| Slack tokens | `xoxb-<digits>-<chars>` | `[REDACTED_SLACK_TOKEN]` |
| Bearer tokens | `bearer <token>` | `Bearer [REDACTED_TOKEN]` |
| OpenAI keys | `sk-[a-zA-Z0-9_-]{32,}` | `[REDACTED_OPENAI_KEY]` |
| Anthropic keys | `sk-ant-[a-zA-Z0-9_-]{40,}` | `[REDACTED_ANTHROPIC_KEY]` |
| Google API keys | `AIzaSy[a-zA-Z0-9_-]{30,45}` | `[REDACTED_GOOGLE_KEY]` |
| DeepSeek keys | `sk-[a-zA-Z0-9_-]{32}` | `[REDACTED_DEEPSEEK_KEY]` |
| Named API key flags | `OPENAI_API_KEY=value`, `--anthropic-api-key=value`, etc. | `KEY=[REDACTED]` |
| Flag values | `--password`, `--token`, `--api-key`, `-p` (mysql/mongo) | `--flag=[REDACTED]` |
| Env var exports | `export SECRET_NAME=value` | `export SECRET_NAME=[REDACTED]` |
| Inline env vars | `ANY_KEY_LIKE_NAME=value` | `KEY=[REDACTED]` |
| IPv4/IPv6 | standard address patterns | `[REDACTED_IP]` |
| Hostnames/FQDNs | `host.domain.tld` (file extensions excluded) | `[REDACTED_HOST]` |
| URL hosts | `https://hostname` | `https://[REDACTED_HOST]` |
| SSH targets | `user@host` | `user@[REDACTED_HOST]` |
| High-entropy strings | ≥ 24 base64-like chars, Shannon entropy > 4.3 | `[REDACTED_ENTROPY]` |
| Custom user rules | patterns from `~/.termstoryignore` | `[REDACTED_CUSTOM]` |

**File extension whitelist:** Paths ending in `.py`, `.json`, `.sh`, `.yml`, `.ts`, `.go`, etc. are never redacted even if they look like FQDNs — preserving filenames like `config.json` and `api.ts`.

**Commit messages are subject to the same rules.** `redact_command()` operates on any string, not only shell commands. Git commit messages pass through the same regex pipeline before being embedded in any LLM prompt.

### Custom Rules (`.termstoryignore`)

Place additional regex patterns (one per line) in `~/.termstoryignore` or `~/.termstory/.termstoryignore`. Comment lines start with `#`. Patterns match case-insensitively; matched text is replaced with `[REDACTED_CUSTOM]`.

```ini
# Company-internal token format
acme_tok_[a-z0-9]{32}
```

> **Note on live-reload:** Patterns are loaded once at process startup via `load_custom_ignore_rules()`. Restart TermStory after editing this file for changes to take effect.

#### Known Sanitizer Limitations

The redaction engine is defense-in-depth, not a guarantee:

* **Entropy heuristic has a floor:** `redact_high_entropy()` only catches strings ≥ 24 characters with Shannon entropy > 4.3 bits/char. A short or low-entropy secret with no recognized prefix (e.g., a predictable 8-character password) will not be caught.
* **Blacklist is structural, not semantic:** If a tool invocation is obfuscated or aliased in a way that doesn't match the blacklist regex, it won't be blocked.
* **Regex on commit text is best-effort:** `redact_command()` was built for shell command syntax. A secret embedded in a natural-language commit message with no surrounding structural cue (no `=`, no `--flag`, no known prefix) may pass through undetected.

For maximally sensitive workflows, prefix commands containing plaintext secrets with a space (`HISTCONTROL=ignorespace`) — the shell will not write them to history, and TermStory will never ingest them.

---
