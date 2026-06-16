# TermStory Batch Dispatch — What Works

## THE WORKING PATTERN

**Prerequisites:**
1. User has tmux session "0" running in their interactive terminal
2. `~/.gemini/antigravity-cli/settings.json` has permissions configured (see below)
3. Prompt file exists at `.batch-<N>-prompt.txt` in repo root

**Pre-config settings (already set):**
```json
{
  "enableTelemetry": false,
  "model": "GPT-OSS 120B (Medium)",
  "permissions": {
    "allow": [
      "command(git)", "command(python3)", "command(pytest)",
      "write_file", "edit_file"
    ]
  }
}
```

**Command to dispatch from Hermes (works):**
```bash
/Users/himanshuverma/.hermes/profiles/agy-work/skills/antigravity-cli/scripts/run-batch.sh <batch-number> <timeout>
```

Wrapper script (already installed):
```bash
#!/bin/bash
set -e
BATCH="${1:-1}"
TIMEOUT="${2:-1800s}"
AGY="/Users/himanshuverma/.local/bin/agy"
WORKDIR="/Users/himanshuverma/personal/termstory"
PROMPT_FILE="$WORKDIR/.batch-${BATCH}-prompt.txt"
OUTPUT_FILE="$WORKDIR/.batch-${BATCH}-output.txt"
MARKER="/tmp/agy-batch-${BATCH}-done"

[ -f "$PROMPT_FILE" ] || { echo "ERROR: Prompt file not found: $PROMPT_FILE"; exit 1; }

PROMPT_CONTENT="$(cat "$PROMPT_FILE")"
rm -f "$MARKER"

tmux send-keys -t 0 C-c
sleep 2
tmux send-keys -t 0 "cd $WORKDIR" Enter
sleep 1
tmux send-keys -t 0 "$AGY --print \"$PROMPT_CONTENT\" --dangerously-skip-permissions --print-timeout $TIMEOUT > $OUTPUT_FILE 2>&1; touch $MARKER" Enter

echo "Batch $BATCH dispatched to tmux session 0"

while [ ! -f "$MARKER" ]; do sleep 15; done

echo "BATCH $BATCH COMPLETE"
tail -50 "$OUTPUT_FILE" 2>/dev/null || true
```

## PROVEN RESULTS (from this session)

| Test | Prompt Size | Result |
|------|-------------|--------|
| `say hi in 3 words` | 1 line | "Hey there friend" |
| Multi-line batch-test (branch/commit) | 15 lines | Created `feat/batch-test`, wrote `release.yml`, committed |

## PROMPT FILE FORMAT

Must be at `.batch-<batch_number>-prompt.txt` in repo root.

Working example (15 lines that was committed):
```
EXECUTE ONLY. NO subagents.

You are at /Users/himanshuverma/personal/termstory.

TASKS:
1. PyPI release workflow
   - Create .github/workflows/release.yml (tag->PyPI, SHA-pinned)
   - Run: python3 -m build --check

WORKFLOW:
1. git checkout main && git pull && git checkout -b feat/batch-test
2. Do tasks. Tests after each.
3. git add -A && git commit -m "feat: test"
4. git push origin feat/batch-test
5. gh pr create --fill

RULES:
- NO subagents
- NO multi_replace on large files
```

## KNOWN LIMITATIONS

- Hermes terminal CANNOT run agy directly (no Keychain for OAuth)
- tmux session MUST be pre-existing from user's login shell
- Multi-line prompts >15 lines may need to be tested first
- If tmux session is filled with stale text, Ctrl+C 3x + clear before running
- gh auth via keyring works in tmux session 0 (`gh auth status` shows logged in)

## NEXT STEPS FOR BATCH 4+ BATCHES

1. Write `.batch-<N>-prompt.txt` in repo root
2. Run: `run-batch.sh <N> 1800s`
3. agy will execute tasks, create branch, commit, push, create PR
4. Greptile review (auto-triggered) — merge if score >= 4
