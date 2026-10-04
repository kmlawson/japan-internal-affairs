"""Build sets.js: curated groupings of sub-files shown under the Sets menu.

Each set is chosen by rules on sub-file titles, document types and dates in catalog.sqlite.
Items are keyed by file id + start page so they survive re-numbering of sub-files. Run after build_db.py.
mode "month": one row per report month, other copies shown as alternative versions (Monthly Reports).
mode "list": one row per document, sorted by date.
"""
import json, os, re, sqlite3

WEB = os.path.dirname(os.path.abspath(__file__))
db = sqlite3.connect(os.path.join(WEB, "catalog.sqlite"))
ROWS = db.execute("select file_id, seq, title, coalesce(doc_type,''), start_page, end_page, coalesce(date,'') from subfiles").fetchall()
MONTHS = "january february march april may june july august september october november december".split()
MRX = re.compile(r"\b(" + "|".join(MONTHS) + r"),?\s+(\d{4})\b", re.I)
XREF = re.compile(r"cross.?ref|file note", re.I)
rx = lambda p: re.compile(p, re.I)


def monthly_reports():
    inc = rx(r"conditions in japan|report on conditions|report of conditions|monthly report|monthly political report|political report for")
    exc_t = rx(r"memorandum|^summary|summary of|summary memorandum|review|extract|excerpt|cross.?ref|file note|list of|instruction|routing|"
               r"acknowledg|request|comment|section of|^internal political affairs|^the cabinet|^the diet|"
               r"^tokyo'?s (monthly|political) report|prior to|current conditions|economic conditions|"
               r"travel conditions|living conditions|industrial conditions|food conditions|military intelligence|semi-monthly")
    exc_d = rx(r"memo|extract|excerpt|cross|note|list|instruction|summary")
    out = []
    for fid, seq, t, ty, p0, p1, d in ROWS:
        if not inc.search(t) or exc_t.search(t) or exc_d.search(ty) or p1 <= p0: continue  # one page cannot be a full report
        if not re.search(r"month|report no\.|monthly", t, re.I): continue
        m = MRX.search(t)
        if not m: continue
        mm = f"{m.group(2)}.{MONTHS.index(m.group(1).lower()) + 1:02d}"
        if not "1929" <= mm[:4] <= "1942": continue
        no = re.search(r"report no\.\s*(\d+)", t, re.I)
        out.append({"f": fid, "p": p0, "m": mm, "n": int(no.group(1)) if no else None})
    return out


def pick(test):
    return [{"f": fid, "p": p0, "d": d, **({"x": 1} if XREF.search(t) or XREF.search(ty) else {})}
            for fid, seq, t, ty, p0, p1, d in ROWS if test(t, ty, d)]


cab_kw, cab_ev = rx(r"\bcabinet\b"), rx(r"resign|formation|\bfall\b|new cabinet|en bloc|chosen to form|form (a|the) (new )?cabinet|organi[sz]ation of the .*cabinet|cabinet crisis")
feb_any, feb_win = rx(r"february 26|feb\.? ?26|2\.26 incident|aizawa|february incident"), rx(r"coup|mutin|insurg|rebel|martial law|uprising|young officers")
diet = rx(r"diet session|session of the (imperial )?diet|\d+(st|nd|rd|th) (ordinary |extraordinary |special )?(session of the )?(imperial )?diet\b")
bio, bio_not, bio_ty = rx(r"biograph"), rx(r"biographical (record|form)s?\b|request for translation|translation resources|portraits for"), rx(r"^(diplomatic )?note$")
law_t, law_kw = rx(r"translat"), rx(r"\b(law|laws|ordinance|ordinances|code|constitution|regulations?|statute|rescript|act)\b")
law_not, law_not_ty = rx(r"speech|article|rights|instruction to|correction of|press translations"), rx(r"magazine|file note|cross|press-translation")
rad_ty = rx(r"intercept|news bulletin|press digest|radio press|radio broadcast transcript|domei|press-translation bulletin|radio commentary")
rad_not = rx(r"radio-station|directory|church|corporate|statistical|election bulletin|campaign|radiogram|code radio|coded radio|intercepted letter|intercepted correspondence|intelligence")

SETS = [
    {"id": "monthly-reports", "name": "Monthly Reports", "mode": "month",
     "about": "The American Embassy in Tokyo's monthly reports on conditions in Japan, and the despatches that transmitted them. "
              "Memoranda summarising or reacting to a report, extracts filed elsewhere and one-page cover sheets are left out. "
              "Several months survive in more than one copy.",
     "items": monthly_reports()},
    {"id": "cabinet-changes", "name": "Cabinet Changes", "mode": "list",
     "about": "Resignations, falls and formations of Japanese cabinets, from the Hamaguchi cabinet in 1931 to the fall of Tojo in 1944: "
              "telegrams, despatches, memoranda and press reactions.",
     "items": pick(lambda t, ty, d: cab_kw.search(t) and cab_ev.search(t))},
    {"id": "diet-sessions", "name": "Diet Sessions", "mode": "list",
     "about": "Reports on the numbered sessions of the Imperial Diet: openings, proceedings, legislation and closing reviews.",
     "items": pick(lambda t, ty, d: diet.search(t))},
    {"id": "february-26-incident", "name": "February 26 Incident", "mode": "list",
     "about": "The army mutiny of 26 February 1936 in Tokyo, its suppression, the courts-martial of the insurgents and the related "
              "trial of Lieutenant Colonel Aizawa.",
     "items": pick(lambda t, ty, d: ("1936-02-20" <= d <= "1938-12-31" and feb_any.search(t))
                   or ("1936-02-20" <= d <= "1936-12-31" and feb_win.search(t)))},
    {"id": "biographical-sketches", "name": "Biographical Sketches", "mode": "list",
     "about": "Biographical notes, sketches and data on Japanese political, military and diplomatic figures. Routine exchanges of "
              "biographical record forms for accredited officers are left out.",
     "items": pick(lambda t, ty, d: bio.search(t) and not bio_not.search(t) and not bio_ty.search(ty))},
    {"id": "law-translations", "name": "Translations of Laws & Ordinances", "mode": "list",
     "about": "English translations of Japanese laws, codes, imperial and ministerial ordinances and regulations, including those of "
              "Taiwan and the Kwantung Leased Territory. Some entries are dated by the law itself rather than by when it was sent.",
     "items": pick(lambda t, ty, d: law_t.search(t) and law_kw.search(t) and not law_not.search(t) and not law_not_ty.search(ty))},
    {"id": "radio-intercepts", "name": "Wartime Radio & Domei Press Intercepts", "mode": "list",
     "about": "Monitored Japanese wartime news and propaganda: Domei news-agency bulletins and broadcasts, Radio Tokyo's Spanish "
              "service to Latin America and Jakarta Radio, mostly 1942–1944.",
     "items": pick(lambda t, ty, d: rad_ty.search(ty) and not rad_not.search(ty) and "farnsworth" not in t.lower())},
]
for s in SETS:
    s["items"].sort(key=lambda i: (i.get("m") or i.get("d") or "9999", i["f"], i["p"]))
with open(os.path.join(WEB, "sets.js"), "w", encoding="utf-8") as fh:
    fh.write("window.SETS=" + json.dumps(SETS, ensure_ascii=False, separators=(",", ":")) + ";\n")
for s in SETS:
    print(f"{s['id']:24} {len(s['items']):4} items, {sum(1 for i in s['items'] if i.get('x'))} cross-reference sheets")
