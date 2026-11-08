"""Verify a Track C `c.lexical` or `c.hybrid` output (spec/track_c.md). Fail-closed.

`--reference` is data/reference/track_c/{scale}/bm25_top10.npy (bm25) or hybrid_top10.npy (hybrid).

- bm25: structure + filter; **tie-aware BM25 recall@10 ≥ 0.95 per filter class** against the reference
  BM25 scores (a returned row counts when its reference score reaches the reference's 10th score within
  `BM25_TOL`, so equal-score rows and Tantivy's 1-byte length norms do not fail a correct engine).
- hybrid: the output plus the two legs the contestant fused (`<out>.dense.npy`, `<out>.lex.npy`, int32
  (Q, 100) each): structure + filter for all three; dense leg recall@10 ≥ 0.95 per class vs exact search;
  lexical leg quality ≥ `LEX_FLOOR` × the reference BM25 quality (the engine's own analyzer and ranking
  function are part of what is compared, so only a sanity floor); fused top-10 = RRF(k=60) of the legs,
  tie-aware, mean agreement ≥ 0.95 (engines differ in rank origin 0/1).
Last stdout line: JSON {"passed", "detail", "quality": {...}}; exit 0 only when passed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

from zj_eval.track_c import REPO, Context, headline, load_context, per_class, recall
from zj_eval.track_c_lex import quality_ext, query_texts
from zj_eval.verify_track_c import structure
from zj_reference import track_c as ref
from zj_reference import track_c_lex as lex

MIN_RECALL = 0.95
BM25_TOL = 0.02  # relative; Tantivy stores document length in one byte (exact up to 40 tokens)
LEX_FLOOR = 0.5
MIN_FUSION = 0.95


def sidecar(output: Path, leg: str) -> Path:
    return output.with_name(f"{output.stem}.{leg}{output.suffix}")


def _load(path: Path, width: int, ctx: Context) -> tuple[np.ndarray | None, list[str]]:
    try:
        a = np.load(path, allow_pickle=False)
    except Exception as e:
        return None, [f"cannot read {path.name}: {e}"]
    shape = (len(ctx.queries), width)
    if a.dtype != np.int32 or a.shape != shape:
        return None, [f"{path.name}: {a.dtype} {a.shape}, expected int32 {shape}"]
    return a, [f"{path.name}: {p}" for p in structure(a, ctx)]


def bm25_recall(ctx: Context, top: np.ndarray, idx: lex.Bm25Index) -> np.ndarray:
    an = lex.Analyzer()
    out = np.ones(len(top))
    for j, text in enumerate(query_texts(ctx)):
        s = idx.scores(an(text))
        cand = np.flatnonzero(ctx.masks[j] & (s > 0))
        need = min(ref.K, len(cand))
        if need == 0:
            out[j] = 1.0 if (top[j] < 0).all() else 0.0
            continue
        kth = np.sort(s[cand])[::-1][need - 1]
        got = top[j][top[j] >= 0]
        out[j] = min(1.0, float(((s[got] > 0) & (s[got] >= kth * (1 - BM25_TOL))).sum()) / need)
    return out


def fusion_agreement(fused: np.ndarray, dense: np.ndarray, lexical: np.ndarray) -> np.ndarray:
    out = np.ones(len(fused))
    for j in range(len(fused)):
        best, score = lex.rrf([dense[j], lexical[j]], ref.K)
        need = int((best >= 0).sum())
        if need == 0:
            out[j] = 1.0 if (fused[j] < 0).all() else 0.0
            continue
        kth = score[int(best[need - 1])]
        got = [int(r) for r in fused[j] if r >= 0]
        out[j] = min(1.0, sum(1 for r in got if score.get(r, 0.0) >= kth - 1e-12) / need)
    return out


def _flat(q: dict[str, dict[str, float]], prefix: str = "") -> dict[str, float]:
    return {f"{prefix}{k}": v["all"] for k, v in q.items()} | {
        f"{prefix}{k}_{sub}": v[sub] for k, v in q.items() for sub in ("kompositum",) if sub in v
    }


def check(output: Path, reference: Path, mode: str) -> dict[str, Any]:
    scale = reference.parent.name
    repo = reference.resolve().parents[4] if reference.is_absolute() else REPO
    ctx = load_context(scale, repo)
    rdir = reference.parent
    top, problems = _load(output, ref.K, ctx)
    if top is None:
        return {"passed": False, "detail": "; ".join(problems)}
    qual = quality_ext(ctx, top)
    flat = _flat(qual)
    res: dict[str, Any] = {"quality_detail": qual}
    if mode == "bm25":
        rec = per_class(ctx, bm25_recall(ctx, top, lex.Bm25Index.load(rdir / "bm25_index.npz")))
        low = {c: v for c, v in rec.items() if c != "all" and v < MIN_RECALL}
        if low:
            problems.append(f"tie-aware BM25 recall@10 < {MIN_RECALL} in {low}")
        flat["bm25_recall_at_10"] = rec["all"]
        res["bm25_recall_at_10"] = rec
    else:
        dense, p1 = _load(sidecar(output, "dense"), lex.DEPTH, ctx)
        lexical, p2 = _load(sidecar(output, "lex"), lex.DEPTH, ctx)
        problems += p1 + p2
        if dense is not None and lexical is not None:
            exact = np.load(rdir / "exact_top10.npy", allow_pickle=False)
            rec = per_class(ctx, recall(dense[:, : ref.K], exact))
            low = {c: v for c, v in rec.items() if c != "all" and v < MIN_RECALL}
            if low:
                problems.append(f"dense leg recall@10 < {MIN_RECALL} in {low}")
            lq = quality_ext(ctx, lexical[:, : ref.K])
            ref_q = json.loads((rdir / "quality_lex.json").read_text("utf-8"))["quality"]["bm25"]
            name, have = headline(lq)
            floor = round(LEX_FLOOR * ref_q[name]["all"], 4)
            if have < floor:
                problems.append(f"lexical leg {name} {have} < floor {floor}")
            agree = per_class(ctx, fusion_agreement(top, dense, lexical))
            if agree["all"] < MIN_FUSION:
                problems.append(f"fused top-10 ≠ RRF(k={lex.RRF_K}) of the legs: agreement {agree['all']}")
            flat |= {"dense_recall_at_10": rec["all"], "fusion_agreement": agree["all"]}
            flat |= _flat(lq, "lex_") | _flat(quality_ext(ctx, dense[:, : ref.K]), "dense_")
            res |= {"dense_recall_at_10": rec, "fusion_agreement": agree, "lex_quality": lq}
    passed = not problems
    detail = ", ".join(f"{k} {v}" for k, v in flat.items()) if passed else "; ".join(problems[:8])
    return {"passed": passed, "detail": detail, "quality": flat, **res}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m zj_eval.verify_track_c_lex")
    p.add_argument("--mode", choices=["bm25", "hybrid"], required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--reference", type=Path, required=True)
    a = p.parse_args(argv)
    res = check(a.output, a.reference, a.mode)
    print(json.dumps(res))
    return 0 if res["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
