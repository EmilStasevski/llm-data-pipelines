"""Track C lexical/hybrid inputs, reference and quality (spec/track_c.md §c.lexical, §c.hybrid).

`python -m zj_eval.track_c_lex reference --scale M|real` writes
- input  data/track_c/{scale}/queries.text.jsonl (qid + query text, same order as queries.jsonl);
- data/reference/track_c/{scale}/bm25_index.npz (analysed corpus), bm25_top10.npy, dense_top100.npy,
  bm25_top100.npy, hybrid_top10.npy and quality_lex.json (BM25 / dense / hybrid quality per filter class
  and for the compound-word subset).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import duckdb
import numpy as np

from zj_eval.track_c import REPO, Context, load_context, ndcg, quality
from zj_reference import track_c as ref
from zj_reference import track_c_lex as lex


def doc_texts(scale: str, repo: Path = REPO) -> list[str]:
    docs = repo / "data" / "track_c" / scale / "docs.parquet"
    rows = (
        duckdb.connect()
        .execute(
            f"SELECT doc_text FROM read_parquet('{docs}', file_row_number = true) ORDER BY file_row_number"
        )
        .fetchall()
    )
    return [str(r[0] or "") for r in rows]


def query_texts(ctx: Context) -> list[str]:
    return [str(q["text"]) for q in ctx.qrels]


def subsets(ctx: Context) -> dict[str, np.ndarray]:
    """Extra query groups beyond the filter classes: the compound-word subset (synthetic scales only)."""
    if "known_item" in ctx.qrels[0]:
        return {}
    comp = np.array([lex.is_compound(t) for t in query_texts(ctx)])
    return {"kompositum": comp, "getrennt": ~comp}


def quality_ext(ctx: Context, top: np.ndarray) -> dict[str, dict[str, float]]:
    """`quality()` per filter class plus the compound-word subsets."""
    q = quality(ctx, top)
    groups = subsets(ctx)
    if groups:
        v = ndcg(ctx, top)
        for name, m in groups.items():
            q["ndcg_at_10"][name] = round(float(v[m].mean()), 4)
    return q


def bm25_all(idx: lex.Bm25Index, texts: Sequence[str], masks: np.ndarray, k: int) -> np.ndarray:
    an = lex.Analyzer()
    return np.stack([lex.topk(idx.scores(an(t)), masks[j], k) for j, t in enumerate(texts)])


def fuse(dense: np.ndarray, lexical: np.ndarray, k: int = ref.K) -> np.ndarray:
    return np.stack([lex.rrf([dense[j], lexical[j]], k)[0] for j in range(len(dense))])


def write_texts(ctx: Context, repo: Path = REPO) -> Path:
    p = repo / "data" / "track_c" / ctx.scale / "queries.text.jsonl"
    p.write_text("".join(json.dumps({"qid": q["qid"], "text": q["text"]}, ensure_ascii=False) + "\n"
                         for q in ctx.qrels), encoding="utf-8")  # fmt: skip
    return p


def build_reference(scale: str, repo: Path = REPO) -> dict[str, Any]:
    ctx = load_context(scale, repo)
    write_texts(ctx, repo)
    out = repo / "data" / "reference" / "track_c" / scale
    idx = lex.build_index(doc_texts(scale, repo))
    idx.save(out / "bm25_index.npz")
    texts = query_texts(ctx)
    bm100 = bm25_all(idx, texts, ctx.masks, lex.DEPTH)
    docs = np.load(repo / "data" / "reference" / "track_b" / scale / "bge-m3.f32.npy", mmap_mode="r")
    qv = np.load(repo / "data" / "track_c" / scale / "queries.f32.npy")
    dense100 = ref.exact_topk(docs, qv, ctx.masks, lex.DEPTH)
    hybrid = fuse(dense100, bm100)
    for name, arr in (("bm25_top100", bm100), ("bm25_top10", bm100[:, : ref.K]),
                      ("dense_top100", dense100), ("hybrid_top10", hybrid)):  # fmt: skip
        np.save(out / f"{name}.npy", arr.astype(np.int32), allow_pickle=False)
    info: dict[str, Any] = {
        "scale": scale, "docs": idx.n, "vocab": len(idx.vocab), "avgdl": round(idx.avgdl, 3),
        "analyzer": "tantivy-equivalent: split non-alnum, <40 bytes, lowercase, snowball german 3.x",
        "bm25": {"k1": lex.K1, "b": lex.B}, "rrf_k": lex.RRF_K, "depth": lex.DEPTH,
        "compound_queries": int(subsets(ctx).get("kompositum", np.zeros(0)).sum()),
        "bm25_empty_queries": int((bm100[:, 0] < 0).sum()),
        "quality": {"bm25": quality_ext(ctx, bm100[:, : ref.K]),
                    "dense": quality_ext(ctx, dense100[:, : ref.K]), "hybrid": quality_ext(ctx, hybrid)},
    }  # fmt: skip
    (out / "quality_lex.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    return info


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m zj_eval.track_c_lex")
    p.add_argument("what", choices=["reference"])
    p.add_argument("--scale", required=True)
    a = p.parse_args(argv)
    print(json.dumps(build_reference(a.scale)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
