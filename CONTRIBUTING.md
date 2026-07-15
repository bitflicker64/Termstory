# Contributing to TermStory

Thank you for your interest in contributing to TermStory! Here is a brief guide to help you get started.

## Community Standards
Please read our [Code of Conduct](CODE_OF_CONDUCT.md) before contributing to help maintain a welcoming and respectful community.

## Dev Setup

1. Clone the repository:
   ```bash
   git clone https://github.com/your-username/termstory.git
   cd termstory
   ```
2. Install the package in editable mode with development dependencies:
   ```bash
   pip install -e ".[dev]"
   ```

## Running Tests

To run the test suite, simply use pytest:
```bash
python3 -m pytest tests/ -v
```

## Code Style

TermStory is designed as a **personal developer memory engine** that prioritizes recognition and clarity. Please adhere to the following code style philosophy:

*   **Density over decoration**: Avoid rounded panels, double borders, or nested boxes. Use clean column alignment, simple tables, and minimal spacing.
*   **Strictly banned**: The use of `rich.panel.Panel` is strictly banned in favor of dense text separators to maintain this philosophy.

## Security: Sanitization Rule for LLM-Facing Code

Any function that builds an LLM prompt using raw session data **must** sanitize before
calling `_send_llm_request()`. This is a hard requirement, not a suggestion — functions
that skip sanitization are a security bug regardless of how benign the context looks at
the time.

**The rule in two lines:**

```python
# For shell commands — always do this before embedding in a prompt:
sanitized_cmds, is_blacklisted = sanitize_session_commands(raw_cmd_strings)
if is_blacklisted:
    # Replace the entire commands block — do NOT iterate sanitized_cmds (it's None)
    block.append("  - [REDACTED: Security/Authentication Operations]")
else:
    for sc in sanitized_cmds:
        block.append(f"  - {sc}")

# For git commit messages — apply to every message string:
safe_msg = redact_command(raw_commit_message)
```

**Why the `is_blacklisted` check must come first:** `sanitize_session_commands` returns
`(None, True)` for blacklisted sessions. Iterating `None` raises `TypeError`. Always
branch on the flag before touching the list.

**Adding a new LLM-context function? Add a regression test.** The pattern is in
`tests/test_ask.py` (`test_generate_answer_redacts_secrets_in_commands`): mock
`urllib.request.urlopen`, capture `req.data.decode("utf-8")`, assert a known fake
secret (`AKIAIO...MPLE`) is not in the captured payload.

Existing reference implementations: `generate_ai_summary()` and `generate_rpg_bio()`
in `termstory/ai.py`.

## Submitting PRs

*   **Branch naming**: Use descriptive branch names like `feature/new-cli-command`, `bugfix/issue-123`, or `docs/update-readme`.
*   **Commit messages**: Use clear, concise commit messages. A good format is `[Scope] Short description`, for example, `[Parser] Fix bug in zsh history parsing`.

## Adding a New CLI Command

To add a new command to the TermStory CLI:
1.  Define the command logic and interface in `termstory/cli.py`.
2.  If the command requires new formatted output, add the rendering logic to `termstory/formatter.py`.
