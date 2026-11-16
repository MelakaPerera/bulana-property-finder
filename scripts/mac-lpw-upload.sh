#!/bin/bash
# Runs on the Mac each morning: scrapes LankaPropertyWeb (which blocks
# GitHub's servers), uploads the listings, and asks GitHub to rebuild the site.
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"
echo "=== $(date '+%Y-%m-%d %H:%M') LankaPropertyWeb upload ==="

git pull -q --ff-only || echo "Could not update the code; using the current copy."
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt

SLUG="$(gh repo view --json nameWithOwner --jq .nameWithOwner)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

.venv/bin/python -m scraper --lpw-export "$WORK/lpw.json"

cd "$WORK"
git init -q -b lpw-upload
git config user.name "$(git -C "$REPO_DIR" config user.name)"
git config user.email "$(git -C "$REPO_DIR" config user.email)"
git add lpw.json
git commit -q -m "LankaPropertyWeb listings $(date +%F)"
git push -q -f "https://github.com/$SLUG.git" lpw-upload

cd "$REPO_DIR"
gh workflow run daily.yml -f only=lpw
echo "Uploaded and asked GitHub to rebuild the site."
