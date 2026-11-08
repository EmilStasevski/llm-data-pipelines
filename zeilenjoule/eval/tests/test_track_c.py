"""Track C reference search, metrics and verifier on a tiny synthetic scale (no real data needed)."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from zj_eval import track_c, verify_track_c
from zj_reference import track_c as ref

N, Q = 40, 4


def _unit(rng: np.random.Generator, n: int) -> np.ndarray:
    v = rng.standard_normal((n, ref.DIM)).astype(np.float32)
    return v / np.linalg.norm(v, axis=1, keepdims=True)


def _scale(tmp: Path) -> tuple[Path, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(7)
    inp = tmp / "data" / "track_c" / "T"
    inp.mkdir(parents=True)
    ort = ["Dortmund" if i % 4 == 0 else "Essen" for i in range(N)]
    datum = [None if i % 10 == 9 else dt.date(2025, 1, 1) + dt.timedelta(days=i) for i in range(N)]
    pq.write_table(pa.table({"id": [f"d{i}" for i in range(N)], "ort": ort, "bezirk": ["Arnsberg"] * N,  # pyright: ignore[reportUnknownMemberType]
                             "datum": pa.array(datum, pa.date32()), "doc_text": ["x"] * N}),
                   inp / "docs.parquet")  # fmt: skip
    queries: list[dict[str, Any]] = [
        {"qid": "q1", "filter_class": "none", "filter": {}},
        {"qid": "q2", "filter_class": "werk", "filter": {"ort": "Dortmund"}},
        {
            "qid": "q3",
            "filter_class": "datum",
            "filter": {"date_from": "2025-01-05", "date_to": "2025-01-20"},
        },
        {"qid": "q4", "filter_class": "kombiniert", "filter": {"ort": "Dortmund", "date_to": "2025-01-09"}},
    ]
    (inp / "queries.jsonl").write_text("".join(json.dumps(q) + "\n" for q in queries), encoding="utf-8")
    docs, qv = _unit(rng, N), _unit(rng, Q)
    np.save(inp / "queries.f32.npy", qv)
    return inp, docs, qv


def _naive(docs: np.ndarray, qv: np.ndarray, masks: np.ndarray) -> np.ndarray:
    out = np.full((len(qv), ref.K), -1, dtype=np.int32)
    for j in range(len(qv)):
        cand = [(-float(docs[i] @ qv[j]), i) for i in range(len(docs)) if masks[j, i]]
        top = [i for _, i in sorted(cand)[: ref.K]]
        out[j, : len(top)] = top
    return out


def test_exact_topk_matches_naive_and_respects_filters(tmp_path: Path) -> None:
    inp, docs, qv = _scale(tmp_path)
    meta = ref.load_meta(inp / "docs.parquet")
    masks = ref.masks_for(meta, ref.read_queries(inp / "queries.jsonl"))
    assert masks[1].sum() == 10  # every 4th row is Dortmund
    assert masks[2].sum() == 14  # rows 4–19 (16 days inclusive) minus the null dates of rows 9 and 19
    assert masks[3].sum() == 3  # Dortmund rows 0, 4, 8 up to 2025-01-09
    old = ref.DOC_CHUNK
    ref.DOC_CHUNK = 7  # exercise the chunk merge + pre-selection
    try:
        top = ref.exact_topk(docs, qv, masks)
    finally:
        ref.DOC_CHUNK = old
    assert (top == _naive(docs, qv, masks)).all()
    assert (top[3, 3:] == -1).all()


def test_recall_counts_only_existing_matches() -> None:
    exact = np.array([[1, 2, 3] + [-1] * 7, list(range(10))], dtype=np.int32)
    top = np.array([[3, 1, 9] + [-1] * 7, [*range(9), 50]], dtype=np.int32)
    assert track_c.recall(top, exact).tolist() == [2 / 3, 0.9]


def test_verifier_passes_exact_and_rejects_filter_violations(tmp_path: Path) -> None:
    inp, docs, qv = _scale(tmp_path)
    (tmp_path / "spec").mkdir()
    qrels = [
        {**json.loads(x), "known_item": "d0"} for x in (inp / "queries.jsonl").read_text("utf-8").splitlines()
    ]
    (tmp_path / "spec" / "qrels_c.T.jsonl").write_text("".join(json.dumps(q) + "\n" for q in qrels), "utf-8")
    meta = ref.load_meta(inp / "docs.parquet")
    exact = ref.exact_topk(docs, qv, ref.masks_for(meta, ref.read_queries(inp / "queries.jsonl")))
    refdir = tmp_path / "data" / "reference" / "track_c" / "T"
    refdir.mkdir(parents=True)
    np.save(refdir / "exact_top10.npy", exact)
    np.save(tmp_path / "ok.npy", exact)
    np.save(tmp_path / "ok.c16.npy", exact)  # concurrency-16 pass
    bad = exact.copy()
    bad[1, 0] = 1  # row 1 is Essen, query q2 filters Dortmund
    np.save(tmp_path / "bad.npy", bad)
    np.save(tmp_path / "bad.c16.npy", exact)
    np.save(tmp_path / "noc16.npy", exact)
    track_c.QRELS["T"] = "spec/qrels_c.T.jsonl"
    try:
        res = verify_track_c.check(tmp_path / "ok.npy", refdir / "exact_top10.npy")
        assert res["passed"], res["detail"]
        assert res["quality"]["recall_at_10"] == 1.0
        res = verify_track_c.check(tmp_path / "bad.npy", refdir / "exact_top10.npy")
        assert not res["passed"]
        assert "outside the filter" in res["detail"]
        res = verify_track_c.check(tmp_path / "noc16.npy", refdir / "exact_top10.npy")
        assert not res["passed"]
        assert "concurrency-16 output missing" in res["detail"]
    finally:
        del track_c.QRELS["T"]
