#!/bin/zsh
# waits for the batch-3a cataloguing worker, then rebuilds the catalogue database and data.js
cd "$(dirname "$0")"
while pgrep -f "catalog_merged.py.*catalog-batch3b" >/dev/null; do sleep 120; done
n=$(for f in json-merged/*/file.json; do echo $f; done | wc -l)
echo "$(date '+%F %T') worker ended; $n file.json present" >> catalog-batch3b.log
taskpolicy -b nice -n 19 python3 build_db.py >> catalog-batch3b.log 2>&1
/bin/cp -f catalog.sqlite downloads/japan-internal-affairs-catalog.sqlite
echo "$(date '+%F %T') build_db done" >> catalog-batch3b.log
