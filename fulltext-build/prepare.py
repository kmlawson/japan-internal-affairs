"""Write records.jsonl: one record per OCR'd page, for build.mjs to index with Pagefind.

Sources: ../../ocr/ (Mistral OCR of the 1,476 PDFs in Files/) and ../../merged/ocr/ (Paddle OCR of
every merged PDF with OCR text in merged/ocr/, merged/ocr-batch3/ or merged/ocr-batch4/).
"""
import os, re, csv, json

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.dirname(HERE)
FILES = os.path.dirname(WEB)
CLASSES = {'00': 'Political affairs', '01': 'Government', '02': 'Executive', '03': 'Legislature', '04': 'Judiciary & law',
           '05': 'International courts', '1': 'Public order, health & regulation', '2': 'Military', '3': 'Naval',
           '4': 'Social & cultural', '5': 'Economic & financial', '6': 'Industry & agriculture',
           '7': 'Communications & transport', '8': 'Navigation & shipping', '9': 'Press & science'}


def subject(dec):  # same rules as cls() in index.html
    m = re.match(r"^89\d[A-Z]?\.(\d+)", dec)
    if not m: return "Other (outside 894)"
    n = m.group(1)
    if n[0] == "0": return CLASSES.get(n[:2], "Political affairs")
    return CLASSES.get(n[0], "Other")


def years(title):
    ys = [int(y) for y in re.findall(r"\b19[1-5]\d\b", title)]
    return [str(y) for y in range(min(ys), max(ys) + 1)] if ys else []


MONS = ['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october', 'november', 'december']


def start_date(title):  # first date in the title as YYYY-MM-DD; "9999-99-99" if none
    m = re.search(r"\b(January|February|March|April|May|June|July|August|September|October|November|December)(?:\s+(\d{1,2})(?:st|nd|rd|th)?)?,\s*(\d{4})\b", title)
    if m: return f"{m.group(3)}-{MONS.index(m.group(1).lower()) + 1:02d}-{int(m.group(2) or 1):02d}"
    y = re.search(r"\b(19[1-5]\d)\b", title)
    return f"{y.group(1)}-01-01" if y else "9999-99-99"


import sqlite3
_db = sqlite3.connect(os.path.join(WEB, "catalog.sqlite"))
CATDATE = {}
for fid, d in _db.execute("select id, date from files"):
    if d and re.match(r"^19[1-5]\d", d): CATDATE[fid] = (d + "-01-01")[:10] if len(d) == 4 else (d + "-01")[:10] if len(d) == 7 else d[:10]
for fid, d in _db.execute("select s.file_id, min(s.date) from subfiles s where s.date glob '19[1-5][0-9]*' group by s.file_id"):
    CATDATE.setdefault(fid, (d + "-01-01")[:10] if len(d) == 4 else (d + "-01")[:10] if len(d) == 7 else d[:10])


# ---- the catalogued document (sub-file) each page belongs to: its number, date and a coarse type ----
TYPES = [("Telegram / airgram", r"telegram|airgram|cable|radiogram"), ("Despatch", r"despatch|dispatch"),
         ("Instruction", r"instruction"), ("File administration", r"cross.?ref|file note|list of papers|docket|charge slip|routing|index sheet|\bform\b|slip|card"),
         ("Memorandum", r"memo"), ("Diplomatic note", r"diplomatic note|note verbale|aide.?m|\bnote\b"),
         ("Press & radio", r"clipping|newspaper|press|article|magazine|radio|intercept|broadcast|bulletin|editorial"),
         ("Laws & translations", r"law|ordinance|regulation|legal|translation|treaty|agreement|rescript|decree"),
         ("Report", r"report|survey|summary|study|intelligence|review|statistic|table"), ("Letter", r"letter|petition|card|postcard"),
         ("Enclosure", r"enclosure|attachment")]


def coarse(ty):
    t = (ty or "").lower()
    return next((name for name, rx in TYPES if re.search(rx, t)), "Other")


