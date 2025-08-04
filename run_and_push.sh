#!/bin/bash

# Remove any lingering lock file just in case
rm -f /home/dstnxtwo/car-data-json/car-data-json/.git/index.lock

# Navigate to the project directory
cd ~/car-data-json/car-data-json

# Pull latest changes from GitHub
git pull origin main

# Activate virtual environment
source venv/bin/activate

# Run the scraping script
python3 test.py

# Add and commit changes
git add *.json
git commit -m "Update JSON data on $(date)"
git push origin main

