#!/usr/bin/env bash
# Usage: ci/preview.sh
# For runs that are not published: put the clip, poster, card and ticket on the `preview`
# branch (one commit, force-pushed, so test clips never pile up in the history).
set -euo pipefail
if [ ! -f out/clip.mp4 ]; then
  echo "no clip to preview"
  exit 0
fi
rm -rf _preview
mkdir _preview
cp out/clip.mp4 out/poster.jpg out/card.json out/ticket.json _preview/
cd _preview
git init -q -b preview
git remote add origin "https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"
git config user.name "sun-daily"
git config user.email "sun-daily@users.noreply.github.com"
git add -A
git commit -q -m "preview of run ${GITHUB_RUN_NUMBER}"
git push -q -f origin preview
echo "preview branch updated"
