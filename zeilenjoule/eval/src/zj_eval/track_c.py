"""Track C quality metrics + reference build (spec/track_c.md).

`python -m zj_eval.track_c reference --scale M|real` → data/reference/track_c/{scale}/:
exact_top10.npy (zj_reference.track_c), labels.parquet (synthetic gold labels per row) and quality.json
(retrieval quality of the exact top-10 = dense ceiling).

Metrics per query:
- synthetic (spec/qrels_c.<scale>.jsonl): nDCG@10 with the graded rule of zj_eval.relevance (gain 2^g - 1,
  log2 discount; the ideal ranking uses all grade-2/grade-1 rows inside the filter);
- real (spec/queries_c.real.jsonl, known item): hit@10 and MRR@10.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import duckdb
import numpy as np
import yaml

from zj_reference import track_c as ref

REPO = Path(__file__).resolve().parents[3]
QRELS = {"real": "spec/queries_c.real.jsonl"}
DISCOUNT = 1.0 / np.log2(np.arange(2, ref.K + 2))


def qrels_path(scale: str) -> Path:
    return REPO / QRELS.get(scale, f"spec/qrels_c.{scale}.jsonl")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


@dataclass(frozen=True)
class Context:
    """Everything needed to score a (Q, 10) result at one scale."""

    scale: str
    meta: ref.Meta
    queries: list[dict[str, Any]]  # data/track_c/{scale}/queries.jsonl
    qrels: list[dict[str, Any]]  # spec file, same order
    masks: np.ndarray  # (Q, N) bool
    ids: list[str]
    labels: tuple[np.ndarray, np.ndarray, np.ndarray] | None  # komponente, schadensart, eq (synthetic)

    @property
    def classes(self) -> np.ndarray:
        return np.array([q["filter_class"] for q in self.queries], dtype=object)


def load_context(scale: str, repo: Path = REPO) -> Context:
    inp = repo / "data" / "track_c" / scale
    meta = ref.load_meta(inp / "docs.parquet")
    queries = cast(list[dict[str, Any]], ref.read_queries(inp / "queries.jsonl"))
    qrels = read_jsonl(repo / QRELS.get(scale, f"spec/qrels_c.{scale}.jsonl"))
    if [q["qid"] for q in qrels] != [q["qid"] for q in queries]:
        raise ValueError(f"{scale}: queries.jsonl and the qrels file disagree on query order")
    ids = [str(r[0]) for r in duckdb.connect().execute(
        f"SELECT id FROM read_parquet('{inp / 'docs.parquet'}', file_row_number = true) "
        "ORDER BY file_row_number"
    ).fetchall()]  # fmt: skip
    labels = None
    lp = repo / "data" / "reference" / "track_c" / scale / "labels.parquet"
    if lp.exists():
        rows = (
            duckdb.connect()
            .execute(f"SELECT komponente, schadensart, eq FROM read_parquet('{lp}')")
            .fetchall()
        )
        labels = tuple(np.array([r[i] for r in rows], dtype=object) for i in range(3))
    return Context(scale, meta, queries, qrels, ref.masks_for(meta, queries), ids,
                   cast(tuple[np.ndarray, np.ndarray, np.ndarray] | None, labels))  # fmt: skip


def ndcg(ctx: Context, top: np.ndarray) -> np.ndarray:
    if ctx.labels is None:
        raise ValueError(f"{ctx.scale}: labels.parquet missing (run `task trackC:reference`)")
    komp, schad, eq = ctx.labels
    eq_of = dict(zip(komp.tolist(), eq.tolist(), strict=True))
    out = np.zeros(len(ctx.qrels))
    for j, q in enumerate(ctx.qrels):
        k, s = q["komponente"], q["schadensart"]
        m = ctx.masks[j]
        g2 = m & (komp == k) & (schad == s)
        g1 = m & ~g2 & ((komp == k) | ((schad == s) & (eq == eq_of[k])))
        grade = g2.astype(np.int8) * 2 + g1.astype(np.int8)
        row = top[j]
        gains = np.where(row >= 0, (2.0 ** grade[np.maximum(row, 0)]) - 1, 0.0)
        n2, n1 = int(g2.sum()), int(g1.sum())
        ideal = np.array([3.0] * min(n2, ref.K) + [1.0] * max(0, min(n1, ref.K - n2)) + [0.0] * ref.K)[
            : ref.K
        ]
        idcg = float((ideal * DISCOUNT).sum())
        out[j] = float((gains * DISCOUNT).sum()) / idcg if idcg > 0 else 0.0
    return out


def known_item(ctx: Context, top: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    pos = {d: i for i, d in enumerate(ctx.ids)}
    hit = np.zeros(len(ctx.qrels))
    rr = np.zeros(len(ctx.qrels))
    for j, q in enumerate(ctx.qrels):
        target = pos[q["known_item"]]
        where = np.nonzero(top[j] == target)[0]
        if len(where):
            hit[j], rr[j] = 1.0, 1.0 / (int(where[0]) + 1)
    return hit, rr


def per_class(ctx: Context, values: np.ndarray) -> dict[str, float]:
    cls = ctx.classes
    out = {"all": round(float(values.mean()), 4)}
    for c in dict.fromkeys(cls.tolist()):
        out[str(c)] = round(float(values[cls == c].mean()), 4)
    return out


def quality(ctx: Context, top: np.ndarray) -> dict[str, dict[str, float]]:
    """Retrieval quality per filter class: ndcg_at_10 (synthetic) or hit_at_10 + mrr_at_10 (real)."""
    if "known_item" in ctx.qrels[0]:
        hit, rr = known_item(ctx, top)
        return {"hit_at_10": per_class(ctx, hit), "mrr_at_10": per_class(ctx, rr)}
    return {"ndcg_at_10": per_class(ctx, ndcg(ctx, top))}


def headline(q: dict[str, dict[str, float]]) -> tuple[str, float]:
    """The scale's single quality number (used by the Track B gate)."""
    name = "hit_at_10" if "hit_at_10" in q else "ndcg_at_10"
    return name, q[name]["all"]


