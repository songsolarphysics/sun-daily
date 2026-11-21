#!/usr/bin/env bash
# Usage: ci/branch.sh <branch> <dir>
# Check out a data branch (gh-pages, ci-logs) into <dir>; start it empty if it does not exist.
set -euo pipefail
branch="$1"; dir="$2"
url="https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"
if git ls-remote --exit-code --heads "$url" "$branch" > /dev/null 2>&1; then
  git clone --quiet --depth 1 --branch "$branch" "$url" "$dir"
else
  mkdir -p "$dir"
  git -C "$dir" init -q -b "$branch"
  git -C "$dir" remote add origin "$url"
fi
git -C "$dir" config user.name "sun-daily"
git -C "$dir" config user.email "sun-daily@users.noreply.github.com"
