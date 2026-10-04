"""Catalogue each OCR'd PDF in Files/ with an LLM CLI, one file at a time.

  python catalog.py --backend agy|codex [--only N] [--out json]
Writes web/<out>/<identifier>.json; skips files already done.
"""
import os, re, csv, json, sys, time, argparse, subprocess, tempfile

WEB = os.path.dirname(os.path.abspath(__file__))
FILES = os.path.dirname(WEB)
ap = argparse.ArgumentParser()
ap.add_argument("--backend", default="agy")
ap.add_argument("--model", default=None)
ap.add_argument("--out", default="json")
ap.add_argument("--only", nargs="*", help="identifiers to do")
ap.add_argument("--timeout", type=int, default=900)
ap.add_argument("--reverse", action="store_true", help="work from the end of the list (for a second worker)")
a = ap.parse_args()

def strict(s):
    """OpenAI structured outputs need every object closed and every key required."""
    if isinstance(s, dict):
        if s.get("type") == "object":
            s["additionalProperties"] = False
            s["required"] = list(s["properties"])
        for v in s.values(): strict(v)
    elif isinstance(s, list):
        for v in s: strict(v)
    return s

schema = strict(json.load(open(os.path.join(WEB, "schema.json"))))
schema_path = os.path.join(tempfile.gettempdir(), "catalog-schema.json")
json.dump(schema, open(schema_path, "w"))
PROMPT = open(os.path.join(WEB, "prompt.txt")).read()
rows = list(csv.DictReader(open(os.path.join(FILES, "upload.csv"), encoding="utf-8")))
out_dir = os.path.join(WEB, a.out); os.makedirs(out_dir, exist_ok=True)
todo = [r for r in rows if (not a.only or r["identifier"] in a.only)
        and not os.path.exists(os.path.join(out_dir, r["identifier"] + ".json"))]
if a.reverse: todo.reverse()
print(f"{len(todo)} to do with {a.backend}{' (from the end)' if a.reverse else ''}", flush=True)
workdir = tempfile.mkdtemp(prefix="catalog-")  # empty dir: nothing for the agent to look at
CODEX_MODEL = next((l.split("=", 1)[1].strip().strip('"') for l in open(os.path.expanduser("~/.codex/config.toml"))
                    if re.match(r"\s*model\s*=", l)), "codex default") if os.path.exists(os.path.expanduser("~/.codex/config.toml")) else "codex default"
STATUS = os.path.join(WEB, "_status.txt"); USAGE_LOG = os.path.join(WEB, "usage.log")


def gemini_usage():
    """{'Weekly': (pct, reset), 'Five Hour': (pct, reset)} from `agy -p /usage`."""
    try:
        r = json.loads(subprocess.run(["agy", "-p", "/usage", "--output-format", "json"],
                                      capture_output=True, text=True, timeout=60).stdout)["response"]
    except Exception:
        return {}
    out = {}
    for line in r.splitlines():
        m = re.match(r"Gemini Models\t(Weekly|Five Hour) Limit Remaining\t(\d+)%\t(\S+)", line)
        if m: out[m.group(1)] = (int(m.group(2)), m.group(3))
    return out


def wait_for_quota(u):
    """Pause (do not give up) until the limit resets if Gemini quota is nearly used."""
    from datetime import datetime, timezone
    low = [(k, v) for k, v in u.items() if v[0] < 5]
    if not low: return
    reset = max(datetime.fromisoformat(v[1].replace("Z", "+00:00")) for _, v in low)
    secs = max(60, (reset - datetime.now(timezone.utc)).total_seconds() + 120)
    msg = f"quota low {low}; pausing until {reset.isoformat()} ({secs / 3600:.1f} h)"
    print(msg, flush=True); open(STATUS, "a").write(msg + "\n")
    time.sleep(secs)

usage = {}

