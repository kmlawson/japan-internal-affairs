"""Build spotlight-data.js from spotlight/urls.txt.

Each archive.org page URL is resolved to the catalogue sub-file that contains the page
(catalog.sqlite), the raw OCR for the sub-file's pages is read from merged/ocr*/ (PaddleOCR) or ../ocr/ (Mistral OCR), and the
cleaned transcription (written by hand from the page images) is read from spotlight/clean/<file id>__<seq>.txt.
Run after build_db.py or after adding URLs / clean transcriptions.
"""
import os, re, json, sqlite3, urllib.parse

WEB = os.path.dirname(os.path.abspath(__file__))
MERGED = os.path.join(os.path.dirname(WEB), "merged")
db = sqlite3.connect(os.path.join(WEB, "catalog.sqlite"))
entries, order, cat = {}, [], "General"
for line in open(os.path.join(WEB, "spotlight", "urls.txt"), encoding="utf-8"):
    line = line.strip()
    if line.startswith("## "): cat = line[3:].strip(); continue  # "## Category" heading: following links belong to it
    if not line or line.startswith("#"): continue
    d = re.search(r"#d/([^/\s]+)/doc/(\d+)", line)  # a link to the site's document entry
    if d:
        row = db.execute("select id, file from files where id=?", (d.group(1),)).fetchone()
        sub = db.execute("select seq, start_page, end_page from subfiles where file_id=? and seq=?", (d.group(1), int(d.group(2)))).fetchone() if row else None
        page = sub[1] if sub else None
    else:  # an archive.org page URL
        m = re.match(r"https://archive\.org/details/([^/]+)/(?:([^/]+)/)?page/n(\d+)", line)
        item, name, n = m.group(1), urllib.parse.unquote(m.group(2) or ""), int(m.group(3))
        page = n + 1  # archive.org leaf n<k> is OCR page k+1
        row = db.execute("select id, file from files where ia_item=? and (? = '' or file=?)", (item, name, name + ".pdf")).fetchone()
        sub = db.execute("select seq, start_page, end_page from subfiles where file_id=? and start_page<=? and end_page>=? order by start_page desc limit 1",
                         (row[0], page, page)).fetchone() if row else None
    if not sub: print("NO MATCH", line); continue
    key = f"{row[0]}__{sub[0]}"
    if key not in entries:
        entries[key] = {"id": key, "file": row[0], "seq": sub[0], "p": [sub[1], sub[2]], "via": [], "cat": cat, "n": len(order)}; order.append(key)
    entries[key]["via"].append(page)
out = []
for key in order:
    e = entries[key]; pdf = db.execute("select file from files where id=?", (e["file"],)).fetchone()[0]
    tp = next(q for q in [os.path.join(MERGED, d, pdf[:-4] + ".txt") for d in ("ocr", "ocr-batch3", "ocr-batch4")]
              + [os.path.join(os.path.dirname(WEB), "ocr", pdf[:-4] + ".txt")] if os.path.exists(q))
    parts = re.split(r"^=== Page (\d+) ===.*$", open(tp, encoding="utf-8").read(), flags=re.M)
    raw = {int(parts[i]): parts[i + 1].strip("\n") for i in range(1, len(parts), 2)}
    e["raw"] = [[p, raw.get(p, "")] for p in range(e["p"][0], e["p"][1] + 1)]
    e["ocr"] = "PaddleOCR" if tp.startswith(MERGED) else "Mistral OCR"
    cp = os.path.join(WEB, "spotlight", "clean", key + ".txt")
    jp = os.path.join(WEB, "spotlight", "clean", key + ".ja.txt")  # optional reconstructed Japanese text
    e["ja"] = None
    if os.path.exists(jp):
        jparts = re.split(r"^=== Page (\d+) ===[ \t]*\n?", open(jp, encoding="utf-8").read(), flags=re.M)
        e["ja"] = [[int(jparts[i]), jparts[i + 1].strip("\n")] for i in range(1, len(jparts), 2)]
    e["clean"] = None
    if os.path.exists(cp):
        cparts = re.split(r"^=== Page (\d+) ===[ \t]*\n?", open(cp, encoding="utf-8").read(), flags=re.M)
        e["clean"] = [[int(cparts[i]), cparts[i + 1].strip("\n")] for i in range(1, len(cparts), 2)]
    out.append(e)
with open(os.path.join(WEB, "spotlight-data.js"), "w", encoding="utf-8") as fh:
    fh.write("window.SPOTLIGHT=" + json.dumps(out, ensure_ascii=False, separators=(",", ":")) + ";\n")
print(len(out), "spotlight entries;", sum(1 for e in out if e["clean"]), "with cleaned text")
