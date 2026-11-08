"""Track C lexical/hybrid reference (analyzer, BM25, RRF) and the c.lexical / c.hybrid verifier."""

from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from zj_eval import track_c, track_c_lex, verify_track_c_lex
from zj_reference import track_c as ref
from zj_reference import track_c_lex as lex


def test_analyzer_is_tantivys_pipeline() -> None:
    an = lex.Analyzer()
    assert an("Leckage an der Kreiselpumpe (P-9640_B)") == ["leckag", "an", "der", "kreiselpump", "p", "9640",
                                                           "b"]  # fmt: skip
    assert an("Druckluftleitungsleckage") == ["druckluftleitungsleckag"]  # Snowball does not decompound
    assert an("x" * 39 + " " + "y" * 40) == ["x" * 39]  # tokens of ≥ 40 bytes are dropped
    assert an("Überhitzung") == an("überhitzung")


def test_bm25_matches_the_formula() -> None:
    texts = ["pumpe leckage", "pumpe pumpe ventil", "ventil", "motor lager"]
    idx = lex.build_index(texts)
    an = lex.Analyzer()
    s = idx.scores(an("Pumpe"))
    avgdl = (2 + 3 + 1 + 2) / 4
    idf = math.log(1 + (4 - 2 + 0.5) / (2 + 0.5))

    def bm25(tf: int, dl: int) -> float:
        return idf * tf * (lex.K1 + 1) / (tf + lex.K1 * (1 - lex.B + lex.B * dl / avgdl))

    assert np.allclose(s, [bm25(1, 2), bm25(2, 3), 0.0, 0.0])
    assert np.allclose(idx.scores(an("pumpe pumpe")), s)  # the query is a set of terms
    top = lex.topk(s, np.array([True, True, True, False]), 3)
    assert top.tolist() == [1, 0, -1]  # only positive scores are hits


def test_rrf_ranks_from_one_and_breaks_ties_by_row() -> None:
    best, score = lex.rrf([np.array([5, 3, -1]), np.array([3, 7, 5])], 3)
    assert math.isclose(score[3], 1 / 62 + 1 / 61)
    assert math.isclose(score[5], 1 / 61 + 1 / 63)
    assert best.tolist() == [3, 5, 7]
    best, _ = lex.rrf([np.array([9]), np.array([4])], 2)
    assert best.tolist() == [4, 9]  # equal scores → lower row first


def test_compound_subset() -> None:
    assert lex.is_compound("Förderbandverstopfung")
    assert not lex.is_compound("Leckage an der Druckluftleitung")


N = 30
TEXTS = [("Leckage an der Pumpe" if i % 3 == 0 else "Verschleiß am Ventil" if i % 3 == 1 else "Pumpenleckage")
         + f" Nr {i}" for i in range(N)]  # fmt: skip


def _scale(tmp: Path) -> tuple[Path, np.ndarray]:
    """A 30-document known-item scale 'T' with query texts and a full reference (bm25, dense, hybrid)."""
    rng = np.random.default_rng(3)
    inp = tmp / "data" / "track_c" / "T"
    inp.mkdir(parents=True)
    ort = ["Essen" if i % 2 else "Hagen" for i in range(N)]
    pq.write_table(pa.table({"id": [f"d{i}" for i in range(N)], "ort": ort,  # pyright: ignore[reportUnknownMemberType]
                             "bezirk": ["Arnsberg"] * N,
                             "datum": pa.array([dt.date(2025, 1, 1)] * N, pa.date32()), "doc_text": TEXTS}),
                   inp / "docs.parquet")  # fmt: skip
    qs: list[dict[str, Any]] = [
        {"qid": "q1", "filter_class": "none", "filter": {}, "text": "Pumpenleckage", "known_item": "d2"},
        {"qid": "q2", "filter_class": "ort", "filter": {"ort": "Essen"}, "text": "Ventil Verschleiß",
         "known_item": "d1"},
    ]  # fmt: skip
    (inp / "queries.jsonl").write_text(
        "".join(json.dumps({k: q[k] for k in ("qid", "filter_class", "filter")}) + "\n" for q in qs), "utf-8"
    )
    (tmp / "spec").mkdir()
    (tmp / "spec" / "qrels_c.T.jsonl").write_text("".join(json.dumps(q) + "\n" for q in qs), "utf-8")
    vec = rng.standard_normal((N, ref.DIM)).astype(np.float32)
    vec /= np.linalg.norm(vec, axis=1, keepdims=True)
    (tmp / "data" / "reference" / "track_b" / "T").mkdir(parents=True)
    np.save(tmp / "data" / "reference" / "track_b" / "T" / "bge-m3.f32.npy", vec)
    np.save(inp / "queries.f32.npy", vec[[2, 1]])
    refdir = tmp / "data" / "reference" / "track_c" / "T"
    refdir.mkdir(parents=True)
    ctx = track_c.load_context("T", tmp)
    np.save(refdir / "exact_top10.npy", ref.exact_topk(vec, vec[[2, 1]], ctx.masks))
    return refdir, vec


def test_reference_and_verifiers(tmp_path: Path) -> None:
    track_c.QRELS["T"] = "spec/qrels_c.T.jsonl"
    old = track_c.REPO
    try:
        refdir, _ = _scale(tmp_path)
        info = track_c_lex.build_reference("T", tmp_path)
        assert info["bm25_empty_queries"] == 0
        assert (tmp_path / "data" / "track_c" / "T" / "queries.text.jsonl").exists()
        bm = np.load(refdir / "bm25_top10.npy")
        assert set(bm[0][bm[0] >= 0].tolist()) == {i for i in range(N) if i % 3 == 2}  # „Pumpenleckage“ only
        assert all(i % 2 == 1 for i in bm[1] if i >= 0)  # filter ort = Essen
        res = verify_track_c_lex.check(refdir / "bm25_top10.npy", refdir / "bm25_top10.npy", "bm25")
        assert res["passed"], res["detail"]
        # hybrid: reference legs + fused output pass; a fused output that is not the legs' RRF fails
        out = tmp_path / "out.npy"
        np.save(out, np.load(refdir / "hybrid_top10.npy"))
        np.save(tmp_path / "out.dense.npy", np.load(refdir / "dense_top100.npy"))
        np.save(tmp_path / "out.lex.npy", np.load(refdir / "bm25_top100.npy"))
        res = verify_track_c_lex.check(out, refdir / "hybrid_top10.npy", "hybrid")
        assert res["passed"], res["detail"]
        assert res["quality"]["fusion_agreement"] == 1.0
        wrong = np.load(refdir / "hybrid_top10.npy")
        wrong[0] = np.load(refdir / "dense_top100.npy")[0, 5:15]
        np.save(out, wrong)
        res = verify_track_c_lex.check(out, refdir / "hybrid_top10.npy", "hybrid")
        assert not res["passed"]
        assert "RRF" in res["detail"]
        (tmp_path / "out.lex.npy").unlink()
        res = verify_track_c_lex.check(out, refdir / "hybrid_top10.npy", "hybrid")
        assert not res["passed"]
        assert "out.lex.npy" in res["detail"]
    finally:
        del track_c.QRELS["T"]
        track_c.REPO = old
