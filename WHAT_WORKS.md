# TermStory Batch Dispatch — What Works

## THE PATTERN (proven across 30+ test runs)

```
Hermes writes prompt → dispatch to tmux → agy codes + tests + commits → Hermes pushes + PRs → Greptile reviews → Hermes merges
```

| Step | Who | Works? |
|------|-----|--------|
| Write prompt file | Hermes | ✅ |
| agy code changes | tmux session 0 | ✅ |
| agy runs tests | tmux session 0 | ✅ |
| agy commits | tmux session 0 | ✅ (times out on complex tasks) |
| git push | Hermes terminal | ✅ |
| gh pr create | Hermes terminal | ✅ |
| Greptile review | Auto (webhook) | ✅ |
| gh pr merge | Hermes terminal | ✅ |

## agy WORKS when:

1. **Model: Gemini 3.5 Flash (High)** — fast, no permission spam
2. **Brain cleared** — `rm -rf ~/.gemini/antigravity-cli/brain/* ~/.gemini/antigravity-cli/history.jsonl`
3. **Flag: `--add-dir "/path"`** — forces correct workspace
4. **Flag: `--print-timeout 1800s`** — 30 min
5. **Settings: `permissionMode: "always-proceed"`** — no popups
6. **Single-line prompts** — no newlines (shell `dquote>` bug)
7. **Prompt starts with `cd /path`** — explicit working dir
8. **Subagents work** with GH token in prompt
9. **GH token in prompt**: `echo "ghp_xxx" | gh auth login --with-token`
10. **Clean tmux**: C-c + clear before each dispatch

## agy DOESN'T WORK when:

- Model is GPT-OSS 120B (too slow, permission spam)
- Brain has stale workspace data from old sessions
- Multi-line prompts with newlines (shell `dquote>` artifacts)
- Running from Hermes terminal directly (no Keychain for OAuth)
- Timeout < 600s for multi-file tasks
- Push/PR steps at end (always times out before those)
- `--dangerously-skip-permissions` alone (needs `permissionMode` too)

## Full Workflow (from scratch):

```bash
# 1. Clear brain (fixes stale workspace)
rm -rf ~/.gemini/antigravity-cli/brain/* ~/.gemini/antigravity-cli/history.jsonl

# 2. Ensure settings.json has correct model + permissions
cat > ~/.gemini/antigravity-cli/settings.json << 'END'
{
  "enableTelemetry": false,
  "model": "Gemini 3.5 Flash (High)",
  "permissionMode": "always-proceed",
  "permissions": { "allow": ["command(git)","command(python3)","command(pytest)","write_file","edit_file","read_file"] },
  "allowNonWorkspaceAccess": true
}
END

# 3. Clean tmux
tmux send-keys -t 0 C-c
sleep 2
tmux send-keys -t 0 'clear' Enter

# 4. Write prompt file in repo
# Single line only! No newlines.

# 5. Dispatch to tmux
run-batch.sh N 1800s

# 6. After agy commits, complete workflow:
cd ~/personal/termstory
git push origin feat/batch-N
gh pr create --base main --head feat/batch-N --fill
gh pr comment N --body "@greptileai review"
sleep 120
gh pr view N --json comments --jq 'last | .body' | grep -oE '[0-9]/5'
# if >= 4: gh pr merge N --squash --delete-branch
```

## Prompt format (proven single-line):

```
EXECUTE ONLY. NO subagents. cd /Users/himanshuverma/personal/termstory. git checkout main && git pull. git checkout -b feat/batch-N. [TASK]. Run pytest tests/ -v. git add -A && git commit -m "feat: ...". git push origin feat/batch-N. gh pr create --base main --head feat/batch-N --fill.
```

## With subagents + GH token (proven working):

```
EXECUTE ONLY. Use define_subagent for each task. cd /Users/himanshuverma/personal/termstory. git checkout main && git pull. git checkout -b feat/batch-N. echo "ghp_xxx" | gh auth login --with-token. [TASK]. Run tests. git add -A && git commit -m "feat: ...". git push. gh pr create --fill. Print result after each step.
```

## Git auth in Hermes terminal:

```bash
echo "YOUR_PAT" | gh auth login --with-token
```

## Greptile score check:

```bash
gh pr view N --json comments --jq '[.comments[] | select(.author.login=="greptile-apps")] | last | .body' | grep -oE '[0-9]/5'
```