done_n = len(rows) - len([r for r in rows if not os.path.exists(os.path.join(out_dir, r["identifier"] + ".json"))])
for k, r in enumerate(todo, 1):
    ident, pdf = r["identifier"], r["file"]
    # another worker may have done or started this file since the list was made
    dst = os.path.join(out_dir, ident + ".json"); lock = os.path.join(out_dir, "." + ident + ".lock")
    if os.path.exists(dst): continue
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY); os.write(fd, a.backend.encode()); os.close(fd)
    except FileExistsError:
        if time.time() - os.path.getmtime(lock) < 3 * 3600: continue
        os.utime(lock)  # stale lock from a killed run: take it over
    if a.backend == "agy" and (k - 1) % 10 == 0:
        usage = gemini_usage()
        open(USAGE_LOG, "a").write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\tfiles done {done_n}\t{json.dumps(usage)}\n")
        wait_for_quota(usage)
    text = open(os.path.join(FILES, "ocr", pdf[:-4] + ".txt"), encoding="utf-8").read()
    prompt = PROMPT.replace("{TITLE}", r["title"]).replace("{TEXT}", text)
    t = time.time()
    try:
        if a.backend == "agy":
            cmd = ["agy", "-p", prompt, "--model", a.model or "gemini-3.8-flash-medium", "--output-format", "json",
                   "--json-schema", schema_path, "--print-timeout", f"{a.timeout}s", "--disable-slash-commands"]
            p = subprocess.run(cmd, capture_output=True, text=True, cwd=workdir, timeout=a.timeout + 60)
            res = json.loads(p.stdout)
            data = res.get("structured_output")
            meta = {"model": a.model or "gemini-3.8-flash-medium", "usage": res.get("usage")}
        else:
            last = os.path.join(workdir, "last.json")
            cmd = ["codex", "exec", "--skip-git-repo-check", "--ephemeral", "-s", "read-only",
                   "--output-schema", schema_path, "-o", last] + (["-m", a.model] if a.model else []) + ["-"]
            p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, cwd=workdir, timeout=a.timeout + 60)
            data = json.load(open(last)); os.remove(last)
            meta = {"model": a.model or CODEX_MODEL}
        assert data and data.get("subfiles") is not None, (p.stdout[-500:], p.stderr[-500:])
    except Exception as e:
        print(f"[{k}/{len(todo)}] ERROR {ident}: {e!r}"[:600], flush=True)
        open(os.path.join(WEB, "errors.log"), "a").write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{ident}\t{e!r}"[:1000] + "\n")
        os.path.exists(lock) and os.remove(lock)
        if a.backend == "agy": wait_for_quota(gemini_usage())
        else: time.sleep(300)  # codex: back off on errors (e.g. rate limits)
        continue
    data["_meta"] = dict(meta, backend=a.backend, seconds=round(time.time() - t, 1),
                         identifier=ident, file=pdf, created=time.strftime("%Y-%m-%d %H:%M:%S"))
    json.dump(data, open(dst + ".tmp", "w"), indent=1, ensure_ascii=False); os.replace(dst + ".tmp", dst)
    os.path.exists(lock) and os.remove(lock)
    done_n += 1
    if done_n % 20 == 0:
        subprocess.run([sys.executable, os.path.join(WEB, "build_db.py")], capture_output=True)
    print(f"[{k}/{len(todo)}] {time.time() - t:.0f}s {len(data['subfiles'])} sub-files {ident}", flush=True)
    open(STATUS if a.backend == "agy" else STATUS.replace(".txt", "-" + a.backend + ".txt"), "w").write(f"updated {time.strftime('%Y-%m-%d %H:%M:%S')}\n{sum(f.endswith('.json') for f in os.listdir(out_dir))} of {len(rows)} files done (all workers)\n"
                            f"last: {ident} ({time.time() - t:.0f}s, {len(data['subfiles'])} sub-files)\n"
                            f"Gemini quota left at last check: {usage}\n")
