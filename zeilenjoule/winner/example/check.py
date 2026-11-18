"""Ask every question of fragen.json via POST /v1/ask (JSON) and check the answer (stdlib only).

zahl: a number in the answer equals the expected value (±0.01) · text: the answer contains the text ·
ids: at least one expected meldung_id is cited (search route) or appears in the answer (SQL route).
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any, cast


def flat(x: object) -> list[object]:
    if isinstance(x, dict):
        return [v for y in cast("dict[str, object]", x).values() for v in flat(y)]
    if isinstance(x, list):
        return [v for y in cast("list[object]", x) for v in flat(y)]
    return [x]


def numbers(vals: list[object]) -> list[float]:
    out: list[float] = []
    for v in vals:
        if isinstance(v, bool):
            continue
        if isinstance(v, int | float):
            out.append(float(v))
        elif isinstance(v, str):
            with contextlib.suppress(ValueError):
                out.append(float(v.replace(" ", "")))
    return out


def ask(base: str, key: str, q: str) -> dict[str, Any]:
    req = urllib.request.Request(
        base + "/v1/ask",
        data=json.dumps({"question": q}).encode(),
        headers={"X-API-Key": key, "Content-Type": "application/json", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.load(r)


def check(f: dict[str, Any], a: dict[str, Any]) -> bool:
    vals = flat(a.get("answer"))
    if "zahl" in f:
        return any(abs(n - f["zahl"]) <= 0.01 for n in numbers(vals))
    if "text" in f:
        return any(f["text"] in str(v) for v in vals)
    cited = set(a.get("cited_ids") or []) | {str(v) for v in vals}
    return bool(cited & set(f["ids"]))


def main() -> int:
    base, key = os.environ["ZJ_BASE"], os.environ["ZJ_KEY"]
    with open(sys.argv[1], encoding="utf-8") as fh:
        fragen = json.load(fh)
    ok = 0
    for i, f in enumerate(fragen, 1):
        t0 = time.time()
        try:
            a = ask(base, key, f["frage"])
        except urllib.error.HTTPError as e:
            a = {"route": "error", "answer": e.read().decode(errors="replace")[:200]}
        good = check(f, a)
        ok += good
        want = f.get("zahl", f.get("text", f.get("ids")))
        print(f"{'OK  ' if good else 'FAIL'} {i}. {f['frage']}")
        print(f"     route {a.get('route')}, {time.time() - t0:.1f} s, expected {want}")
        if a.get("sql"):
            print(f"     sql   {a['sql']}")
        if a.get("sql_status") not in (None, "executed"):
            print(f"     sql_status {a['sql_status']}: {a.get('rejected') or a.get('error') or ''}")
        if a.get("estimated"):
            print("     note  depends on the estimated column schadensart (filter or SQL)")
        print(f"     answer {json.dumps(a.get('answer'), ensure_ascii=False)[:300]}")
        if a.get("cited_ids"):
            print(f"     cited {', '.join(a['cited_ids'][:10])}")
    print(f"\n{ok}/{len(fragen)} answers match the expected values")
    return 0 if ok == len(fragen) else 1


if __name__ == "__main__":
    sys.exit(main())
