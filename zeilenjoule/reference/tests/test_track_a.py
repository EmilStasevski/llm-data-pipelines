"""The reference implementation must satisfy every golden vector in spec/track_a_cases.jsonl."""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from zj_reference import track_a as ta

REPO = Path(__file__).resolve().parents[2]
CASES = [
    json.loads(line)
    for line in (REPO / "spec" / "track_a_cases.jsonl").read_text(encoding="utf-8").splitlines()
]
LEX = ta.load_lexicon()


def _doc_text(inp: dict[str, Any]) -> str:
    row = dict(inp)
    row["datum"] = dt.date.fromisoformat(row["datum"]) if row["datum"] else None
    return ta.doc_text(row)


def _row(inp: dict[str, str]) -> dict[str, Any]:
    out = ta.process_row(inp, LEX)
    out["datum"] = out["datum"].isoformat() if out["datum"] else None
    return out


def _date(inp: str) -> str | None:
    d = ta.parse_date(inp)
    return d.isoformat() if d else None


DISPATCH: dict[str, Callable[[Any], Any]] = {
    "norm": ta.norm,
    "equipment": ta.equipment,
    "date": _date,
    "prio": ta.parse_prio,
    "eur": ta.parse_eur,
    "minutes": ta.parse_minutes,
    "expand": lambda s: ta.expand_abbreviations(s, LEX),
    "fold": lambda s: ta.fold(s.lower()),
    "lower": lambda s: s.lower(),
    "materialnr": ta.materialnr,
    "safety": lambda s: ta.safety(s, LEX),
    "komponente": lambda s: ta.komponente(s, LEX),
    "doc_text": _doc_text,
    "row": _row,
}


def _call(fn: str, inp: Any) -> Any:
    return DISPATCH[fn](inp)


def test_at_least_150_cases() -> None:
    assert len(CASES) >= 150


@pytest.mark.parametrize("case", CASES, ids=[f"{c['id']}-{c['fn']}" for c in CASES])
def test_golden_vector(case: dict[str, Any]) -> None:
    got = _call(case["fn"], case["input"])
    if case["fn"] == "row":
        sub = {k: got[k] for k in case["expected"]}
        assert sub == case["expected"]
    elif isinstance(case["expected"], float):
        assert got == pytest.approx(case["expected"], abs=1e-9)
    else:
        assert got == case["expected"]


def test_lexicon_json_is_current() -> None:
    assert ta.compile_lexicon(REPO / "spec" / "lexicon") == LEX, (
        "run: python -m zj_reference.track_a compile-lexicon"
    )


def test_streaming_run_matches_single_chunk(tmp_path: Path) -> None:
    """Chunked writing must not change the output (XL reference streams in chunks)."""
    import pyarrow.parquet as pq  # noqa: PLC0415

    src = REPO / "data" / "synth" / "stoermeldungen" / "S" / "stoermeldungen.cp1252.csv"
    if not src.exists():
        pytest.skip("synthetic S data not generated")
    n1 = ta.run(src, tmp_path / "one.parquet", LEX, chunk_rows=10**9)
    n2 = ta.run(src, tmp_path / "many.parquet", LEX, chunk_rows=777)
    assert n1 == n2 == 10_000
    one, many = (pq.ParquetFile(tmp_path / f).read() for f in ("one.parquet", "many.parquet"))  # pyright: ignore[reportUnknownMemberType]
    assert one.equals(many)


def test_unsorted_input_fails_loudly(tmp_path: Path) -> None:
    src = REPO / "data" / "synth" / "stoermeldungen" / "S" / "stoermeldungen.cp1252.csv"
    if not src.exists():
        pytest.skip("synthetic S data not generated")
    lines = src.read_bytes().split(b"\r\n")
    swapped = [lines[0], lines[2], lines[1], *lines[3:]]
    bad = tmp_path / "bad.csv"
    bad.write_bytes(b"\r\n".join(swapped))
    with pytest.raises(ValueError, match="strictly increasing"):
        ta.run(bad, tmp_path / "out.parquet", LEX)
