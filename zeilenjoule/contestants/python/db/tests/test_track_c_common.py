"""Filter translation of the Track C contestants: every engine must apply exactly spec/track_c.md's filter."""

from __future__ import annotations

import datetime as dt
from typing import ClassVar

import numpy as np
import pyarrow as pa

from zj_db import c06_sqlite_vec, c07_lancedb
from zj_db.common import Query, run_queries, sql_where
from zj_db.meta import MetaArrays, exact_subset

D = dt.date


def q(**kw: object) -> Query:
    base: dict[str, object] = {"qid": "q", "filter_class": "x", "ort": None, "bezirk": None,
                               "date_from": None, "date_to": None}  # fmt: skip
    return Query(**(base | kw))  # type: ignore[arg-type]


def test_sql_where() -> None:
    assert sql_where(q()) == ("TRUE", [])
    w, p = sql_where(q(ort="Essen", date_from=D(2025, 1, 1), date_to=D(2025, 3, 31)), "?")
    assert w == "ort = ? AND datum >= ? AND datum <= ?"
    assert p == ["Essen", D(2025, 1, 1), D(2025, 3, 31)]


def test_sqlite_where_never_matches_missing_dates() -> None:
    w, p = c06_sqlite_vec.where(q(date_to=D(2025, 1, 9)))
    assert w == "AND datum >= ? AND datum <= ?"
    assert p[0] == c06_sqlite_vec.NO_DATE
    assert str(p[0]) > ""  # '' (missing date) is below every lower bound


def test_lance_where() -> None:
    assert c07_lancedb.where(q()) is None
    assert c07_lancedb.where(q(bezirk="Münster", date_from=D(2025, 10, 1))) == (
        "bezirk = 'Münster' AND datum >= date '2025-10-01'"
    )
    assert c07_lancedb.where(q(ort="O'Brien")) == "ort = 'O''Brien'"


def test_meta_mask_and_exact_subset() -> None:
    t = pa.table({"ort": ["A", "B", "A", None], "bezirk": [None, "X", "X", "X"],
                  "datum": pa.array([D(2025, 1, 1), D(2025, 1, 2), None, D(2025, 1, 3)],
                                    pa.date32())})  # fmt: skip
    m = MetaArrays.from_table(t)
    assert m.mask(q()) is None
    assert m.mask(q(ort="A")).tolist() == [True, False, True, False]  # type: ignore[union-attr]
    assert m.mask(q(bezirk="X", date_to=D(2025, 1, 2))).tolist() == [False, True, False, False]  # type: ignore[union-attr]
    vecs = np.eye(4, dtype=np.float16)
    assert exact_subset(vecs, np.array([0, 2, 3]), np.array([0.1, 0, 0.9, 0.5], np.float32), 10) == [2, 3, 0]
    assert exact_subset(vecs, np.array([], dtype=np.int64), np.ones(4, np.float32), 10) == []


def test_run_queries_pads_and_truncates() -> None:
    class Inp:
        queries: ClassVar[list[Query]] = [q(), q()]
        qvec = np.zeros((2, 4), np.float32)

    top, lat = run_queries(Inp(), lambda qq, v: [1, 2, 3] if qq is Inp.queries[0] else list(range(12)))  # type: ignore[arg-type]
    assert top[0].tolist() == [1, 2, 3] + [-1] * 7
    assert top[1].tolist() == list(range(10))
    assert lat.shape == (2,)


def test_hybrid_words_fts_query_and_rrf_sql() -> None:
    import sqlite3  # noqa: PLC0415

    from zj_db import c06_sqlite_hybrid, hybrid  # noqa: PLC0415

    assert hybrid.words("Leckage an der Pumpe – Leckage (P_9)") == ["leckage", "an", "der", "pumpe", "p", "9"]
    assert c06_sqlite_hybrid.fts_query('Ölstand "hoch"') == '"ölstand" OR "hoch"'
    assert c06_sqlite_hybrid.fts_query("– !") == ""
    con = sqlite3.connect(":memory:")  # the generic RRF statement: ranks from 1, k = 60, ties → lower row
    rows = con.execute(
        "WITH d(i, r) AS (VALUES (5, 1), (3, 2)), l(i, r) AS (VALUES (3, 1), (7, 2), (5, 3), (9, 4), (8, 4)) "
        + hybrid.rrf_sql()
    ).fetchall()
    assert [r[0] for r in rows] == [3, 5, 7, 8, 9]
