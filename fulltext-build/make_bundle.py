"""Write downloads/japan-internal-affairs-ocr-text.jsonl.gz: every OCR'd page as one JSON line
(file title, page number, OCR source, archive.org page URL, text), for bulk users and LLM crawlers."""
import os, json, gzip
from urllib.parse import quote
HERE = os.path.dirname(os.path.abspath(__file__)); WEB = os.path.dirname(HERE)
n = 0
with gzip.open(os.path.join(WEB, "downloads", "japan-internal-affairs-ocr-text.jsonl.gz"), "wt", encoding="utf-8", compresslevel=9) as out:
    for line in open(os.path.join(HERE, "records.jsonl"), encoding="utf-8"):
        r = json.loads(line); m = r["meta"]
        # split PDFs: the part's file name without ".pdf" selects it; single-PDF items need no file name
        part = m["iaf"] and "-part-" in m["id"]
        base = "https://archive.org/details/" + m["ia"] + (("/" + quote(m["iaf"][:-4])) if part else "")
        out.write(json.dumps({"id": m["id"], "title": m["title"], "page": int(m["page"]), "ocr": m["src"],
                              "archive_org": f"{base}/page/n{int(m['page']) - 1}", "text": r["content"]}, ensure_ascii=False) + "\n"); n += 1
print(n, "pages")
