# TermStory Handoff

## Current State (2026-06-16)

### Working Batch Dispatch Pattern
See **`WHAT_WORKS.md`** for full details.

**Key facts:**
- Hermes terminal CANNOT run agy directly (no Keychain for OAuth)
- ONLY tmux session "0" works (has Keychain from user's login shell)
- Wrapper script `run-batch.sh` dispatches to tmux 0, blocks until done
- agy settings.json now has permissions pre-configured
- Prompt file format: `.batch-<N>-prompt.txt`

### Repository
- `/Users/himanshuverma/personal/termstory`
- `bitflicker64/Termstory` (public)
- Latest merged: `d89bf9c feat: Batch 3 v4 — FTS5 + stress + AI contexts`

### Proven test commits this session:
- `feat/batch-test` branch: `5d308d3 feat: add PyPI release workflow` (from multi-line test)

### Next: Batch 4 PR workflow
Branch `feat/batch-4-v4` exists with agy's partial work. Need to:
1. Diff branch, check what's there
2. Decide whether to keep agy's incomplete work or re-run
