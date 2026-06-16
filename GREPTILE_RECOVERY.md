# Greptile Recovery — When balance comes back, do this:

1. Open `~/.local/bin/agy-cycle`
2. Find `# PHASE 3: agy reviews its own code with subagents (replaces Greptile)`
3. Replace the entire Phase 3 section (from `while [ "$RETRY" -lt ...]` to `exit 1`) with the Greptile version from `greptile-watch`:

The pattern to restore:
- `gh pr comment "$PR_NUM" --body "@greptileai review"`
- Poll every 30s for score in comment body
- `SCORE=$(echo "$COMMENT" | grep -oE '[0-9]/5' | cut -d/ -f1)`
- If >= 4: merge
- If < 4: extract issues, feed to agy via fix prompt, re-push, retry

See `~/.local/bin/greptile-watch` for the exact poll/issue-extraction code.
