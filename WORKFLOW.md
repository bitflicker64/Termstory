# Workflow — What Hermes Does vs What agy Does

## THE RULE
**agy does everything. Hermes does nothing except write prompts and run agy-cycle.**

## Hermes (me) — only these actions:
1. Define the batch prompt (single-line prompt describing the feature to build)
2. Run: `agy-cycle N` (background, notify_on_complete=true)
3. Wait for ping — ZERO tokens until notification
4. On ping: check output, report to user

## agy-cycle (the script) — does everything else:
- Phase 1: agy writes code, runs tests, commits
- Phase 2: bash pushes branch, creates PR
- Phase 3: agy reviews code directly (no subagents — they hang), fixes issues, retries, merges

## NEVER do these (wastes tokens, user gets angry):
- Fix code manually
- Resolve merge conflicts manually
- Push or PR manually
- Merge PRs manually
- Write prompt for agy that could be automated
- Poll for anything (use background+notify)

## If agy-cycle Phase 3 finds issues or merge conflicts:
- Let agy fix them through agy-cycle retry loop
- Do NOT touch git or code yourself

## Current tools:
| Tool | Purpose |
|------|---------|
| agy-cycle N | Full batch: code → push → PR → review → fix → merge |
| run-batch.sh N | Basic dispatch to tmux (used internally by agy-cycle) |
| greptile-watch N | Poll PR for Greptile score (disabled while balance out) |