def recall(top: np.ndarray, exact: np.ndarray) -> np.ndarray:
    """Per query |top ∩ exact| / |exact| (exact is -1 padded when < 10 rows match)."""
    out = np.ones(len(exact))
    for j in range(len(exact)):
        e = set(exact[j][exact[j] >= 0].tolist())
        if e:
            out[j] = len(e & set(top[j].tolist())) / len(e)
    return out


def build_labels(scale: str, repo: Path) -> int:
    """labels.parquet (synthetic scales): gold komponente / schadensart / equipment group per docs row."""
    comps = cast(dict[str, Any], yaml.safe_load((repo / "spec/lexicon/komponenten.yaml").read_text("utf-8")))
    eq = {str(k["name"]): str(k["eq"]) for k in comps["komponenten"]}
    con = duckdb.connect()
    con.execute("CREATE TABLE eq (name VARCHAR, eq VARCHAR)")
    con.executemany("INSERT INTO eq VALUES (?, ?)", list(eq.items()))
    docs = repo / "data" / "track_c" / scale / "docs.parquet"
    gold = repo / "data" / "synth" / "stoermeldungen" / scale / "stoermeldungen_gold.parquet"
    out = repo / "data" / "reference" / "track_c" / scale / "labels.parquet"
    con.execute(f"""
        COPY (SELECT g.komponente, g.schadensart, e.eq
              FROM (SELECT id, file_row_number AS rn FROM read_parquet('{docs}', file_row_number = true)) d
              JOIN read_parquet('{gold}') g ON g.meldung_id = d.id
              LEFT JOIN eq e ON e.name = g.komponente
              ORDER BY d.rn) TO '{out}' (FORMAT parquet)""")
    n = con.execute(f"SELECT count(*) FROM read_parquet('{out}')").fetchone()
    return int(n[0]) if n else 0


def build_reference(scale: str, repo: Path = REPO) -> dict[str, Any]:
    info: dict[str, Any] = ref.build(scale, repo)
    out = repo / "data" / "reference" / "track_c" / scale
    if scale != "real":
        info["labels_rows"] = build_labels(scale, repo)
        if info["labels_rows"] != info["docs"]:
            raise ValueError(f"labels.parquet has {info['labels_rows']} rows, docs {info['docs']}")
    ctx = load_context(scale, repo)
    top = np.load(out / "exact_top10.npy")
    info["quality"] = quality(ctx, top)
    (out / "quality.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    return info


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m zj_eval.track_c")
    p.add_argument("what", choices=["reference"])
    p.add_argument("--scale", required=True)
    a = p.parse_args(argv)
    print(json.dumps(build_reference(a.scale)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
