"""Build web/catalog.sqlite and web/data.js from the per-file JSON in web/json/.

Mention counts for places and people are counted in the OCR text itself (all the
spellings the model reported for that name), not taken from the model.
Run any time; it rebuilds from whatever JSON exists so far.

Speed: counting mentions is the slow part (one pattern search per name over a file's whole OCR text).
Counts and page totals are cached in .counts-cache.json, keyed by the OCR file's path, size and
modification time plus the exact list of names, so only new or changed files are recounted; those are
counted in parallel on all but two CPU cores. Delete the cache file to force a full recount.
"""
import os, re, csv, json, sqlite3, hashlib, multiprocessing

WEB = os.path.dirname(os.path.abspath(__file__))
FILES = os.path.dirname(WEB)
DB = os.path.join(WEB, "catalog.sqlite")

rows = {r["identifier"]: r for r in csv.DictReader(open(os.path.join(FILES, "upload.csv"), encoding="utf-8"))}


def count(text, variants):
    vs = sorted({v.strip() for v in variants if len(v.strip()) >= 2}, key=len, reverse=True)
    if not vs: return 0
    rx = re.compile(r"(?<![A-Za-z])(?:" + "|".join(re.escape(v) for v in vs) + r")(?![A-Za-z])", re.I)
    return len(rx.findall(text))


def group_names(items):
    """Combine entries with the same name: [(name, role, sorted variants)]."""
    by = {}
    for it in items:
        n = it["name"].strip()
        if not n: continue
        e = by.setdefault(n.lower(), {"name": n, "role": it.get("role", ""), "variants": set()})
        e["variants"].update(it.get("variants") or []); e["variants"].add(n)
        if not e["role"] and it.get("role"): e["role"] = it["role"]
    return [(e["name"], e["role"], sorted(e["variants"])) for e in by.values()]


def analyse(job):
    """Worker: page total and mention counts for one OCR file. job = (key, text path, places, people)."""
    key, tp, places, people = job
    text = open(tp, encoding="utf-8").read()
    pages = len(re.findall(r"^=== Page \d+ ===", text, re.M))
    return key, {"pages": pages, "pl": [count(text, v) for _, _, v in places], "pe": [count(text, v) for _, _, v in people]}


def fingerprint(job):
    key, tp, places, people = job
    st = os.stat(tp)
    return hashlib.sha1(json.dumps([tp, st.st_size, st.st_mtime_ns, places, people], ensure_ascii=False).encode()).hexdigest()


def run_jobs(jobs):
    """Return {key: {"pages", "pl", "pe"}}, using the cache and counting misses in parallel."""
    path = os.path.join(WEB, ".counts-cache.json")
    try: cache = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError): cache = {}
    fps = {j[0]: fingerprint(j) for j in jobs}
    todo = [j for j in jobs if cache.get(j[0], {}).get("fp") != fps[j[0]]]
    print(f"mention counts: {len(jobs) - len(todo)} cached, {len(todo)} to count", flush=True)
    if todo:
        todo.sort(key=lambda j: -os.path.getsize(j[1]))  # biggest first, so the pool finishes evenly
        with multiprocessing.get_context("fork").Pool(max(1, (os.cpu_count() or 4) - 2)) as pool:
            for n, (key, res) in enumerate(pool.imap_unordered(analyse, todo), 1):
                cache[key] = {"fp": fps[key], **res}
                if n % 50 == 0: print(f"  counted {n}/{len(todo)}", flush=True)
        keep = {j[0] for j in jobs}
        cache = {k: v for k, v in cache.items() if k in keep}
        json.dump(cache, open(path + ".tmp", "w", encoding="utf-8"), ensure_ascii=False); os.replace(path + ".tmp", path)
    return cache


def ranked(groups, counts):
    out = [{"name": n, "role": r, "count": c} for (n, r, _), c in zip(groups, counts)]
    return sorted(out, key=lambda e: (-e["count"], e["name"]))


