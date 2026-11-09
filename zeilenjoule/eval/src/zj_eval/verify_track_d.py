"""Verify a Track D `d.extract` output (spec/track_d.md). Fail-closed.

Output = JSONL, one line per input row in input order: {"meldung_id", "text", "completion_tokens",
"finish_reason"} (+ optional fields). Gate: every row present in order, JSON validity ≥ 99.5 % (PLAN §10),
no reasoning output (no `<think>`, completion_tokens ≤ max_tokens). Quality (per-field scores, macro-F1) is
reported, not gated here: the winner gate (macro-F1 ≥ 0.9 × best) needs all runs (report).

Last stdout line: JSON {"passed", "detail", "quality", ...}; exit 0 only when passed.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

from zj_reference import track_d as ref

MIN_VALIDITY = 0.995


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in path.read_text("utf-8").splitlines() if x.strip()]


def check(output: Path, reference: Path) -> dict[str, Any]:
    try:
        out = _jsonl(output)
    except Exception as e:
        return {"passed": False, "detail": f"cannot read output: {e}"}
    gold = _jsonl(reference)
    ids = [str(o.get("meldung_id")) for o in out]
    if ids != [g["meldung_id"] for g in gold]:
        return {
            "passed": False,
            "detail": f"{len(out)} rows / ids or order differ from the {len(gold)} input rows",
        }
    texts = [str(o.get("text") or "") for o in out]
    preds = [ref.parse(t) for t in texts]
    q = ref.score(preds, gold)
    toks = [int(o["completion_tokens"]) for o in out if o.get("completion_tokens") is not None]
    think = sum("<think" in t for t in texts)
    over = sum(t > ref.MAX_TOKENS for t in toks)
    truncated = sum(o.get("finish_reason") == "length" for o in out)
    problems: list[str] = []
    if q["json_validity"] < MIN_VALIDITY:
        problems.append(f"JSON validity {q['json_validity']:.4f} < {MIN_VALIDITY}")
    if think:
        problems.append(f"{think} rows contain reasoning output (<think>)")
    if over:
        problems.append(f"{over} rows with completion_tokens > {ref.MAX_TOKENS} (reasoning not off?)")
    if len(toks) != len(out):
        problems.append(f"completion_tokens missing in {len(out) - len(toks)} rows")
    quality = {k: round(v, 4) for k, v in q.items()}
    stats = {"completion_tokens_median": statistics.median(toks) if toks else None,
             "completion_tokens_total": sum(toks), "finish_length": truncated,
             "invalid_rows": sum(p is None for p in preds)}  # fmt: skip
    if toks:  # in quality too, so the report can show tokens per row
        quality["completion_tokens_median"] = float(statistics.median(toks))
    passed = not problems
    detail = (f"{len(out)} rows, JSON validity {q['json_validity']:.4f}, macro-F1 {q['macro_f1']:.4f}"
              if passed else "; ".join(problems))  # fmt: skip
    return {"passed": passed, "detail": detail, "quality": quality, **stats}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m zj_eval.verify_track_d")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--reference", type=Path, required=True)
    a = p.parse_args(argv)
    res = check(a.output, a.reference)
    print(json.dumps(res, ensure_ascii=False))
    return 0 if res["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
