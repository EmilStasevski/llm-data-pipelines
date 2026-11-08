"""Quality notifications: the verdict must follow from the *visible* value and tolerance alone."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from zj_datagen import gen, lexicon
from zj_datagen import qualitaetsmeldungen as q

REPO = Path(__file__).resolve().parents[2]
LEXDIR = REPO / "spec" / "lexicon"
CFG = q.Config.from_dict(gen.load_config(REPO))


@pytest.fixture(scope="module")
def chunk() -> tuple[dict[str, list[Any]], dict[str, list[Any]]]:
    st = q.QState(lexicon.load(LEXDIR).werke, q.load_lexicon(LEXDIR), q.lieferanten(CFG.seed, 40), CFG)
    return q.generate_chunk(st, 0, 5000)


NUM = r"(-?\d+(?:,\d+)?)"
WERT = re.compile(rf"^{NUM}\s?(\S+)$")
SYM = re.compile(rf"^{NUM} ± {NUM} (\S+)$")
RANGE = re.compile(rf"^{NUM}–{NUM} (\S+)$")
PLUSMINUS = re.compile(rf"^{NUM} \+{NUM}/-{NUM} (\S+)$")
ONE_SIDED = re.compile(rf"^(max\.|<=|min\.) {NUM} (\S+)$")


def f(s: str) -> float:
    return float(s.replace(",", "."))


def inside_visible(wert: str, tol: str) -> bool:
    """Decide in/out of tolerance from the two visible strings only (reference parser)."""
    m = WERT.match(wert)
    assert m, wert
    w, unit = f(m[1]), m[2]
    eps = 1e-9
    if t := SYM.match(tol):
        assert t[3] == unit
        return abs(w - f(t[1])) <= f(t[2]) + eps
    if t := RANGE.match(tol):
        assert t[3] == unit
        return f(t[1]) - eps <= w <= f(t[2]) + eps
    if t := PLUSMINUS.match(tol):
        assert t[4] == unit
        return f(t[1]) - f(t[3]) - eps <= w <= f(t[1]) + f(t[2]) + eps
    t = ONE_SIDED.match(tol)
    assert t, tol
    assert t[3] == unit
    return w <= f(t[2]) + eps if t[1] in ("max.", "<=") else w >= f(t[2]) - eps


def test_verdict_follows_from_visible_values(
    chunk: tuple[dict[str, list[Any]], dict[str, list[Any]]],
) -> None:
    vis, gold = chunk
    bad: list[tuple[Any, ...]] = []
    for i in range(len(vis["qmeldung_id"])):
        ok = inside_visible(vis["messwert"][i], vis["toleranz"][i])
        if ok != gold["messwert_in_toleranz"][i]:
            bad.append((vis["messwert"][i], vis["toleranz"][i], gold["fehlerklasse"][i]))
        if (vis["urteil"][i] == "i.O.") != (gold["fehlerklasse"][i] == "kein Fehler"):
            bad.append(("verdict", vis["urteil"][i], gold["fehlerklasse"][i]))
        if gold["fehlerklasse"][i] == "kein Fehler" and not ok:
            bad.append(("i.O. but outside", vis["messwert"][i], vis["toleranz"][i]))
    assert not bad, bad[:5]


def test_out_of_tolerance_text_names_feature_and_value(
    chunk: tuple[dict[str, list[Any]], dict[str, list[Any]]],
) -> None:
    vis, gold = chunk
    for i in range(len(vis["qmeldung_id"])):
        if not gold["messwert_in_toleranz"][i] and not gold["noise"][i]:
            assert vis["merkmal"][i] in vis["freitext"][i]
            assert vis["messwert"][i] in vis["freitext"][i]


def test_gold_deviation_and_units(chunk: tuple[dict[str, list[Any]], dict[str, list[Any]]]) -> None:
    vis, gold = chunk
    for i in range(len(vis["qmeldung_id"])):
        m = WERT.match(vis["messwert"][i])
        assert m
        assert abs(f(m[1]) - gold["messwert"][i]) < 1e-9
        assert m[2] == gold["einheit"][i]
        assert (gold["abweichung_mm"][i] is not None) == (gold["einheit"][i] == "mm")


def test_class_mix_matches_config(chunk: tuple[dict[str, list[Any]], dict[str, list[Any]]]) -> None:
    _, gold = chunk
    n = len(gold["fehlerklasse"])
    out = sum(not x for x in gold["messwert_in_toleranz"]) / n
    ok = sum(x == "kein Fehler" for x in gold["fehlerklasse"]) / n
    assert abs(out - CFG.p["massfehler"]) < 0.03
    assert abs(ok - (1 - CFG.p["massfehler"] - CFG.p["sonstiger_fehler"])) < 0.03


def test_suppliers_deterministic_and_commercial() -> None:
    a, b = q.lieferanten(1, 50), q.lieferanten(1, 50)
    assert a == b
    assert len(set(a)) == 50
    assert all(s.endswith(q.RECHTSFORMEN) for s in a)
    assert q.lieferanten(2, 50) != a


def test_rows_per_scale(tmp_path: Path) -> None:
    m = gen.generate(REPO, "S", tmp_path, workers=1, table="qualitaetsmeldungen")
    assert m["rows"] == 1000
    for name in ("cp1252.csv", "utf8.csv", "parquet", "gold.parquet"):
        assert m["files"][name]["bytes"] > 0
