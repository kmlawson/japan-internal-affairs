"""Catalogue the large merged/ PDFs (Paddle OCR text in ../merged/ocr/) for the website.

Each PDF is split into sections of <= 80 pages / ~100k characters. Each section is catalogued
on its own (documents, people, places, keywords); when all sections of a PDF are done, one more
request writes the file-level overview, keywords and "interest" note from the section results.

  python catalog_merged.py --backend agy            # works from the start of the list
  python catalog_merged.py --backend codex --reverse  # works from the end

Output: json-merged/<key>/sNNN.json and json-merged/<key>/file.json, where <key> is the
archive.org identifier plus "-part-N" for split PDFs. Picks up PDFs as Paddle finishes them,
and keeps waiting while Paddle is still running. Safe to run two workers (per-task lock files).
"""
import os, re, csv, json, sys, time, argparse, subprocess, tempfile
from datetime import datetime, timezone

WEB = os.path.dirname(os.path.abspath(__file__))
MERGED = os.path.join(os.path.dirname(WEB), "merged")
OCR = os.path.join(MERGED, "ocr")
OUT = os.path.join(WEB, "json-merged")
ITEMS = os.path.join(WEB, "merged-items.csv")  # full copy of merged/upload.csv (before any trimming)
MAX_PAGES, MAX_CHARS = 80, 100_000

ap = argparse.ArgumentParser()
ap.add_argument("--backend", default="agy")
ap.add_argument("--model", default=None)
ap.add_argument("--reverse", action="store_true")
ap.add_argument("--ocr", default=None, help="OCR dir (default merged/ocr)")
ap.add_argument("--only", default=None, help="file listing the only PDFs to catalogue; stop when they are done")
ap.add_argument("--timeout", type=int, default=1200)
ap.add_argument("--effort", default=None, help="Codex reasoning effort (low, medium, high, xhigh); default from ~/.codex/config.toml")
ap.add_argument("--tag", default="", help="suffix for this worker's log and status files")
a = ap.parse_args()
OCRS = [os.path.abspath(d) for d in a.ocr.split(",")] if a.ocr else [OCR]
ONLY = set(l.strip() for l in open(a.only) if l.strip()) if a.only else None
LOG = os.path.join(WEB, f"catalog-merged-{a.backend}{a.tag}.log")
STATUS = os.path.join(WEB, f"_status-merged-{a.backend}{a.tag}.txt")
AGY_MODEL = a.model or "gemini-3.8-flash-medium"
# agy keeps separate quota buckets: "Gemini Models" and "Claude and GPT models"
QUOTA_GROUP = "Gemini Models" if AGY_MODEL.startswith("gemini") else "Claude and GPT models"


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)


def strict(s):
    if isinstance(s, dict):
        if s.get("type") == "object":
            s["additionalProperties"] = False; s["required"] = list(s["properties"])
        for v in s.values(): strict(v)
    elif isinstance(s, list):
        for v in s: strict(v)
    return s


NAMES = {"type": "array", "items": {"type": "object", "properties": {
    "name": {"type": "string"}, "role": {"type": "string"},
    "variants": {"type": "array", "items": {"type": "string"}}}}}
SECTION_SCHEMA = strict({"type": "object", "properties": {
    "section_summary": {"type": "string", "description": "2-4 sentences on what this section of the file contains"},
    "places": {"type": "array", "items": {"type": "object", "properties": {
        "name": {"type": "string"}, "variants": {"type": "array", "items": {"type": "string"}}}}},
    "people": NAMES,
    "keywords": {"type": "array", "items": {"type": "string"}},
    "subfiles": {"type": "array", "items": {"type": "object", "properties": {
        "title": {"type": "string"}, "doc_type": {"type": "string"},
        "start_page": {"type": "integer"}, "end_page": {"type": "integer"},
        "continues_previous": {"type": "boolean", "description": "true only if this document began before the first page of this section"},
        "date": {"type": "string"}, "from": {"type": "string"}, "to": {"type": "string"},
        "about": {"type": "string"}, "summary": {"type": "string"},
        "keywords": {"type": "array", "items": {"type": "string"}}}}}}})
FILE_SCHEMA = strict({"type": "object", "properties": {
    "overview": {"type": "string"}, "keywords": {"type": "array", "items": {"type": "string"}},
    "interest": {"type": "string"}}})

