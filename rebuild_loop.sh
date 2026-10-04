#!/bin/zsh
# Rebuild catalog.sqlite + data.js every 30 minutes while the merged-catalogue workers run.
cd "/Volumes/Oma/Archives/US Government Documents/US Docs Related to Japan/Files/japan-internal-affairs"
while ps -Ao command | grep -q '[c]atalog_merged.py --backend'; do
  python3 build_db.py >> rebuild.log 2>&1; sleep 1800
done
python3 build_db.py >> rebuild.log 2>&1
