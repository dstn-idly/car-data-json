#!/bin/bash
set -euo pipefail

# Project directory
PROJECT_DIR="$HOME/car-data-json/car-data-json"

# Ensure no leftover git lock file
rm -f "$PROJECT_DIR/.git/index.lock"

# Navigate to project directory
cd "$PROJECT_DIR"

# Reset any local changes that might block pulls
git reset --hard HEAD
git pull --rebase origin main || true

# Activate virtual environment
source venv/bin/activate

# Run the scraping script
python3 test.py

# Stage ALL JSON files inside src/
git add src/*.json

# Only commit if something actually changed
if ! git diff --cached --quiet; then
    git commit -m "Update JSON data on $(date '+%Y-%m-%d %H:%M:%S')"
    git push origin main
    echo "✅ JSON changes committed & pushed."
else
    echo "⚡ No changes to commit."
fi