SECTION_PROMPT = """You are cataloguing a large file from the U.S. State Department's Records Relating to Internal Affairs of Japan, 1930-1949 (Record Group 59, decimal file 894). The file is a bundle of documents (telegrams, despatches, memoranda, letters, cross-reference sheets, clippings) filed under one subject. It is too long to read at once, so you are given one section: pages {START}-{END} of {PAGES}. Each page starts with a "=== Page N ===" header; use those page numbers.

The text comes from machine OCR of typewritten pages and is noisy: correct obvious misreadings of names and places, and ignore stray characters, stamps and marginal numbers.

Do not use any tools or read any files. Answer only from the text below.

Produce:
- section_summary: 2-4 sentences on this section.
- places: every place mentioned, with a standard modern English name and the spellings exactly as they occur.
- people: every named person, with role if stated and the name forms exactly as they occur. Not organisations.
- keywords: 5-12 for this section.
- subfiles: the individual documents in this section, in page order, each with start/end page, title, type, date (YYYY-MM-DD, YYYY-MM or YYYY; empty if none), sender, recipient, a one-sentence "about", a summary whose length fits the document: a one-page cover sheet, form, document file note, index card or routing slip at most 20 words; any other document under 5 pages at most 50 words (aim for 20 or fewer); longer documents 2-5 sentences, and keywords. Every page should belong to a document. If the first document clearly began before page {START}, set continues_previous=true for it.

File title: {TITLE}

OCR TEXT (pages {START}-{END}):
{TEXT}
"""
FILE_PROMPT = """Below is a list of the documents in a large file from the U.S. State Department's Records Relating to Internal Affairs of Japan, 1930-1949 (Record Group 59, decimal file 894), with section summaries. It was compiled from the file section by section.

Do not use any tools or read any files. Answer only from the text below.

Produce:
- overview: one paragraph on the file as a whole (at most 20 words if the file is a single page, at most 50 words if it has fewer than 5 pages).
- keywords: 8-15 for the whole file.
- interest: what is interesting, unique or useful about this file and its documents for a historian, in at most 20 words (at most 10 words if the file is a single page; a few sentences only for files of hundreds of pages).

File title: {TITLE} ({PAGES} pages, {NDOCS} documents)

{BODY}
"""

sec_schema_path = os.path.join(tempfile.gettempdir(), "catalog-merged-section.json")
file_schema_path = os.path.join(tempfile.gettempdir(), "catalog-merged-file.json")
json.dump(SECTION_SCHEMA, open(sec_schema_path, "w")); json.dump(FILE_SCHEMA, open(file_schema_path, "w"))
CODEX_CFG = os.path.expanduser("~/.codex/config.toml")
CODEX_MODEL = next((l.split("=", 1)[1].strip().strip('"') for l in open(CODEX_CFG) if re.match(r"\s*model\s*=", l)),
                   "codex default") if os.path.exists(CODEX_CFG) else "codex default"
workdir = tempfile.mkdtemp(prefix="catalog-merged-")


def run_llm(prompt, schema_path):
    if a.backend == "agy":
        p = subprocess.run(["agy", "-p", prompt, "--model", AGY_MODEL, "--output-format", "json",
                            "--json-schema", schema_path, "--print-timeout", f"{a.timeout}s", "--disable-slash-commands"],
                           capture_output=True, text=True, cwd=workdir, timeout=a.timeout + 60)
        res = json.loads(p.stdout); data = res.get("structured_output")
        meta = {"model": AGY_MODEL, "usage": res.get("usage")}
    else:
        last = os.path.join(workdir, "last.json")
        p = subprocess.run(["codex", "exec", "--skip-git-repo-check", "--ephemeral", "-s", "read-only",
                            "--output-schema", schema_path, "-o", last] + (["-m", a.model] if a.model else []) + (["-c", f'model_reasoning_effort="{a.effort}"'] if a.effort else []) + ["-"],
                           input=prompt, capture_output=True, text=True, cwd=workdir, timeout=a.timeout + 60)
        data = json.load(open(last)); os.remove(last)
        meta = {"model": a.model or CODEX_MODEL}
    assert data, (p.stdout[-400:], p.stderr[-400:])
    return data, meta


def gemini_usage():
    try:
        r = json.loads(subprocess.run(["agy", "-p", "/usage", "--output-format", "json"],
                                      capture_output=True, text=True, timeout=60).stdout)["response"]
    except Exception:
        return {}
    out = {}
    for line in r.splitlines():
        m = re.match(re.escape(QUOTA_GROUP) + r"\t(Weekly|Five Hour) Limit Remaining\t(\d+)%\t(\S+)", line)
        if m: out[m.group(1)] = (int(m.group(2)), m.group(3))
    return out


