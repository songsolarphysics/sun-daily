#!/usr/bin/env bash
# Usage: ci/save_logs.sh <label>
# Copy this run's log and status files to the ci-logs branch, with private values removed,
# so a failed night can be diagnosed without opening the Actions page.
set -euo pipefail
label="$1"
ci/branch.sh ci-logs _logs
dest="_logs/runs/$(date -u +%Y%m%d-%H%M)-${GITHUB_RUN_NUMBER}-${label}"
mkdir -p "$dest"
for f in out/run.log out/probe.log out/status.json out/probe.json out/card.json out/ticket.json out/poster.jpg; do
  if [ -f "$f" ]; then cp "$f" "$dest/"; fi
done
python - "$dest" <<'PY'
import os, pathlib, sys
email = os.environ.get("JSOC_EMAIL", "").strip()
for path in pathlib.Path(sys.argv[1]).glob("*"):
    if path.suffix not in (".log", ".json"):
        continue
    text = path.read_text(errors="replace")
    if email:
        text = text.replace(email, "<jsoc-email>").replace(email.replace("@", "%40"), "<jsoc-email>")
    path.write_text(text)
PY
rm -rf _logs/latest
cp -r "$dest" _logs/latest
ls -1d _logs/runs/* | sort | head -n -40 | xargs -r rm -rf
ci/push.sh _logs ci-logs "run ${GITHUB_RUN_NUMBER} ${label}"
