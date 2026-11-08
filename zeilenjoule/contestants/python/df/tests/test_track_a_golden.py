"""spec/track_a_cases.jsonl against every Track A implementation in this env (row-wise, Polars, DuckDB)."""

from __future__ import annotations

import csv
import datetime as dt
import json
import math
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from zj_df.track_a import duckdb_impl
from zj_df.track_a.common import INPUT_COLUMNS
from zj_df.track_a.pandas_impl import PandasRules
from zj_df.track_a.polars_impl import PolarsRules
from zj_df.track_a.rowwise import Rules

REPO = Path(__file__).resolve().parents[4]
LEX = json.loads((REPO / "spec" / "track_a_lexicon.json").read_text(encoding="utf-8"))
CASES = [
    json.loads(x) for x in (REPO / "spec" / "track_a_cases.jsonl").read_text(encoding="utf-8").splitlines()
]


def _iso(d: Any) -> Any:
    return d.isoformat() if isinstance(d, dt.date) else d


# ---------------------------------------------------------------------------------- row-wise
R = Rules(LEX)


def rowwise(fn: str, inp: Any) -> Any:
    table: dict[str, Callable[[Any], Any]] = {
        "norm": R.norm,
        "equipment": R.equipment,
        "date": lambda s: _iso(R.date(s)),
        "prio": R.prio,
        "eur": R.eur,
        "minutes": R.minutes,
        "expand": R.expand,
        "fold": lambda s: R.fold(s.lower()),
        "lower": str.lower,
        "materialnr": lambda s: R.row({**dict.fromkeys(INPUT_COLUMNS, ""), "meldungstext": s})["materialnr"],
        "safety": lambda s: any(t in s for t in R.triggers),
        "komponente": R.komponente,
        "doc_text": lambda d: R.doc(
            {**d, "datum": dt.date.fromisoformat(d["datum"]) if d["datum"] else None}
        ),
        "row": lambda r: {k: _iso(v) for k, v in R.row(r).items()},
    }
    return table[fn](inp)


# ------------------------------------------------------------------------------------ Polars
P = PolarsRules(LEX)


def _one(expr: Callable[[pl.Expr], pl.Expr], value: str) -> Any:
    return pl.DataFrame({"x": [value]}, schema={"x": pl.String}).select(expr(pl.col("x"))).item()


def _polars_row(r: dict[str, str]) -> dict[str, Any]:
    out = P.transform(pl.DataFrame({c: [r[c]] for c in INPUT_COLUMNS})).row(0, named=True)
    return {k: _iso(v) for k, v in out.items()}


def polars(fn: str, inp: Any) -> Any:
    n = P.norm
    table: dict[str, Callable[[Any], Any]] = {
        "norm": lambda s: _one(n, s),
        "equipment": lambda s: _one(lambda e: P.equipment(n(e)), s),
        "date": lambda s: _iso(_one(lambda e: P.date(n(e)), s)),
        "prio": lambda s: _one(lambda e: P.prio(n(e)), s),
        "eur": lambda s: _one(lambda e: P.eur(n(e)), s),
        "minutes": lambda s: _one(lambda e: P.minutes(n(e)), s),
        "expand": lambda s: _one(P.expand, s),
        "fold": lambda s: _one(lambda e: P.fold(e.str.to_lowercase()), s),
        "lower": lambda s: _one(lambda e: e.str.to_lowercase(), s),
        "materialnr": lambda s: _one(P.materialnr, s),
        "safety": lambda s: _one(P.safety, s),
        "komponente": lambda s: P.komponente(pl.DataFrame({"meldung_id": ["1"], "text_fold": [s]}))[
            "komponente_rule"
        ][0],
        "row": _polars_row,
    }
    return table[fn](inp)


# ------------------------------------------------------------------------------------ pandas
PD = PandasRules(LEX)


def _ps(value: str) -> pd.Series:
    return pd.Series([value], dtype=pd.ArrowDtype(pa.string()))


def _p1(x: Any) -> Any:
    v = x.to_pylist()[0] if hasattr(x, "to_pylist") else x.iloc[0]
    return None if v is None or v is pd.NA or (isinstance(v, float) and math.isnan(v)) else v


def _pandas_row(r: dict[str, str]) -> dict[str, Any]:
    t = PD.transform(pd.DataFrame({c: [r[c]] for c in INPUT_COLUMNS}, dtype=str))
    return {k: _iso(v) for k, v in t.to_pylist()[0].items()}