def wait_for_quota(u):
    """Pause (never give up) until the limit resets if this worker's agy quota bucket is nearly used (< 10%).
    Stopping early matters: when a bucket runs out, agy has been seen to fall back silently to other models."""
    low = [(k, v) for k, v in u.items() if v[0] < 10]
    if not low: return
    reset = max(datetime.fromisoformat(v[1].replace("Z", "+00:00")) for _, v in low)
    secs = max(60, (reset - datetime.now(timezone.utc)).total_seconds() + 120)
    log(f"quota low {low}; pausing until {reset.isoformat()} ({secs / 3600:.1f} h)")
    open(STATUS, "a").write(f"paused for quota until {reset.isoformat()}\n")
    time.sleep(secs)


rows = {r["file"]: r for r in csv.DictReader(open(ITEMS, encoding="utf-8"))}
titles = {}
for r in csv.DictReader(open(ITEMS, encoding="utf-8")):
    if r["title"]: titles[r["identifier"]] = r["title"]


def key_for(pdf):
    m = re.search(r"-part-(\d+)\.pdf$", pdf)
    return rows[pdf]["identifier"] + (f"-part-{m.group(1)}" if m else "")


def title_for(pdf):
    t = titles[rows[pdf]["identifier"]]
    m = re.search(r"-part-(\d+)\.pdf$", pdf)
    if m:
        n = sum(1 for f in rows if rows[f]["identifier"] == rows[pdf]["identifier"])
        t += f", part {m.group(1)} of {n}"
    return t


def pages_of(pdf):
    tp = next(p for p in (os.path.join(d, pdf[:-4] + ".txt") for d in OCRS) if os.path.exists(p))
    txt = open(tp, encoding="utf-8").read()
    parts = re.split(r"^(=== Page \d+ ===.*)$", txt, flags=re.M)
    return [parts[i] + "\n" + parts[i + 1].strip() + "\n" for i in range(1, len(parts), 2)]


def sections(pages):
    out, start, size = [], 0, 0
    for i, p in enumerate(pages):
        if i > start and (i - start >= MAX_PAGES or size + len(p) > MAX_CHARS):
            out.append((start, i)); start, size = i, 0
        size += len(p)
    out.append((start, len(pages)))
    return out  # [(first index, end index)] 0-based, end exclusive


def finished_pdfs():
    done = [l.strip() for d in OCRS for l in open(os.path.join(d, "_done.txt")) if l.strip()]
    test = "894.248 Equipment And Supplies., Japan, Aircraft. Accidents.Landing Fields. Stations., January 2, 1940 - March 21, 1944 1420.pdf"
    if test not in done and os.path.exists(os.path.join(MERGED, "ocr", test[:-4] + ".txt")): done.append(test)
    return [f for f in done if f in rows and (ONLY is None or f in ONLY)]


def tasks():
    """All tasks in list order: each PDF's sections, then its file summary."""
    out = []
    for pdf in finished_pdfs():
        k = key_for(pdf); d = os.path.join(OUT, k)
        pages = pages_of(pdf); secs = sections(pages)
        for i, (s, e) in enumerate(secs, 1):
            out.append(("section", pdf, k, i, s, e, len(pages), len(secs)))
        out.append(("file", pdf, k, 0, 0, len(pages), len(pages), len(secs)))
    return out


def lock(path):
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY); os.write(fd, a.backend.encode()); os.close(fd); return True
    except FileExistsError:
        if time.time() - os.path.getmtime(path) > 3 * 3600: os.utime(path); return True
        return False


def do_section(pdf, k, i, s, e, npages, nsecs):
    pages = pages_of(pdf)
    prompt = (SECTION_PROMPT.replace("{START}", str(s + 1)).replace("{END}", str(e)).replace("{PAGES}", str(npages))
              .replace("{TITLE}", title_for(pdf)).replace("{TEXT}", "".join(pages[s:e])))
    data, meta = run_llm(prompt, sec_schema_path)
    data["_meta"] = dict(meta, backend=a.backend, section=i, sections=nsecs, pages=[s + 1, e], pdf=pdf)
    return data


