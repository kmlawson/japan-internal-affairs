"""Build sets.js: curated groupings of sub-files shown under the Sets menu.

Monthly Reports = the Tokyo Embassy's monthly "Report on Conditions in Japan" (and the despatches
transmitting it). Excluded: memoranda/summaries/reviews about a report, extracts and excerpts filed
elsewhere, cross-reference sheets, file notes and lists of papers.
Items are keyed by file id + start page so they survive re-numbering of sub-files. Run after build_db.py.
"""
import json, os, re, sqlite3

WEB = os.path.dirname(os.path.abspath(__file__))
db = sqlite3.connect(os.path.join(WEB, "catalog.sqlite"))
MONTHS = "january february march april may june july august september october november december".split()
MRX = re.compile(r"\b(" + "|".join(MONTHS) + r"),?\s+(\d{4})\b", re.I)

INCLUDE = re.compile(r"conditions in japan|report on conditions|report of conditions|monthly report|monthly political report|political report for", re.I)
EXCLUDE_TITLE = re.compile(r"memorandum|^summary|summary of|summary memorandum|review|extract|excerpt|cross.?ref|file note|list of|instruction|routing|"
                           r"acknowledg|request|comment|section of|^internal political affairs|^the cabinet|^the diet|"
                           r"^tokyo'?s (monthly|political) report|prior to|current conditions|economic conditions|"
                           r"travel conditions|living conditions|industrial conditions|food conditions|"
                           r"military intelligence|semi-monthly", re.I)
EXCLUDE_TYPE = re.compile(r"memo|extract|excerpt|cross|note|list|instruction|summary", re.I)

def report_month(title, date):
    m = MRX.search(title)
    if m: return f"{m.group(2)}.{MONTHS.index(m.group(1).lower()) + 1:02d}"
    return None

items = []
for fid, seq, title, ty, p0, p1, date, sender in db.execute(
        "select file_id, seq, title, doc_type, start_page, end_page, date, sender from subfiles"):
    if not INCLUDE.search(title) or EXCLUDE_TITLE.search(title) or EXCLUDE_TYPE.search(ty or ""): continue
    if not re.search(r"month|report no\.|monthly", title, re.I): continue
    m = report_month(title, date)
    if not m or not ("1929" <= m[:4] <= "1942"): continue
    no = re.search(r"report no\.\s*(\d+)", title, re.I)
    items.append({"f": fid, "p": p0, "m": m, "n": int(no.group(1)) if no else None})
items.sort(key=lambda i: (i["m"], i["f"], i["p"]))
sets = [{"id": "monthly-reports", "name": "Monthly Reports",
         "about": "The American Embassy in Tokyo's monthly reports on conditions in Japan, and the despatches that "
                  "transmitted them. Memoranda summarising or reacting to a report, and extracts filed elsewhere, are "
                  "left out. Several months survive in more than one copy.",
         "items": items}]
with open(os.path.join(WEB, "sets.js"), "w", encoding="utf-8") as fh:
    fh.write("window.SETS=" + json.dumps(sets, ensure_ascii=False, separators=(",", ":")) + ";\n")
print(len(items), "monthly-report items;", len({i["m"] for i in items}), "distinct months",
      min(i["m"] for i in items), "-", max(i["m"] for i in items))
