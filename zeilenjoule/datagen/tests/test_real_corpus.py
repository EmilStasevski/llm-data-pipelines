from __future__ import annotations

import datetime as dt
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from zj_datagen import real_corpus


def _write(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), path)  # pyright: ignore[reportUnknownMemberType]


@pytest.fixture
def real(tmp_path: Path) -> Path:
    r = tmp_path / "real"
    fac = {"ort": "Essen", "nace_text": "Stahl", "prtr_beschreibung": "Hochofen", "ivu_beschreibung": "x",
           "branchengruppe": "Metall"}  # fmt: skip
    _write(r / "thru" / "thru_betriebe_nrw.parquet", [
        {**fac, "jahr": 2024}, {**fac, "jahr": 2024},  # identical → de-duplicated
        {**fac, "jahr": 2020, "ort": "Köln"},  # older year → dropped
    ])  # fmt: skip
    _write(r / "ted" / "ted_nrw.parquet", [
        {"publication_number": f"{i}-2026", "publication_date": f"2026-01-{i + 1:02d}",
         "title": "Gleich" if i < 2 else f"Titel {i}", "description": "d", "buyer_name": "Stadt",
         "buyer_city": "Bonn" if i % 2 else "Köln", "cpv": "45000000"}
        for i in range(12)
    ])  # fmt: skip
    _write(r / "ba" / "ba_werkstudent_nrw.parquet", [
        {"referenznummer": f"R{i}", "titel": f"Werkstudent {i}", "alle_berufe": "Informatiker/in",
         "firma": "Firma AG", "ort": "Bonn", "angebotsart": "ARBEIT", "erstveroeffentlichung": "2026-02-01",
         "vertragsdauer": "BEFRISTET"}
        for i in range(6)
    ])  # fmt: skip
    return r


def test_rows_dedup_and_ids(real: Path) -> None:
    rows = real_corpus.build_rows(real)
    by = {q: [r for r in rows if r["quelle"] == q] for q in ("thru", "ted", "ba")}
    assert (len(by["thru"]), len(by["ted"]), len(by["ba"])) == (1, 12, 6)
    assert by["thru"][0]["doc_id"].startswith("thru:")
    assert by["thru"][0]["datum"] is None
    assert by["ted"][0]["datum"] == dt.date(2026, 1, 1)
    assert "Ausschreibung: Gleich | Auftraggeber: Stadt, Köln" in by["ted"][0]["doc_text"]
    assert len({r["doc_id"] for r in rows}) == len(rows)


def test_queries_are_unique_known_items_inside_their_filter(real: Path) -> None:
    rows = real_corpus.build_rows(real)
    qs = real_corpus.queries(rows, seed=1, per_class=4)
    assert [q["filter_class"] for q in qs] == [c for c in real_corpus.CLASSES for _ in range(4)]
    docs = {r["doc_id"]: r for r in rows}
    for q in qs:
        doc = docs[q["known_item"]]
        assert q["text"] == doc["titel"] != "Gleich"  # ambiguous titles are never known items
        assert q["n_filter_match"] >= 1
        f = q["filter"]
        if "ort" in f:
            assert doc["ort"] == f["ort"]
        if "datum_von" in f:
            assert f["datum_von"] <= doc["datum"].isoformat() <= f["datum_bis"]
    assert qs == real_corpus.queries(rows, seed=1, per_class=4)  # deterministic


def test_too_few_unique_titles_fails_loudly(real: Path) -> None:
    with pytest.raises(RuntimeError, match="unique-title"):
        real_corpus.queries(real_corpus.build_rows(real), seed=1, per_class=10)
