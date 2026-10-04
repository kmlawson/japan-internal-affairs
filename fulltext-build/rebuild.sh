#!/bin/zsh
# Rebuild the full-text search index and the bulk downloads from the current OCR.
set -e
# Heavy job: run at background priority so the Mac stays usable (Pagefind is a separate Rust process, so node memory flags do not cap it).
cd "$(dirname "$0")"
python3 prepare.py          # records.jsonl, one record per OCR page (ocr/ + merged/ocr, ocr-batch3, ocr-batch4)
taskpolicy -b nice -n 19 node build.mjs              # ../pagefind/
python3 make_bundle.py      # ../downloads/japan-internal-affairs-ocr-text.jsonl.gz
cp ../catalog.sqlite ../downloads/japan-internal-affairs-catalog.sqlite
