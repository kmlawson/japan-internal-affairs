"""Japanese personal names in surname-first form: "Hideki Tojo" -> "TOJO Hideki".

  python3 people_names.py candidates   # list names that look like romanized Japanese -> people-names-todo.json
  python3 people_names.py run [--workers 2]   # ask a model (agy, Gemini Flash) in batches -> people-names.tsv

people-names.tsv (original <TAB> normalized) is the reviewed mapping. build_db.py applies it when it groups
names, keeping the original spelling as a search variant so mention counts in the OCR text are unchanged.
Names the model is unsure of are left out of the map (shown as before). Edit the TSV by hand to fix any entry;
a line whose second column is empty means "leave as is".
"""
import os, re, sys, json, glob, subprocess, tempfile
from concurrent.futures import ThreadPoolExecutor

WEB = os.path.dirname(os.path.abspath(__file__))
TSV = os.path.join(WEB, "people-names.tsv")
TODO = os.path.join(WEB, "people-names-todo.json")
MODEL = "gemini-3.8-flash-high"
SYL = r"(?:(?:ky|gy|sh|ch|ny|hy|by|py|my|ry|ts|j|f|[kgsztdnhbpmyrw])?[aiueoāīūēō]|n(?![aiueoy]))"
JP = re.compile(rf"^(?:{SYL})+$", re.I)
TITLE = re.compile(r"^(baron|count|marquis|viscount|prince|princess|general|admiral|lieutenant|colonel|major|captain|mr|mrs|miss|dr|professor|emperor|empress|premier|minister|ambassador|consul|vice|rear|lt|gen|adm|col|capt)\.?$", re.I)


def looks_japanese(n):
    t = [w for w in re.split(r"\s+", re.sub(r"[(),]", " ", n)) if w and not TITLE.match(w) and not re.match(r"^[A-Z]\.?$", w)]
    return 0 < len(t) <= 3 and all(JP.match(w.replace("'", "").replace("-", "")) for w in t) and any(len(w) >= 4 for w in t)


def load_map():
    m = {}
    if os.path.exists(TSV):
        for line in open(TSV, encoding="utf-8"):
            if line.startswith("#") or "\t" not in line: continue
            a, b = line.rstrip("\n").split("\t", 1); m[a] = b
    return m


def all_names():
    names = {}
    for f in glob.glob(os.path.join(WEB, "json", "*.json")) + glob.glob(os.path.join(WEB, "json-merged", "*", "s*.json")):
        try: d = json.load(open(f, encoding="utf-8"))
        except ValueError: continue
        for p in d.get("people", []):
            n = (p.get("name") or "").strip()
            if n: names.setdefault(n, p.get("role", ""))
    return names


PROMPT = """You are normalising personal names in a catalogue of U.S. State Department records on Japan, 1930-1949.
For each numbered name below, decide whether it is the name of a Japanese person (including Japanese living abroad and Japanese Americans whose family name is Japanese). If so, rewrite it surname-first: the FAMILY NAME in capital letters, then the given name(s) as written. Examples:
  "Hideki Tojo" -> "TOJO Hideki";  "Tojo Hideki" -> "TOJO Hideki";  "T. Matsui" -> "MATSUI T.";  "Koshimura" -> "KOSHIMURA";
  "Baron Kijuro Shidehara" -> "Baron SHIDEHARA Kijuro";  "George Kaneko" -> "KANEKO George";  "Kōki Hirota" -> "HIROTA Kōki".
Rules:
- Keep every letter, macron, hyphen and apostrophe exactly as given; do not correct, add or remove macrons, and do not change spellings.
- Keep a title or honorific (Baron, Prince, General, Mr., Dr., Admiral...) in front, unchanged.
- Only include a name if you are confident which part is the family name. Skip it if you are unsure, if it is a given name alone, if it is an emperor or other name used without a surname (Hirohito, Meiji), or if it is not a Japanese person (Chinese, Korean, Western, place or organisation names).
- Return only the names you change, as {"i": <number>, "name": "<normalised>"}. Return an empty list if none change.
The role given after each name is context only.
Do not use any tools, run any commands or read any files. Answer only from the list below.

"""
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["changes"], "properties": {"changes": {"type": "array", "items": {
    "type": "object", "additionalProperties": False, "required": ["i", "name"], "properties": {"i": {"type": "integer"}, "name": {"type": "string"}}}}}}


def ask(batch):
    sp = os.path.join(tempfile.gettempdir(), "people-names-schema.json"); json.dump(SCHEMA, open(sp, "w"))
    text = PROMPT + "\n".join(f"{i}. {n}" + (f"  (role: {r[:80]})" if r else "") for i, (n, r) in enumerate(batch, 1))
    p = subprocess.run(["agy", "-p", text, "--model", MODEL, "--output-format", "json", "--json-schema", sp,
                        "--print-timeout", "900s", "--disable-slash-commands"], capture_output=True, text=True, timeout=1000, cwd=tempfile.gettempdir())
    data = json.loads(p.stdout).get("structured_output")
    assert data and "changes" in data, p.stdout[-300:]
    out = {}
    for c in data["changes"]:
        i, nm = c.get("i"), (c.get("name") or "").strip()
        if not (isinstance(i, int) and 1 <= i <= len(batch)) or not nm: continue
        orig = batch[i - 1][0]
        # safety: the result must be the same words, only reordered and with the surname capitalised
        if sorted(w.lower() for w in nm.split()) != sorted(w.lower() for w in orig.split()): continue
        if nm != orig: out[orig] = nm
    return out


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "candidates":
        names = all_names(); done = load_map()
        todo = {n: r for n, r in names.items() if looks_japanese(n) and n not in done}
        json.dump(todo, open(TODO, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
        print(f"{len(names)} names, {len(todo)} Japanese-looking names still to check -> {TODO}")
    elif cmd == "run":
        workers = int(sys.argv[sys.argv.index("--workers") + 1]) if "--workers" in sys.argv else 2
        todo = list(json.load(open(TODO, encoding="utf-8")).items())
        batches = [todo[i:i + 300] for i in range(0, len(todo), 300)]
        def one(k):
            for attempt in range(3):
                try: return k, ask(batches[k]), None
                except Exception as e: err = e
            return k, {}, err
        with ThreadPoolExecutor(workers) as ex:
            for k, res, err in ex.map(one, range(len(batches))):
                with open(TSV, "a", encoding="utf-8") as fh:
                    if err: print(f"batch {k + 1}/{len(batches)} FAILED: {err!r}"[:300], flush=True); continue
                    changed = set(res)
                    for n, _ in batches[k]: fh.write(f"{n}\t{res.get(n, '')}\n")  # empty second column: checked, unchanged
                print(f"batch {k + 1}/{len(batches)}: {len(changed)} of {len(batches[k])} renamed", flush=True)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
