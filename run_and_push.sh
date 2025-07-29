rm -f /home/dstnxtwo/car-data-json/car-data-json/.git/index.lock
#!/bin/bash

<<<<<<< HEAD
cd ~/car-data-json/car-data-json/
=======
cd ~/car-data-json/car-data-json
>>>>>>> 4bdca43 (Update vehicle data and add script)

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
<<<<<<< HEAD
=======

>>>>>>> 4bdca43 (Update vehicle data and add script)
