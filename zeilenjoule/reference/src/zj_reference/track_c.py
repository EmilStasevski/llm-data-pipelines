"""Track C reference: exact filtered top-k by brute force (numpy fp32), spec/track_c.md.

Slow and obviously correct: every query scores every document (inner product), documents outside the
query's metadata filter are dropped, ties are broken by the lower row index. Used for
- `data/reference/track_c/{scale}/exact_top10.npy` (ground truth for c.dense recall), and
- the recall part of the Track B gate (zj_eval.verify_track_b).
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import duckdb
import numpy as np

DIM = 1024
K = 10
FILTER_KEYS = ("ort", "bezirk", "date_from", "date_to")
EPOCH = dt.date(1970, 1, 1)
DOC_CHUNK = 16384


@dataclass(frozen=True)
class Meta:
    """Filterable metadata of docs.parquet, row-aligned."""

    ort: np.ndarray  # object (str)
    bezirk: np.ndarray  # object (str | None)
    datum: np.ndarray  # int32 days since 1970-01-01, valid only where has_datum
    has_datum: np.ndarray  # bool

    @property
    def n(self) -> int:
        return len(self.ort)


def load_meta(docs: Path) -> Meta:
    rows = (
        duckdb.connect()
        .execute(
            "SELECT ort, bezirk, datum - DATE '1970-01-01' AS d "
            f"FROM read_parquet('{docs}', file_row_number = true) "
            "ORDER BY file_row_number"
        )
        .fetchall()
    )
    ort = np.array([r[0] for r in rows], dtype=object)
    bezirk = np.array([r[1] for r in rows], dtype=object)
    has = np.array([r[2] is not None for r in rows], dtype=bool)
    datum = np.array([r[2] if r[2] is not None else 0 for r in rows], dtype=np.int32)
    return Meta(ort, bezirk, datum, has)


def read_queries(path: Path) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            q = json.loads(line)
            bad = set(q["filter"]) - set(FILTER_KEYS)
            if bad:
                raise ValueError(f"{q['qid']}: unknown filter keys {sorted(bad)}")
            out.append(q)
    return out


def _days(iso: str) -> int:
    return (dt.date.fromisoformat(iso) - EPOCH).days


def filter_mask(meta: Meta, flt: Mapping[str, str]) -> np.ndarray:
    """Rows passing the filter (spec/track_c.md: AND of equalities / inclusive date bounds; null date: no)."""
    m = np.ones(meta.n, dtype=bool)
    if "ort" in flt:
        m &= meta.ort == flt["ort"]
    if "bezirk" in flt:
        m &= meta.bezirk == flt["bezirk"]
    if "date_from" in flt or "date_to" in flt:
        m &= meta.has_datum
        if "date_from" in flt:
            m &= meta.datum >= _days(flt["date_from"])
        if "date_to" in flt:
            m &= meta.datum <= _days(flt["date_to"])
    return m


def exact_topk(
    docs: np.ndarray, queries: np.ndarray, masks: Sequence[np.ndarray] | np.ndarray, k: int = K
) -> np.ndarray:
    """int32 (Q, k) row indices, best first, -1 padded; scores in float32, ties → lower index."""
    q = np.ascontiguousarray(queries, dtype=np.float32)
    nq = q.shape[0]
    best_s = np.full((nq, k), -np.inf, dtype=np.float32)
    best_i = np.full((nq, k), -1, dtype=np.int64)
    mask_all = np.asarray(masks, dtype=bool)
    for s in range(0, docs.shape[0], DOC_CHUNK):
        d = np.asarray(docs[s : s + DOC_CHUNK], dtype=np.float32)
        sc = q @ d.T
        sc[~mask_all[:, s : s + len(d)]] = -np.inf
        if sc.shape[1] > k:  # pre-select the chunk's k best (exact float ties at the cut: arbitrary)
            part = np.argpartition(-sc, k - 1, axis=1)[:, :k]
            sc = np.take_along_axis(sc, part, axis=1)
            idx = part.astype(np.int64) + s
        else:
            idx = np.broadcast_to(np.arange(s, s + len(d), dtype=np.int64), sc.shape)
        cs = np.concatenate([best_s, sc], axis=1)
        ci = np.concatenate([best_i, idx], axis=1)
        # lexsort: primary -score, secondary index (stable, deterministic ties)
        order = np.lexsort((ci, -cs), axis=1)[:, :k]
        best_s = np.take_along_axis(cs, order, axis=1)
        best_i = np.take_along_axis(ci, order, axis=1)
    best_i[~np.isfinite(best_s)] = -1
    return best_i.astype(np.int32)


def masks_for(meta: Meta, queries: Sequence[Mapping[str, object]]) -> np.ndarray:
    return np.stack([filter_mask(meta, q["filter"]) for q in queries])  # type: ignore[arg-type]


def build(scale: str, repo: Path) -> dict[str, object]:
    """exact_top10.npy from the fp32 Track B reference vectors (the source of vectors.f16.npy)."""
    inp = repo / "data" / "track_c" / scale
    meta = load_meta(inp / "docs.parquet")
    queries = read_queries(inp / "queries.jsonl")
    docs = np.load(repo / "data" / "reference" / "track_b" / scale / "bge-m3.f32.npy", mmap_mode="r")
    qv = np.load(inp / "queries.f32.npy")
    if docs.shape != (meta.n, DIM) or qv.shape != (len(queries), DIM):
        raise ValueError(f"shape mismatch: docs {docs.shape}, meta {meta.n}, queries {qv.shape}")
    top = exact_topk(docs, qv, masks_for(meta, queries))
    out = repo / "data" / "reference" / "track_c" / scale
    out.mkdir(parents=True, exist_ok=True)
    np.save(out / "exact_top10.npy", top, allow_pickle=False)
    return {"scale": scale, "queries": len(queries), "docs": meta.n, "padded": int((top < 0).sum())}
