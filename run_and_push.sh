#!/bin/bash
set -euo pipefail

# Go to the repo root
cd ~/car-data-json

# Ensure no leftover git lock
rm -f .git/index.lock

# Pull latest
git reset --hard HEAD
git pull --rebase origin main || true

# Run your scraper
python3 test.py

# Stage ALL JSON files inside src/
git add src/*.json

# Commit only if there are staged changes
if ! git diff --cached --quiet; then
    git commit -m "Update JSON data on $(date '+%Y-%m-%d %H:%M:%S')"
    git push origin main
    echo "✅ JSON changes committed & pushed."
else
    echo "⚡ No changes to commit."
fi
