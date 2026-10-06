"""Write text/<ID>.txt: the OCR text of every file, one text file per catalogue file, plus text/index.csv.

<ID> is the site's short ID: decimal number, file number, and -N for part N of a split PDF (e.g. 894A.00-1181,
894.00-1645-2). Each file starts with a short citation header; pages follow as "=== Page N ===" blocks, as OCR'd.
Also writes downloads/japan-internal-affairs-ocr-text-files.zip (all of text/) for the GitHub release.
Run after build_db.py:  python3 make_text_files.py
"""
import os, re, csv, json, zipfile

WEB = os.path.dirname(os.path.abspath(__file__))
FILES = os.path.dirname(WEB)
MERGED = os.path.join(FILES, "merged")
OUT = os.path.join(WEB, "text")
src = open(os.path.join(WEB, "data.js"), encoding="utf-8").read()
D = json.loads(src[src.index("["):src.index(";\nwindow.CATALOG_EXPECTED")])


def sid(x):
    m = re.search(r"-part-(\d+)$", x["id"])
    return f"{x['dec']}-{x['n']}" + (f"-{m.group(1)}" if m else "")


def ocr_path(x):
    for p in [os.path.join(FILES, "ocr", x["f"][:-4] + ".txt")] + [os.path.join(MERGED, d, x["f"][:-4] + ".txt") for d in ("ocr", "ocr-batch3", "ocr-batch4")]:
        if os.path.exists(p): return p
    return None


def ia_link(x):
    item = x.get("ia") or x["id"]
    url = f"https://archive.org/details/{item}"
    if x.get("iaf") and re.search(r"-part-\d+$", x["id"]):
        from urllib.parse import quote
        url += "/" + quote(re.sub(r"\.pdf$", "", x["iaf"], flags=re.I))
    return url


os.makedirs(OUT, exist_ok=True)
rows, missing, keep = [], [], set()
for x in sorted(D, key=lambda x: (x["dec"], x["n"], x["id"])):
    p = ocr_path(x)
    if not p: missing.append(x["id"]); continue
    name = sid(x) + ".txt"; keep.add(name)
    engine = "PaddleOCR" if p.startswith(MERGED) else "Mistral OCR"
    head = (f"{x['t']}\nID: {sid(x)}   Pages: {x['pg']}   OCR: {engine} (machine OCR, uncorrected)\n"
            f"Page images: {ia_link(x)}\n"
            f"Records of the Department of State Relating to Internal Affairs of Japan, 1930-1949 (decimal file 894, RG 59). Public domain.\n\n")
    body = open(p, encoding="utf-8").read()
    dst = os.path.join(OUT, name)
    new = head + body
    if not os.path.exists(dst) or open(dst, encoding="utf-8").read() != new:
        open(dst, "w", encoding="utf-8").write(new)
    rows.append([sid(x), x["id"], x["t"], x["dec"], x["n"], x["pg"], engine, "no" if x.get("sk") else "yes", ia_link(x), name])
for f in os.listdir(OUT):  # drop files for IDs that no longer exist
    if f.endswith(".txt") and f not in keep: os.remove(os.path.join(OUT, f))
with open(os.path.join(OUT, "index.csv"), "w", encoding="utf-8", newline="") as fh:
    w = csv.writer(fh); w.writerow(["id", "site_id", "title", "decimal", "file_no", "pages", "ocr", "catalogued", "page_images", "text_file"]); w.writerows(rows)
zp = os.path.join(WEB, "downloads", "japan-internal-affairs-ocr-text-files.zip")
with zipfile.ZipFile(zp + ".tmp", "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
    for r in rows: z.write(os.path.join(OUT, r[-1]), "japan-internal-affairs-text/" + r[-1])
    z.write(os.path.join(OUT, "index.csv"), "japan-internal-affairs-text/index.csv")
os.replace(zp + ".tmp", zp)
print(f"{len(rows)} text files in text/, index.csv, {os.path.getsize(zp) / 1e6:.0f} MB zip; missing OCR: {len(missing)}")