def pandas(fn: str, inp: Any) -> Any:
    n = PD.norm
    table: dict[str, Callable[[Any], Any]] = {
        "norm": lambda s: _p1(n(_ps(s))),
        "equipment": lambda s: _p1(PD.equipment(n(_ps(s)))),
        "date": lambda s: _iso(_p1(PD.date(n(_ps(s))))),
        "prio": lambda s: _p1(PD.prio(n(_ps(s)))),
        "eur": lambda s: _p1(PD.eur(n(_ps(s)))),
        "minutes": lambda s: _p1(PD.minutes(n(_ps(s)))),
        "expand": lambda s: _p1(PD.expand(_ps(s))),
        "fold": lambda s: _p1(PD.to_fold(_ps(s).str.lower())),
        "lower": lambda s: _p1(_ps(s).str.lower()),
        "materialnr": lambda s: _pandas_row({**dict.fromkeys(INPUT_COLUMNS, ""), "meldungstext": s})[
            "materialnr"
        ],
        "safety": lambda s: bool(_p1(_ps(s).str.contains(PD.safety_re, regex=True))),
        "komponente": lambda s: _p1(PD.komponente(_ps(s))),
        "row": _pandas_row,
    }
    return table[fn](inp)


# ------------------------------------------------------------------------------------ DuckDB
CON = duckdb.connect()
CON.execute("LOAD encodings")
for _sql in duckdb_impl.macros(LEX) + duckdb_impl.tables(LEX):
    CON.execute(_sql)


def _q(sql: str, value: Any) -> Any:
    row = CON.execute(sql, [value]).fetchone()
    assert row is not None
    return row[0]


def _duck_row(r: dict[str, str]) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as d:
        src, out = Path(d) / "in.csv", Path(d) / "out.parquet"
        with src.open("w", encoding="cp1252", newline="") as f:
            w = csv.writer(f, delimiter=";", lineterminator="\r\n")
            w.writerow(INPUT_COLUMNS)
            w.writerow([r[c] for c in INPUT_COLUMNS])
        for sql in duckdb_impl.pipeline(str(src), str(out)):
            CON.execute(sql)
        row = pq.read_table(out).to_pylist()[0]  # pyright: ignore[reportUnknownMemberType]
    return {k: _iso(v) for k, v in row.items()}


def duck(fn: str, inp: Any) -> Any:
    table: dict[str, Callable[[Any], Any]] = {
        "norm": lambda s: _q("SELECT zj_norm(?)", s),
        "equipment": lambda s: _q("SELECT zj_equipment(zj_norm(?))", s),
        "date": lambda s: _iso(_q("SELECT zj_date(zj_norm(?))", s)),
        "prio": lambda s: _q("SELECT zj_prio(zj_norm(?))", s),
        "eur": lambda s: _q("SELECT zj_eur(zj_norm(?))", s),
        "minutes": lambda s: _q("SELECT zj_minutes(zj_norm(?))", s),
        "expand": lambda s: _q("SELECT zj_expand(?)", s),
        "fold": lambda s: _q("SELECT zj_fold(lower(?))", s),
        "lower": lambda s: _q("SELECT lower(?)", s),
        "materialnr": lambda s: _q("SELECT zj_matnr(?)", s),
        "safety": lambda s: _q("SELECT zj_safety(?)", s),
        "komponente": lambda s: _q(
            "SELECT arg_max(name, length(form) * 1000000 - instr(?1, form)) "
            "FROM zj_forms WHERE contains(?1, form)",
            s,
        ),
        "row": _duck_row,
    }
    return table[fn](inp)


IMPLS: dict[str, Callable[[str, Any], Any]] = {
    "rowwise": rowwise, "polars": polars, "pandas": pandas, "duckdb": duck,
}  # fmt: skip
PARAMS = [
    pytest.param(impl, c, id=f"{impl}-{c['id']}-{c['fn']}")
    for impl in IMPLS
    for c in CASES
    if not (
        c["fn"] == "doc_text" and impl != "rowwise"
    )  # Polars/pandas/DuckDB build doc_text inside the pipeline (row cases)
]


@pytest.mark.parametrize(("impl", "case"), PARAMS)
def test_golden(impl: str, case: dict[str, Any]) -> None:
    got = IMPLS[impl](case["fn"], case["input"])
    exp = case["expected"]
    if case["fn"] == "row":
        assert {k: got[k] for k in exp} == exp
    elif isinstance(exp, float):
        assert got == pytest.approx(exp, abs=1e-9)
    else:
        assert got == exp
