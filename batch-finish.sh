#!/bin/bash
# batch-finish — Push, PR, Greptile, poll, merge in one command
# Usage: batch-finish <branch-name>
# Example: batch-finish feat/batch-5
# Requires: gh auth, git, jq
# Zero AI tokens — pure bash + gh + jq

set -euo pipefail

BRANCH="${1:-}"
REPO="bitflicker64/Termstory"
WORKDIR="/Users/himanshuverma/personal/termstory"

if [ -z "$BRANCH" ]; then
  echo "Usage: batch-finish <branch-name>"
  echo "Example: batch-finish feat/batch-5"
  exit 1
fi

cd "$WORKDIR"

echo "=== batch-finish: $BRANCH ==="

# 1. Push branch
echo "[1/6] Pushing branch..."
git push origin "$BRANCH" 2>&1

# 2. Create PR if not exists, otherwise update
EXISTING_PR=$(gh pr list --state open --head "$BRANCH" --json number --jq '.[0].number' 2>/dev/null || echo "")
if [ -n "$EXISTING_PR" ]; then
  PR_NUM="$EXISTING_PR"
  echo "[2/6] Using existing PR #$PR_NUM"
  PR_URL="https://github.com/$REPO/pull/$PR_NUM"
else
  echo "[2/6] Creating PR..."
  PR_URL=$(gh pr create --base main --head "$BRANCH" --fill 2>&1)
  PR_NUM=$(echo "$PR_URL" | grep -oE '[0-9]+$')
  echo "PR #$PR_NUM: $PR_URL"
fi

# 3. Trigger Greptile
echo "[3/6] Triggering Greptile review..."
gh pr comment "$PR_NUM" --body "@greptileai review"

# 4. Poll Greptile (up to 5 min)
echo "[4/6] Waiting for Greptile review..."
for i in $(seq 1 10); do
  sleep 30
  COMMENT=$(gh pr view "$PR_NUM" --repo "$REPO" --json comments \
    --jq '[.comments[] | select(.author.login=="greptile-apps")] | last | .body' 2>/dev/null || echo "")
  SCORE=$(echo "$COMMENT" | grep -oE '[0-9]/5' | head -1 | cut -d/ -f1 || echo "")
  if [ -n "$SCORE" ]; then
    echo "Got score: $SCORE/5"
    break
  fi
  echo "  Poll $i/10: still waiting..."
done

if [ -z "$SCORE" ]; then
  echo "[FAIL] Greptile didn't respond after 5 min. Check manually:"
  echo "  gh pr view $PR_NUM"
  exit 1
fi

# 5. Decision
echo "[5/6] Score: $SCORE/5"
if [ "$SCORE" -ge 4 ]; then
  echo "Score >= 4. MERGING..."
  gh pr merge "$PR_NUM" --squash --delete-branch
  echo "[6/6] MERGED ✅"
else
  echo "Score < 4. NOT merging. Check Greptile feedback:"
  echo "$COMMENT" | grep -A5 "Confidence"
  echo ""
  echo "Issues found. Fix them, push, and re-run batch-finish."
  echo ""
  echo "PR: $PR_URL"
  exit 1
fi
