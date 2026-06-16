# TermStory Batch Dispatch — What Works

## THE PATTERN (30+ runs, proven across 5 batches)

```
1. Define single-line batch prompt
2. run-batch.sh N 1800s          (agy does code + tests + commit)
3. git push origin feat/batch-N   (Hermes, 5s)
4. gh pr create --fill            (Hermes, 2s)
5. greptile-watch N               (zero tokens, polls + reports)
6. If score >= 4: gh pr merge N --squash --delete-branch
```

## What agy CAN do (via tmux session 0):
- Write code across multiple files (+400 lines per batch)
- Run tests (all pass)
- Commit changes
- Use subagents (with GH token in prompt)
- Read project files correctly (Gemini + cleared brain + --add-dir)

## What agy CANNOT do:
- Push to remote (always times out)
- Create PR (always times out)
- Wait for Greptile + merge (always times out)
- Run directly in Hermes terminal (no Keychain for OAuth)
- Handle multi-line prompts (shell dquote> bug)

## Proof (5 batches, 12+ PRs):
| Batch | What | Files | Lines | Tests | Greptile |
|-------|------|-------|-------|-------|----------|
| 4a | Profile command | 4 | +161 | 26/26 | 3/5 |
| 4b | Greptile refactor | 1 | +12 | 8/8 | 4/5 |
| 4c | Release verify | 0 | empty | - | 3/5 |
| 4d | Subagents test | 0 | empty | - | 5/5 |
| 5 | Anger + Fortunes | 4 | +476 | 22/22 | 3/5 (stale) |

## Greptile gate (STRICT):
- Score >= 4: `gh pr merge N --squash --delete-branch`
- Score < 4: NEVER merge. Fix issues, push, re-trigger.
- Use `greptile-watch N` to poll (zero tokens, bash+gh only)
- If Greptile reviews stale commit: force-push amended commit

## Zero-token tools:
| Tool | What | Where |
|------|------|-------|
| `run-batch.sh N 1800s` | Dispatch agy to tmux | agy-orchestration skill |
| `batch-finish.sh` | Push + PR + Greptile | repo root |
| `greptile-watch N` | Poll + report, NO merge | ~/.local/bin/ |
| `gh pr merge N` | Manual merge (score >= 4) | gh CLI |

## Required setup:
```bash
# ~/.gemini/antigravity-cli/settings.json:
{"model":"Gemini 3.5 Flash (High)","permissionMode":"always-proceed","permissions":{"allow":["command(git)","command(python3)","command(pytest)","write_file","edit_file","read_file"]},"allowNonWorkspaceAccess":true}

# Clear stale brain:
rm -rf ~/.gemini/antigravity-cli/brain/* ~/.gemini/antigravity-cli/history.jsonl
```

## Prompt format (single line only):
```
EXECUTE ONLY. NO chat. cd /Users/himanshuverma/personal/termstory. git checkout main && git pull. git checkout -b feat/batch-N. [TASK]. Run pytest tests/ -v. git add -A && git commit -m "feat: ...". Print result.
```

## GH token for agy (optional, enables push/PR from agy):
Include in prompt: `echo "ghp_YOUR_TOKEN" | gh auth login --with-token`.
