rm -f /home/dstnxtwo/car-data-json/car-data-json/.git/index.lock
#!/bin/bash

cd ~/car-data-json/car-data-json/

git pull origin main
python3 test.py
git add *.json
git commit -m "Update JSON data on $(date)"
git push origin main
