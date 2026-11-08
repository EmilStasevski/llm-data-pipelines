"""The Track A verifier must catch every kind of deviation (rule 4: wrong output is disqualified)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from zj_eval.verify_track_a import verify
from zj_reference.track_a import SCHEMA

REPO = Path(__file__).resolve().parents[2]
SPEC = REPO / "spec" / "track_a.schema.json"


def _row(i: int, **kw: Any) -> dict[str, Any]:
    r: dict[str, Any] = {
        "meldung_id": f"20000000{i:02d}",
        "werk": "Duisburg",
        "anlage": "Beizlinie 2",
        "equipment_nr": "P-4711",
        "datum": dt.date(2026, 3, 2),
        "schicht": "Früh",
        "prioritaet": 2,
        "stillstand_min": 90,
        "kosten_eur": 1234.56,
        "status": "offen",
        "text_norm": "Pumpe undicht.",
        "text_lower": "pumpe undicht.",
        "text_fold": "pumpe undicht.",
        "materialnr": None,
        "sicherheitsrelevant_rule": False,
        "komponente_rule": "Kreiselpumpe",
        "regierungsbezirk": "Düsseldorf",
        "nuts2": "DEA1",
        "doc_text": "Werk: Duisburg | Meldung: Pumpe undicht.",
    }
    r.update(kw)
    return r


def _write(path: Path, rows: list[dict[str, Any]], schema: pa.Schema = SCHEMA) -> Path:
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), path)  # pyright: ignore[reportUnknownMemberType]
    return path


@pytest.fixture
def ref(tmp_path: Path) -> Path:
    return _write(tmp_path / "ref.parquet", [_row(i) for i in range(5)])


def test_identical_passes(tmp_path: Path, ref: Path) -> None:
    out = _write(tmp_path / "o.parquet", [_row(i) for i in range(5)])
    assert verify(out, ref, SPEC)["passed"]


def test_float_within_tolerance_passes(tmp_path: Path, ref: Path) -> None:
    out = _write(tmp_path / "o.parquet", [_row(i, kosten_eur=1234.56 + 1e-12) for i in range(5)])
    assert verify(out, ref, SPEC)["passed"]


@pytest.mark.parametrize(
    ("change", "key"),
    [
        ({"kosten_eur": 1234.57}, "kosten_eur"),
        ({"kosten_eur": None}, "kosten_eur"),
        ({"text_norm": "Pumpe undicht"}, "text_norm"),
        ({"komponente_rule": None}, "komponente_rule"),
        ({"datum": dt.date(2026, 2, 3)}, "datum"),
        ({"sicherheitsrelevant_rule": True}, "sicherheitsrelevant_rule"),
    ],
)
def test_value_mismatch_detected(tmp_path: Path, ref: Path, change: dict[str, Any], key: str) -> None:
    rows = [_row(i) for i in range(5)]
    rows[3] = _row(3, **change)
    res = verify(_write(tmp_path / "o.parquet", rows), ref, SPEC)
    assert not res["passed"]
    assert res["mismatches"] == {key: 1}
    assert res["examples"][0]["meldung_id"] == "2000000003"


def test_missing_extra_unsorted_detected(tmp_path: Path, ref: Path) -> None:
    res = verify(_write(tmp_path / "a.parquet", [_row(i) for i in range(4)]), ref, SPEC)
    assert res["mismatches"] == {"missing_rows": 1}
    res = verify(_write(tmp_path / "b.parquet", [_row(i) for i in [4, 3, 2, 1, 0]]), ref, SPEC)
    assert "unsorted" in res["mismatches"]
    assert not res["passed"]


def test_schema_mismatch_detected(tmp_path: Path, ref: Path) -> None:
    # pyarrow-stubs leave Schema.field() partially untyped (same gap as pq.read_table, see zj_datagen.writer)
    wrong = pa.schema(
        [(n, pa.int64() if n == "prioritaet" else SCHEMA.field(n).type) for n in SCHEMA.names]  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
    )
    res = verify(_write(tmp_path / "o.parquet", [_row(i) for i in range(5)], wrong), ref, SPEC)
    assert not res["passed"]
    assert "schema mismatch" in res["detail"]
    assert "BIGINT" in res["detail"]


def test_missing_output(tmp_path: Path, ref: Path) -> None:
    assert not verify(tmp_path / "nope.parquet", ref, SPEC)["passed"]