def do_file(pdf, k, nsecs, npages):
    secs = [json.load(open(os.path.join(OUT, k, f"s{i:03d}.json"))) for i in range(1, nsecs + 1)]
    lines, ndocs = [], 0
    for sec in secs:
        lines.append(f"\nSECTION pages {sec['_meta']['pages'][0]}-{sec['_meta']['pages'][1]}: {sec.get('section_summary', '')}")
        for d in sec.get("subfiles", []):
            ndocs += 1
            lines.append(f"- pp. {d.get('start_page')}-{d.get('end_page')} | {d.get('date', '')} | {d.get('doc_type', '')} | {d.get('title', '')} — {d.get('about', '')}")
    prompt = (FILE_PROMPT.replace("{TITLE}", title_for(pdf)).replace("{PAGES}", str(npages))
              .replace("{NDOCS}", str(ndocs)).replace("{BODY}", "\n".join(lines)))
    data, meta = run_llm(prompt, file_schema_path)
    data["_meta"] = dict(meta, backend=a.backend, pdf=pdf, pages=npages, sections=nsecs)
    return data


checked = 0
while True:
    tl = tasks()
    if a.reverse:  # second worker: walk PDFs from the end, but each PDF's sections still in order
        by = {}
        for t in tl: by.setdefault(t[2], []).append(t)
        tl = [t for k in reversed(list(by)) for t in by[k]]
    todo = []
    for t in tl:
        kind, pdf, k, i = t[:4]; d = os.path.join(OUT, k)
        out = os.path.join(d, "file.json" if kind == "file" else f"s{i:03d}.json")
        if os.path.exists(out): continue
        if kind == "file" and not all(os.path.exists(os.path.join(d, f"s{j:03d}.json")) for j in range(1, t[7] + 1)): continue
        todo.append((t, out))
    left_sections = sum(1 for t in tl if t[0] == "section" and not os.path.exists(os.path.join(OUT, t[2], f"s{t[3]:03d}.json")))
    done_files = sum(1 for t in tl if t[0] == "file" and os.path.exists(os.path.join(OUT, t[2], "file.json")))
    paddle = subprocess.run(["pgrep", "-f", "paddle_ocr_images.py"], capture_output=True).returncode == 0
    open(STATUS, "w").write(f"updated {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                            f"{done_files} of {sum(1 for t in tl if t[0] == 'file')} OCR'd PDFs fully catalogued (all workers); "
                            f"{left_sections} sections left\nPaddle still running: {paddle}\n")
    took = False
    for t, out in todo:
        kind, pdf, k, i, s, e, npages, nsecs = t
        os.makedirs(os.path.dirname(out), exist_ok=True)
        lk = out + ".lock"
        if os.path.exists(out) or not lock(lk): continue
        if a.backend == "agy":  # check this worker's quota bucket before every task; never sleep while holding a lock
            u = gemini_usage(); open(os.path.join(WEB, "usage.log"), "a").write(
                f"{time.strftime('%Y-%m-%d %H:%M:%S')}\tmerged\t{json.dumps(u)}\n")
            if any(v[0] < 10 for v in u.values()):
                os.remove(lk); wait_for_quota(u); continue
        checked += 1
        t0 = time.time()
        try:
            data = do_file(pdf, k, nsecs, npages) if kind == "file" else do_section(pdf, k, i, s, e, npages, nsecs)
            data["_meta"].update(seconds=round(time.time() - t0, 1), created=time.strftime("%Y-%m-%d %H:%M:%S"), key=k)
            json.dump(data, open(out + ".tmp", "w"), indent=1, ensure_ascii=False); os.replace(out + ".tmp", out)
            log(f"{time.time() - t0:.0f}s {kind} {i}/{nsecs} {len(data.get('subfiles', []))} docs {k}")
        except Exception as ex:
            log(f"ERROR {kind} {i} {k}: {ex!r}"[:600])
            open(os.path.join(WEB, "errors-merged.log"), "a").write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{a.backend}\t{k}\t{kind} {i}\t{ex!r}"[:1000] + "\n")
            if a.backend == "agy": wait_for_quota(gemini_usage())
            else: time.sleep(300)
        finally:
            if os.path.exists(lk): os.remove(lk)
        took = True
        break  # rebuild the task list after every task, so newly finished Paddle files are picked up
    if not took:
        if not todo and (not paddle or ONLY):
            log("nothing left to do and Paddle has finished - stopping"); break
        time.sleep(300)
