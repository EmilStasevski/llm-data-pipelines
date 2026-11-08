"""Verify a Track B `b.embed` output against the fp32 reference (spec/track_b.md). Fail-closed.

Last stdout line: JSON {"passed": bool, "detail": str, ...}; exit 0 only when passed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

DIM = 1024
MIN_MEAN_COS = 0.995
MAX_QUALITY_DELTA = 0.01  # spec/track_b.md §3 / spec/track_c.md: within 1 point of the reference
NORM_TOL = 0.01
CHUNK = 65536


def check(output: Path, reference: Path) -> dict[str, Any]:
    try:
        out = np.load(output, mmap_mode="r", allow_pickle=False)
    except Exception as e:
        return {"passed": False, "detail": f"cannot read output: {e}"}
    ref = np.load(reference, mmap_mode="r", allow_pickle=False)
    if out.dtype != np.float16:
        return {"passed": False, "detail": f"dtype {out.dtype}, expected float16"}
    if out.shape != (ref.shape[0], DIM):
        return {"passed": False, "detail": f"shape {out.shape}, expected {(ref.shape[0], DIM)}"}
    cos = np.empty(out.shape[0], dtype=np.float64)
    bad_norm = non_finite = 0
    for s in range(0, out.shape[0], CHUNK):
        o = np.asarray(out[s : s + CHUNK], dtype=np.float32)
        r = np.asarray(ref[s : s + CHUNK], dtype=np.float32)
        finite = np.isfinite(o).all(axis=1)
        non_finite += int((~finite).sum())
        n = np.linalg.norm(o, axis=1)
        bad_norm += int((np.abs(n - 1) > NORM_TOL).sum())
        rn = np.linalg.norm(r, axis=1)
        cos[s : s + len(o)] = (o * r).sum(axis=1) / np.maximum(n * rn, 1e-12)
    stats = {
        "mean_cos": round(float(cos.mean()), 6),
        "min_cos": round(float(cos.min()), 6),
        "p01_cos": round(float(np.quantile(cos, 0.01)), 6),
        "non_finite_rows": non_finite,
        "bad_norm_rows": bad_norm,
    }
    problems: list[str] = []
    if non_finite:
        problems.append(f"{non_finite} rows with NaN/Inf")
    if bad_norm:
        problems.append(f"{bad_norm} rows with |norm-1| > {NORM_TOL}")
    if stats["mean_cos"] < MIN_MEAN_COS:
        problems.append(f"mean cosine {stats['mean_cos']} < {MIN_MEAN_COS}")
    gate = retrieval_gate(out, reference) if not problems else None
    quality: dict[str, Any] = {}
    if gate is None:
        recall_note = "not checked (no Track C queries at this scale, or vectors already failed)"
    else:
        name, cand, ref_value, rec = gate
        quality = {name: cand, "recall_at_10": rec}
        recall_note = f"{name} {cand} vs reference {ref_value} (recall@10 vs exact {rec})"
        if abs(cand - ref_value) > MAX_QUALITY_DELTA:
            problems.append(f"{name} {cand} differs from the reference {ref_value} by > {MAX_QUALITY_DELTA}")
    passed = not problems
    detail = (
        f"{out.shape[0]} vectors, mean cos {stats['mean_cos']} (min {stats['min_cos']}) vs reference; "
        f"retrieval: {recall_note}"
        if passed
        else "; ".join(problems)
    )
    return {"passed": passed, "detail": detail, "quality": quality, **stats, "recall_check": recall_note}


def retrieval_gate(out: np.ndarray, reference: Path) -> tuple[str, float, float, float] | None:
    """Exact filtered top-10 with the candidate's document vectors and the reference query vectors, scored
    with the scale's Track C metric; None when the scale has no Track C queries (e.g. S)."""
    from zj_eval import track_c  # noqa: PLC0415
    from zj_reference import track_c as ref  # noqa: PLC0415

    scale = reference.parent.name
    repo = reference.resolve().parents[4] if reference.is_absolute() else track_c.REPO
    inp = repo / "data" / "track_c" / scale
    refq = repo / "data" / "reference" / "track_c" / scale
    if not (inp / "queries.f32.npy").exists() or not (refq / "quality.json").exists():
        return None
    ctx = track_c.load_context(scale, repo)
    if ctx.meta.n != out.shape[0]:
        return None
    top = ref.exact_topk(out, np.load(inp / "queries.f32.npy"), ctx.masks)
    name, value = track_c.headline(track_c.quality(ctx, top))
    ref_name, ref_value = track_c.headline(json.loads((refq / "quality.json").read_text("utf-8"))["quality"])
    if ref_name != name:
        raise ValueError(f"metric mismatch {name} vs {ref_name}")
    rec = round(float(track_c.recall(top, np.load(refq / "exact_top10.npy")).mean()), 4)
    return name, value, ref_value, rec


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m zj_eval.verify_track_b")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--reference", type=Path, required=True)
    a = p.parse_args(argv)
    res = check(a.output, a.reference)
    print(json.dumps(res))
    return 0 if res["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
