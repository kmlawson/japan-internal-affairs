#!/bin/zsh
# Append the current Codex quota (same figures as /status) to codex-quota.log: one tiny saved session, then read
# rate_limits from its log. stdin must be closed, or codex exec waits for more prompt text.
HERE="${0:A:h}"
cd "${TMPDIR:-/tmp}"
codex exec --skip-git-repo-check -s read-only -m gpt-6-sol -c 'model_reasoning_effort="low"' "Reply: ok" < /dev/null > /dev/null 2>&1
f=$(ls -t ~/.codex/sessions/*/*/*/*.jsonl | head -1)
python3 - "$f" >>| "$HERE/codex-quota.log" <<'P'
import sys, json, datetime
def find(o):
    if isinstance(o, dict):
        if o.get("rate_limits"): return o["rate_limits"]
        for v in o.values():
            r = find(v)
            if r: return r
    if isinstance(o, list):
        for v in o:
            r = find(v)
            if r: return r
last = None
for l in open(sys.argv[1]):
    if '"rate_limits"' in l:
        try: r = find(json.loads(l))
        except ValueError: r = None
        if r: last = r
p = last["primary"]
print(f"{datetime.datetime.now():%Y-%m-%d %H:%M}  plan {last.get('plan_type')}: {p['used_percent']}% of the {p['window_minutes'] // 1440}-day limit used, "
      f"{100 - p['used_percent']:.0f}% left, resets {datetime.datetime.fromtimestamp(p['resets_at']):%a %d %b %H:%M}")
P
