#!/bin/zsh
set -eu

cd /Users/leelark/openclaw-status
python3 generate_status.py

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Not a git repo; status.json refreshed locally only."
  exit 0
fi

if ! git remote get-url origin >/dev/null 2>&1; then
  echo "No git remote configured; status.json refreshed locally only."
  exit 0
fi

if git diff --quiet -- status.json; then
  echo "No status change."
  exit 0
fi

git add status.json
git commit -m "Update OpenClaw status" >/dev/null 2>&1 || true
git push origin HEAD >/dev/null 2>&1