# ---- pre-pass: every OCR file to analyse (small files in json/, catalogued merged files, uncatalogued merged files) ----
MERGED = os.path.join(FILES, "merged")
mrows = list(csv.DictReader(open(os.path.join(WEB, "merged-items.csv"), encoding="utf-8")))
mtitle = {r["identifier"]: r for r in mrows if r["title"]}
mdir = os.path.join(WEB, "json-merged")


def merged_text(pdf):
    return next((q for q in (os.path.join(MERGED, sub, pdf[:-4] + ".txt") for sub in ("ocr", "ocr-batch3", "ocr-batch4")) if os.path.exists(q)), None)


JOBS, GROUPS, SMALL, MERGEDF = [], {}, [], []
for fn in sorted(os.listdir(os.path.join(WEB, "json"))):
    if not fn.endswith(".json"): continue
    d = json.load(open(os.path.join(WEB, "json", fn), encoding="utf-8")); ident = fn[:-5]
    GROUPS[ident] = (group_names(d.get("places", [])), group_names(d.get("people", [])))
    JOBS.append((ident, os.path.join(FILES, "ocr", rows[ident]["file"][:-4] + ".txt"), *GROUPS[ident])); SMALL.append((fn, d))
for key in sorted(os.listdir(mdir)) if os.path.isdir(mdir) else []:
    fj = os.path.join(mdir, key, "file.json")
    if not os.path.exists(fj): continue
    fd = json.load(open(fj, encoding="utf-8"))
    secs = [json.load(open(os.path.join(mdir, key, f"s{i:03d}.json"), encoding="utf-8")) for i in range(1, fd["_meta"]["sections"] + 1)]
    GROUPS[key] = (group_names([p for sc in secs for p in sc.get("places", [])]), group_names([p for sc in secs for p in sc.get("people", [])]))
    JOBS.append((key, merged_text(fd["_meta"]["pdf"]), *GROUPS[key])); MERGEDF.append((key, fd, secs))
done_pdfs = {fd["_meta"]["pdf"] for _, fd, _ in MERGEDF}
for item in mrows:
    tp = None if item["file"] in done_pdfs else merged_text(item["file"])
    if tp: JOBS.append(("skeleton:" + item["file"], tp, [], []))
R = run_jobs(JOBS)

DB_FINAL = DB; DB = DB + ".building"
if os.path.exists(DB): os.remove(DB)
db = sqlite3.connect(DB)
db.executescript("""
CREATE TABLE files (id TEXT PRIMARY KEY, file TEXT, title TEXT, decimal TEXT, file_no INTEGER,
  date TEXT, pages INTEGER, overview TEXT, interest TEXT, keywords TEXT, model TEXT, created TEXT,
  ia_item TEXT, ia_file TEXT, source TEXT);
CREATE TABLE places (file_id TEXT, name TEXT, mentions INTEGER);
CREATE TABLE people (file_id TEXT, name TEXT, role TEXT, mentions INTEGER);
CREATE TABLE file_keywords (file_id TEXT, keyword TEXT);
CREATE TABLE subfiles (file_id TEXT, seq INTEGER, title TEXT, doc_type TEXT, start_page INTEGER, end_page INTEGER,
  date TEXT, sender TEXT, recipient TEXT, about TEXT, summary TEXT, keywords TEXT);
CREATE TABLE subfile_keywords (file_id TEXT, seq INTEGER, keyword TEXT);
CREATE INDEX i_places ON places(name); CREATE INDEX i_people ON people(name);
CREATE INDEX i_fkw ON file_keywords(keyword); CREATE INDEX i_skw ON subfile_keywords(keyword);
CREATE INDEX i_sub ON subfiles(file_id);
""")