def norm(d):
    return None if not d or not re.match(r"^1[89]\d\d", d) else (d + "-01-01")[:10] if len(d) == 4 else (d + "-01")[:10] if len(d) == 7 else d[:10]


SUBS = {}
for fid, seq, a, b, d, ty in _db.execute("select file_id, seq, start_page, end_page, date, doc_type from subfiles order by file_id, seq"):
    SUBS.setdefault(fid, []).append((seq, a or 0, b or a or 0, norm(d), coarse(ty)))


def subfile(fid, p):  # (seq, date, type) of the first catalogued document covering page p, or (0, None, None)
    for seq, a, b, d, ty in SUBS.get(fid, []):
        if a <= p <= b: return seq, d, ty
    return 0, None, None


def record(url, fid, p, body, meta, dec, title):
    seq, d, ty = subfile(fid, p)
    date = d or docdate(fid, title)
    yrs = [d[:4]] if d else years(title)
    return {"url": url, "content": body, "meta": {**meta, "seq": str(seq)},
            "filters": {"subject": [subject(dec)], "year": yrs, "doc": [fid], "sub": [f"{fid}#{seq}"], "type": [ty or "Uncatalogued pages"], "decimal": [dec]},
            "sort": {"date": f"{date}|{fid}|{p:05d}"}}


def docdate(fid, title):  # title date, else catalogue date, else first dated sub-document; 9999 sorts last
    d = start_date(title)
    return d if d != "9999-99-99" else CATDATE.get(fid, d)


def pages(path):
    txt = open(path, encoding="utf-8").read()
    parts = re.split(r"^=== Page (\d+) ===.*$", txt, flags=re.M)
    for i in range(1, len(parts), 2):
        body = re.sub(r"\s+", " ", parts[i + 1]).strip()
        if body: yield int(parts[i]), body


out = open(os.path.join(HERE, "records.jsonl"), "w", encoding="utf-8")
n = 0
# 1. Files/*.pdf with Mistral OCR
for r in csv.DictReader(open(os.path.join(FILES, "upload.csv"), encoding="utf-8")):
    ocr = os.path.join(FILES, "ocr", r["file"][:-4] + ".txt")
    if not os.path.exists(ocr): continue
    dec = r["title"].split(" ", 1)[0]
    for p, body in pages(ocr):
        out.write(json.dumps(record(f"#d/{r['identifier']}/pg/{p}", r["identifier"], p, body, {
            "title": r["title"], "page": str(p), "id": r["identifier"], "ia": r["identifier"], "iaf": "", "src": "Mistral OCR"}, dec, r["title"]), ensure_ascii=False) + "\n"); n += 1
# 2. merged/*.pdf with Paddle OCR
mrows = list(csv.DictReader(open(os.path.join(WEB, "merged-items.csv"), encoding="utf-8")))
head = {r["identifier"]: r for r in mrows if r["title"]}
nparts = {}
for r in mrows: nparts[r["identifier"]] = nparts.get(r["identifier"], 0) + 1
for r in mrows:
    ocr = next((q for q in (os.path.join(FILES, "merged", sub, r["file"][:-4] + ".txt") for sub in ("ocr", "ocr-batch3", "ocr-batch4")) if os.path.exists(q)), None)
    if not ocr: continue
    m = re.search(r"-part-(\d+)\.pdf$", r["file"])
    key = r["identifier"] + (f"-part-{m.group(1)}" if m else "")
    title = head[r["identifier"]]["title"] + (f", part {m.group(1)} of {nparts[r['identifier']]}" if m else "")
    dec = title.split(" ", 1)[0]
    for p, body in pages(ocr):
        out.write(json.dumps(record(f"#d/{key}/pg/{p}", key, p, body, {
            "title": title, "page": str(p), "id": key, "ia": r["identifier"], "iaf": r["REMOTE_NAME"] or r["file"], "src": "PaddleOCR"}, dec, title), ensure_ascii=False) + "\n"); n += 1
out.close()
print(n, "pages written to records.jsonl")
