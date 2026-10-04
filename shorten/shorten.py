"""Shorten over-long summaries/interest/overview fields in json/ and json-merged/ (no re-reading of documents).
  python3 shorten/shorten.py next <A|B> [n]   -> writes shorten/<A|B>-next.json (pending items, up to n)
  python3 shorten/shorten.py apply <A|B>      -> applies shorten/<A|B>-round.jsonl ({"id","text"} per line)
  python3 shorten/shorten.py status
Originals are appended to shorten/originals.jsonl before any change."""
import json, os, sys, tempfile
H = os.path.dirname(os.path.abspath(__file__)); W = os.path.dirname(H)
os.chdir(W)
items = {i["id"]: i for i in json.load(open(os.path.join(H, "worklist.json")))}
W_ = lambda s: len((s or "").split())
def applied(a):
    p = os.path.join(H, f"{a}-applied.txt")
    return set(open(p).read().split("\n")) if os.path.exists(p) else set()
def get(d, path):
    if path in ("interest", "overview"): return d, path
    _, k = path.split("."); return d["subfiles"][int(k)], "summary"
cmd, a = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else None)
if cmd == "status":
    for a in "AB":
        mine = [i for i in items.values() if i["agent"] == a]; print(a, len(applied(a) & {i["id"] for i in mine}), "/", len(mine))
elif cmd == "next":
    n = int(sys.argv[3]) if len(sys.argv) > 3 else 150; done = applied(a)
    todo = [{k: i[k] for k in ("id", "pages", "limit", "form_limit", "doc_type", "title", "text") if k in i} for i in items.values() if i["agent"] == a and i["id"] not in done][:n]
    json.dump(todo, open(os.path.join(H, f"{a}-next.json"), "w"), ensure_ascii=False, indent=1)
    print(len(todo), "items written to", f"shorten/{a}-next.json;", sum(1 for i in items.values() if i["agent"] == a and i["id"] not in done), "pending in total")
elif cmd == "apply":
    rows, bad, kept = [], [], []
    for ln in open(os.path.join(H, f"{a}-round.jsonl"), encoding="utf-8"):
        if not ln.strip(): continue
        r = json.loads(ln); it = items.get(r["id"])
        if not it or it["agent"] != a: bad.append((r.get("id"), "unknown id / not yours")); continue
        if r.get("keep"):
            if W_(it["text"]) > it["limit"]: bad.append((r["id"], f"keep not allowed: original {W_(it['text'])} words > {it['limit']}")); continue
            kept.append(r["id"]); continue
        t = " ".join(r["text"].split())
        if not t: bad.append((r["id"], "empty")); continue
        if W_(t) > it["limit"]: bad.append((r["id"], f"{W_(t)} words > {it['limit']}")); continue
        rows.append((it, t))
    by = {}
    for it, t in rows: by.setdefault(it["src"], []).append((it, t))
    ok = []
    with open(os.path.join(H, "originals.jsonl"), "a", encoding="utf-8") as orig:
        for src, lst in by.items():
            d = json.load(open(src, encoding="utf-8"))
            for it, t in lst:
                o, f = get(d, it["path"])
                if " ".join((o.get(f) or "").split()) != " ".join(it["text"].split()): bad.append((it["id"], "source changed since worklist; skipped")); continue
                orig.write(json.dumps({"id": it["id"], "text": o.get(f)}, ensure_ascii=False) + "\n"); o[f] = t; ok.append(it["id"])
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(src)); 
            with os.fdopen(fd, "w", encoding="utf-8") as fh: json.dump(d, fh, ensure_ascii=False, indent=1)
            os.replace(tmp, src)
    with open(os.path.join(H, f"{a}-applied.txt"), "a") as fh:
        for i in ok + kept: fh.write(i + "\n")
    print(len(ok), "applied;", len(kept), "kept unchanged;", len(bad), "rejected")
    for b in bad: print("  REJECTED", *b)
