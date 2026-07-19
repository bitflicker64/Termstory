# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.6.x   | ✅        |
| < 0.6   | ❌        |

## Reporting a Vulnerability

If you discover a security issue in TermStory, **do not open a public GitHub issue** — this gives potential attackers advance notice before a fix is available.

Instead, use GitHub's private security advisory system:

1. Navigate to the repository **Security** tab.
2. Click **"Report a vulnerability"**.
3. Describe the issue, steps to reproduce, and potential impact.

Expected response time: initial acknowledgement within **48 hours**, fix within **14 days** for confirmed vulnerabilities.

---

## Trust Model and Known Limitations

TermStory's privacy guarantee is: **no raw data leaves your machine without passing through `termstory/sanitizer.py` first.** Understanding the limits of that guarantee is important.

### What the sanitizer does well
- **Named-prefix secrets** (AWS `AKIA*`, OpenAI `sk-*`, Anthropic `sk-ant-*`, Slack `xoxb-*`, Google `AIzaSy*`, etc.) are caught by specific regex patterns.
- **Structural patterns** (`--password=`, `export KEY=***`) are redacted regardless of the specific value.
- **Blacklisted workflows** (`vault`, `aws configure`, `gh auth`, raw token strings, `kubectl create secret`) gate the entire COMMANDS block — none of those raw command strings reach the LLM.

### Known gaps (by design — documented here, not hidden)

| Limitation | Detail |
|------------|--------|
| **Short / low-entropy secrets** | `redact_high_entropy()` requires ≥ 24 characters and Shannon entropy > 4.3 bits/char. A short or predictable secret with no recognizable prefix (e.g., a 12-character password) will not be flagged. |
| **Novel token formats** | Blacklist and named-pattern detection is structural. A company-internal token format with no known prefix silently passes through unless you add it to `~/.termstoryignore`. |
| **Commit message prose** | `redact_command()` was designed for shell syntax. A secret embedded in natural-language commit text with no `=`, flag, or prefix cue may not be detected. |
| **`.termstoryignore` not live-reloaded** | Custom patterns load once at process startup. Edits require a TermStory restart to take effect. |
| **Model refusal is not a control** | TermStory adds an instruction in `termstory ask` prompts asking the LLM never to output credentials verbatim. This is a secondary layer; the redaction pass is the actual control. |

### Recommended mitigations
- **Use `HISTCONTROL=ignorespace`** in your shell and prefix any command containing a plaintext secret with a space — the shell will refuse to write it to history, and TermStory will never see it.
- **Use Ollama** for fully local inference if you work with highly sensitive codebases.
- **Maintain `~/.termstoryignore`** with patterns for any proprietary token formats your workflows produce.
