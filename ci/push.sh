#!/usr/bin/env bash
# Usage: ci/push.sh <dir> <branch> <message>
# Commit everything in <dir> and push it to <branch>; does nothing if there is no change.
set -euo pipefail
dir="$1"; branch="$2"; msg="$3"
cd "$dir"
git add -A
if git diff --cached --quiet; then
  echo "nothing new for $branch"
  exit 0
fi
git commit -q -m "$msg"
for attempt in 1 2 3; do
  if git push -q origin "HEAD:$branch"; then
    echo "pushed to $branch"
    exit 0
  fi
  sleep 5
  git pull -q --rebase origin "$branch" || true
done
echo "could not push to $branch" >&2
exit 1
