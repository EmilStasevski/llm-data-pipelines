"""Verify a Track C `c.dense` output (spec/track_c.md). Fail-closed.

`--reference` is data/reference/track_c/{scale}/exact_top10.npy; the scale's inputs are found from it.
The concurrency-16 result `<out>.c16.npy` must exist and passes the same checks.
Last stdout line: JSON {"passed", "detail", "quality": {...}, ...}; exit 0 only when passed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

from zj_eval.track_c import REPO, Context, load_context, per_class, quality, recall

MIN_RECALL = 0.95


def structure(top: np.ndarray, ctx: Context) -> list[str]:
    problems: list[str] = []
    n = ctx.meta.n
    if ((top < -1) | (top >= n)).any():
        problems.append(f"indices outside [-1, {n})")
        return problems
    for j, row in enumerate(top):
        valid = row[row >= 0]
        if len(valid) != len(set(valid.tolist())):
            problems.append(f"query {ctx.queries[j]['qid']}: duplicate rows")
        elif (row[: len(valid)] < 0).any():
            problems.append(f"query {ctx.queries[j]['qid']}: -1 before a result (padding must trail)")
        elif not ctx.masks[j][valid].all():
            problems.append(f"query {ctx.queries[j]['qid']}: returned rows outside the filter")
        if len(problems) >= 5:
            break
    return problems


def check(output: Path, reference: Path) -> dict[str, Any]:
    try:
        top = np.load(output, allow_pickle=False)
    except Exception as e:
        return {"passed": False, "detail": f"cannot read output: {e}"}
    scale = reference.parent.name
    repo = reference.resolve().parents[4] if reference.is_absolute() else REPO
    ctx = load_context(scale, repo)
    shape = (len(ctx.queries), 10)  # spec/track_c.md: 1 000 queries
    if top.dtype != np.int32 or top.shape != shape:
        return {"passed": False, "detail": f"{top.dtype} {top.shape}, expected int32 {shape}"}
    exact = np.load(reference, allow_pickle=False)
    problems = structure(top, ctx)
    rec = per_class(ctx, recall(top, exact))
    low = {c: v for c, v in rec.items() if c != "all" and v < MIN_RECALL}
    if low:
        problems.append(f"recall@10 < {MIN_RECALL} in {low}")
    # concurrency-16 pass (`<out>.c16.npy`, same queries from 16 client threads): same checks
    c16 = output.with_name(f"{output.stem}.c16{output.suffix}")
    rec16: dict[str, float] = {}
    try:
        top16 = np.load(c16, allow_pickle=False)
    except Exception as e:
        problems.append(f"concurrency-16 output missing/unreadable: {e}")
    else:
        if top16.dtype != np.int32 or top16.shape != shape:
            problems.append(f"{c16.name}: {top16.dtype} {top16.shape}, expected int32 {shape}")
        else:
            problems += [f"{c16.name}: {p}" for p in structure(top16, ctx)]
            rec16 = per_class(ctx, recall(top16, exact))
            low16 = {c: v for c, v in rec16.items() if c != "all" and v < MIN_RECALL}
            if low16:
                problems.append(f"{c16.name}: recall@10 < {MIN_RECALL} in {low16}")
    q = quality(ctx, top)
    passed = not problems
    detail = (f"recall@10 {rec} vs exact; " + "; ".join(f"{k} {v['all']}" for k, v in q.items())
              if passed else "; ".join(problems))  # fmt: skip
    flat: dict[str, Any] = {"recall_at_10": rec["all"], "recall_at_10_c16": rec16.get("all")}
    flat.update({k: v["all"] for k, v in q.items()})
    return {"passed": passed, "detail": detail, "quality": flat, "recall_at_10": rec, **q}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m zj_eval.verify_track_c")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--reference", type=Path, required=True)
    a = p.parse_args(argv)
    res = check(a.output, a.reference)
    print(json.dumps(res))
    return 0 if res["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
