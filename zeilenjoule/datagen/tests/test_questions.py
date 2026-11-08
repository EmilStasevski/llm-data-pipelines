"""Derived evaluation sets must be exactly consistent with the gold labels."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import pytest

from zj_datagen import gen, lexicon, questions
from zj_datagen.writer import read_table
from zj_eval.relevance import Filter, grade

REPO = Path(__file__).resolve().parents[2]
LEX = lexicon.load(REPO / "spec" / "lexicon")
SEED = 20261106


@pytest.fixture(scope="module")
def scale_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("synth")
    gen.generate(REPO, "S", out, workers=2)
    return out / "stoermeldungen" / "S"


@pytest.fixture(scope="module")
def gold_rows(scale_dir: Path) -> list[dict[str, Any]]:
    return read_table(scale_dir / "stoermeldungen_gold.parquet").to_pylist()


def test_sql_grade_counts_equal_python_rule(scale_dir: Path, gold_rows: list[dict[str, Any]]) -> None:
    con = questions.connect(scale_dir, LEX)
    eq = {k.name: k.eq for k in LEX.komponenten}
    cases = [
        ("Kreiselpumpe", "Leckage", Filter()),
        ("Getriebe", "Verschleiß", Filter(werk="Duisburg")),
        ("Elektromotor", "Überhitzung", Filter(date_from=dt.date(2025, 1, 1), date_to=dt.date(2025, 6, 30))),
        (
            "Drucksensor",
            "Fehlalarm",
            Filter(regierungsbezirk="Köln", date_from=dt.date(2024, 4, 1), date_to=dt.date(2024, 6, 30)),
        ),
    ]
    for k, s, f in cases:
        grades = [grade(r, k, s, f, eq) for r in gold_rows]
        assert questions.grade_counts(con, k, s, f) == (grades.count(2), grades.count(1)), (k, s, f)


def test_queries_c_are_consistent(scale_dir: Path) -> None:
    qs = questions.queries_c(scale_dir, LEX, SEED, "S", per_class=15)
    assert [q["filter_class"] for q in qs].count("kombiniert") == 15
    assert all(q["n_grade2"] >= 1 for q in qs)
    assert questions.queries_c(scale_dir, LEX, SEED, "S", per_class=15) == qs  # deterministic


def test_questions_e_gold_is_exact(scale_dir: Path, gold_rows: list[dict[str, Any]]) -> None:
    qs = questions.questions_e(scale_dir, LEX, SEED, "S", per_type=12)
    con = questions.connect(scale_dir, LEX)
    by_id = {r["meldung_id"]: r for r in gold_rows}
    vis = {r["meldung_id"]: r for r in read_table(scale_dir / "stoermeldungen.parquet").to_pylist()}
    for q in qs:
        if q["type"] == "lookup":
            (mid,) = q["gold_ids"]
            g = by_id[mid]
            assert q["answer"] in (g["ursache"], g["massnahme"], g["stillstand_minuten"])
            if "Ursache" in q["text"]:  # natural key must identify exactly one row
                key = (vis[mid]["equipment_nr"], vis[mid]["werk"], g["datum_iso"])
                hits = [
                    m
                    for m, r in by_id.items()
                    if (vis[m]["equipment_nr"], vis[m]["werk"], r["datum_iso"]) == key
                ]
                assert hits == [mid]
        elif q["type"] == "filtered_semantic":
            f = Filter.from_json(q["filter"])
            target = (q["komponente"], q["schadensart"])
            exp = sorted(
                m for m, r in by_id.items() if (r["komponente"], r["schadensart"]) == target and f.matches(r)
            )
            assert q["gold_ids"] == exp
        else:
            rows = con.execute(q["gold_sql"]).fetchall()
            assert rows, q
    assert questions.questions_e(scale_dir, LEX, SEED, "S", per_type=12) == qs  # deterministic


def test_d2k_disjoint(scale_dir: Path, tmp_path: Path) -> None:
    info = questions.d2k(scale_dir, tmp_path, SEED, 500, 100)
    a = set(read_table(tmp_path / "d2k.parquet")["meldung_id"].to_pylist())
    b = set(read_table(tmp_path / "dev.parquet")["meldung_id"].to_pylist())
    assert (len(a), len(b), len(a & b)) == (500, 100, 0)
    assert info["disjoint"]
    g = read_table(tmp_path / "d2k_gold.parquet")["meldung_id"].to_pylist()
    assert g == read_table(tmp_path / "d2k.parquet")["meldung_id"].to_pylist()  # rows aligned
