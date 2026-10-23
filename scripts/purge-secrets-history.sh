#!/usr/bin/env bash
# Purges leaked secret-bearing files from ALL git history.
#
# WARNING:
#   - Rewrites every commit hash after the first affected commit.
#   - Requires force-push and coordination with everyone on the team.
#   - Rotate ALL credentials that were in those files regardless
#     (Atlassian API token, Jira OAuth client secret + tokens,
#      ChatGPT/Codex refresh JWTs) — purging history does not
#      un-leak anything already cloned.
#
# Usage:  bash scripts/purge-secrets-history.sh

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

SECRET_PATHS=(
  "agent_factory.db"
  "agent_factory.db.bhanu-merge-backup"
  ".schema agent_run_events"
)

echo "==> Creating safety backup bundle at ../agentic-pipeline-pre-purge.bundle"
git bundle create ../agentic-pipeline-pre-purge.bundle --all

if ! command -v git-filter-repo >/dev/null 2>&1; then
  echo "==> Installing git-filter-repo"
  pip install git-filter-repo
fi

if [ -n "$(git status --porcelain)" ]; then
  echo "!! Working tree has uncommitted changes."
  echo "!! Commit or stash them first, then re-run this script." >&2
  exit 1
fi

echo "==> Verifying target paths exist in history"
for p in "${SECRET_PATHS[@]}"; do
  if git log --all --oneline -- "$p" | head -1 | grep -q .; then
    echo "    found in history: $p"
  fi
done

ARGS=()
for p in "${SECRET_PATHS[@]}"; do
  ARGS+=(--path "$p" --invert-paths)
done

echo "==> Rewriting history (this may take a moment)"
git filter-repo --force "${ARGS[@]}"

echo "==> Re-adding origin remote (filter-repo removes it)"
if [ -f .git/filter-repo/already_ran ]; then
  echo "    Inspect 'git remote -v'. Re-add origin manually if needed:"
  echo "    git remote add origin <your-origin-url>"
fi

echo ""
echo "Done. Next steps:"
echo "  1. git remote add origin <url>   # if missing"
echo "  2. git push --force --all && git push --force --tags"
echo "  3. Team: delete local clones, re-clone fresh"
echo "  4. ROTATE every credential that lived in those files:"
echo "     - Atlassian API token + OAuth client secret"
echo "     - Jira OAuth access/refresh tokens (re-connect in UI)"
echo "     - ChatGPT/Codex auth (codex login again)"
echo "     - Any GitHub PATs / git tokens stored via the app"