site = []
for fn, d in SMALL:
    ident = fn[:-5]; r = rows[ident]
    pages = R[ident]["pages"]
    dec = r["title"].split(" ", 1)[0]
    num = int(re.search(r"\(File (\d+)\)$", r["title"]).group(1))
    places = ranked(GROUPS[ident][0], R[ident]["pl"])
    people = ranked(GROUPS[ident][1], R[ident]["pe"])
    kws = list(dict.fromkeys(k.strip() for k in d.get("keywords", []) if k.strip()))
    meta = d.get("_meta", {})
    db.execute("INSERT INTO files VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (ident, r["file"], r["title"], dec, num, r["date"], pages, d.get("overview", ""),
                d.get("interest", ""), "; ".join(kws), meta.get("model", ""), meta.get("created", ""),
                ident, r["file"], "Mistral OCR"))
    db.executemany("INSERT INTO places VALUES (?,?,?)", [(ident, p["name"], p["count"]) for p in places])
    db.executemany("INSERT INTO people VALUES (?,?,?,?)", [(ident, p["name"], p["role"], p["count"]) for p in people])
    db.executemany("INSERT INTO file_keywords VALUES (?,?)", [(ident, k) for k in kws])
    subs = []
    for i, s in enumerate(d.get("subfiles", []), 1):
        skw = list(dict.fromkeys(k.strip() for k in s.get("keywords", []) if k.strip()))
        db.execute("INSERT INTO subfiles VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                   (ident, i, s.get("title", ""), s.get("doc_type", ""), s.get("start_page"), s.get("end_page"),
                    s.get("date", ""), s.get("from", ""), s.get("to", ""), s.get("about", ""),
                    s.get("summary", ""), "; ".join(skw)))
        db.executemany("INSERT INTO subfile_keywords VALUES (?,?,?)", [(ident, i, k) for k in skw])
        subs.append({"t": s.get("title", ""), "ty": s.get("doc_type", ""), "p": [s.get("start_page"), s.get("end_page")],
                     "d": s.get("date", ""), "fr": s.get("from", ""), "to": s.get("to", ""),
                     "a": s.get("about", ""), "s": s.get("summary", ""), "k": skw})
    site.append({"id": ident, "f": r["file"], "t": r["title"], "dec": dec, "n": num, "d": r["date"], "pg": pages,
                 "o": d.get("overview", ""), "i": d.get("interest", ""), "k": kws,
                 "pl": [[p["name"], p["count"]] for p in places],
                 "pe": [[p["name"], p["role"], p["count"]] for p in people], "sub": subs})
# ---- large merged PDFs (Paddle OCR), catalogued section by section in json-merged/ ----
nparts = {}
for r in mrows: nparts[r["identifier"]] = nparts.get(r["identifier"], 0) + 1
catalogued_pdfs = set()
for key, fd, secs in MERGEDF:
    pdf = fd["_meta"]["pdf"]
    item = [r for r in mrows if r["file"] == pdf][0]; ia = item["identifier"]; head = mtitle[ia]
    m = re.search(r"-part-(\d+)\.pdf$", pdf)
    title = head["title"] + (f", part {m.group(1)} of {nparts[ia]}" if m else "")
    pages = R[key]["pages"]
    places = ranked(GROUPS[key][0], R[key]["pl"])
    people = ranked(GROUPS[key][1], R[key]["pe"])
    sublist = []
    for sc in secs:
        for sd in sc.get("subfiles", []):
            if sd.get("continues_previous") and sublist:
                prev = sublist[-1]; prev["end_page"] = max(prev.get("end_page") or 0, sd.get("end_page") or 0)
                prev["keywords"] = list(dict.fromkeys((prev.get("keywords") or []) + (sd.get("keywords") or [])))
            else:
                sublist.append(dict(sd))
    kws = list(dict.fromkeys(k.strip() for k in fd.get("keywords", []) if k.strip()))
    dec = title.split(" ", 1)[0]; num = int(re.search(r"\(File (\d+)\)", title).group(1))
    remote = item["REMOTE_NAME"] or pdf
    models = sorted({sc["_meta"].get("model", "") for sc in secs} | {fd["_meta"].get("model", "")})
    db.execute("INSERT INTO files VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (key, pdf, title, dec, num, head["date"], pages, fd.get("overview", ""), fd.get("interest", ""),
                "; ".join(kws), ", ".join(models), fd["_meta"].get("created", ""), ia, remote, "PaddleOCR"))
    db.executemany("INSERT INTO places VALUES (?,?,?)", [(key, p["name"], p["count"]) for p in places])
    db.executemany("INSERT INTO people VALUES (?,?,?,?)", [(key, p["name"], p["role"], p["count"]) for p in people])
    db.executemany("INSERT INTO file_keywords VALUES (?,?)", [(key, k) for k in kws])
    subs = []
    for i, s in enumerate(sublist, 1):
        skw = list(dict.fromkeys(k.strip() for k in s.get("keywords", []) if k.strip()))
        db.execute("INSERT INTO subfiles VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                   (key, i, s.get("title", ""), s.get("doc_type", ""), s.get("start_page"), s.get("end_page"),
                    s.get("date", ""), s.get("from", ""), s.get("to", ""), s.get("about", ""), s.get("summary", ""), "; ".join(skw)))
        db.executemany("INSERT INTO subfile_keywords VALUES (?,?,?)", [(key, i, k) for k in skw])
        subs.append({"t": s.get("title", ""), "ty": s.get("doc_type", ""), "p": [s.get("start_page"), s.get("end_page")],
                     "d": s.get("date", ""), "fr": s.get("from", ""), "to": s.get("to", ""),
                     "a": s.get("about", ""), "s": s.get("summary", ""), "k": skw})
    site.append({"id": key, "ia": ia, "iaf": remote, "f": pdf, "t": title, "dec": dec, "n": num, "d": head["date"], "pg": pages,
                 "o": fd.get("overview", ""), "i": fd.get("interest", ""), "k": kws,
                 "pl": [[p["name"], p["count"]] for p in places],
                 "pe": [[p["name"], p["role"], p["count"]] for p in people], "sub": subs})
    catalogued_pdfs.add(pdf)
# ---- merged PDFs with OCR but no catalogue yet: skeleton entries (title, page count; full-text search covers them) ----
NOTE = "Note: The sub-file list, summaries, and tags for this file are not yet available."
for item in mrows:
    pdf = item["file"]
    if pdf in catalogued_pdfs: continue
    if "skeleton:" + pdf not in R: continue
    ia = item["identifier"]; head = mtitle[ia]
    m = re.search(r"-part-(\d+)\.pdf$", pdf)
    key = ia + (f"-part-{m.group(1)}" if m else "")
    title = head["title"] + (f", part {m.group(1)} of {nparts[ia]}" if m else "")
    pages = R["skeleton:" + pdf]["pages"]
    dec = title.split(" ", 1)[0]; num = int(re.search(r"\(File (\d+)\)", title).group(1))
    remote = item["REMOTE_NAME"] or pdf
    db.execute("INSERT INTO files VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (key, pdf, title, dec, num, head["date"], pages, NOTE, "", "", "", "", ia, remote, "PaddleOCR"))
    site.append({"id": key, "ia": ia, "iaf": remote, "f": pdf, "t": title, "dec": dec, "n": num, "d": head["date"], "pg": pages,
                 "o": NOTE, "i": "", "k": [], "pl": [], "pe": [], "sub": [], "sk": 1})
EXPECTED = len(rows) + len(mrows)
db.commit(); db.close(); os.replace(DB, DB_FINAL)

site.sort(key=lambda x: (x["dec"], x["d"] or "9999", x["n"]))
# write to a temp file and swap it in, so a page loaded mid-rebuild never sees half a file
tmp = os.path.join(WEB, "data.js.tmp")
with open(tmp, "w", encoding="utf-8") as fh:
    fh.write("window.CATALOG=" + json.dumps(site, ensure_ascii=False, separators=(",", ":")) + ";\n"
             f"window.CATALOG_EXPECTED={EXPECTED};\n")
os.replace(tmp, os.path.join(WEB, "data.js"))
print(f"{len(site)} files, {sum(len(x['sub']) for x in site)} sub-files -> catalog.sqlite, data.js")
