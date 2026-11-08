"""Generator claims: determinism, prefix stability, gold ↔ visible consistency, configured rates."""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import re
from pathlib import Path
from typing import Any

import pytest

from zj_datagen import gen, lexicon, noise
from zj_datagen import stoermeldungen as sm

REPO = Path(__file__).resolve().parents[2]
LEX = lexicon.load(REPO / "spec" / "lexicon")
CFG = sm.Config.from_dict(gen.load_config(REPO))


@pytest.fixture(scope="module")
def chunk0() -> tuple[dict[str, list[Any]], dict[str, list[Any]]]:
    return sm.generate_chunk(LEX, CFG, 0, CFG.chunk_rows)


# --- reference parsers (PLAN §7.2 rules, test-only; the real spec is spec/track_a.md in Phase 4) ---
DATE_DE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{2}|\d{4})$")
DATE_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
NUM = re.compile(r"^(-?)(\d{1,3}(?:[.  ]\d{3})*|\d+)(?:,(\d{1,2}))?\s*(?:€|EUR)?$")
DUR = re.compile(r"^(?:ca\. )?(?:(\d+)-)?(\d+(?:,\d+)?)\s*(h|Std\.|Stunden?|min|Minuten)$")


def parse_date(s: str) -> str | None:
    if m := DATE_ISO.match(s):
        y, mo, d = (int(x) for x in m.groups())
    elif m := DATE_DE.match(s):
        d, mo, y = (int(x) for x in m.groups())
        y = 2000 + y if y < 100 else y
    else:
        return None
    try:
        return dt.date(y, mo, d).isoformat()
    except ValueError:
        return None


def parse_eur(s: str) -> float | None:
    m = NUM.match(s)
    if not m:
        return None
    sign, whole, frac = m.groups()
    v = int(re.sub(r"[.  ]", "", whole)) + (int(frac.ljust(2, "0")) / 100 if frac else 0)
    return -v if sign else v


def parse_minutes(s: str) -> int | None:
    m = DUR.match(s)
    if not m:
        return None
    _, num, unit = m.groups()
    val = float(num.replace(",", "."))
    return round(val * 60) if unit in ("h", "Std.", "Stunde", "Stunden") else round(val)


def test_visible_formats_parse_back_to_gold(
    chunk0: tuple[dict[str, list[Any]], dict[str, list[Any]]],
) -> None:
    vis, gold = chunk0
    bad: list[str] = []
    for i in range(len(vis["meldung_id"])):
        if parse_date(vis["meldungsdatum"][i]) != gold["datum_iso"][i]:
            bad.append(f"date {vis['meldungsdatum'][i]!r} → {gold['datum_iso'][i]}")
        if parse_minutes(vis["stillstand"][i]) != gold["stillstand_minuten"][i]:
            bad.append(f"downtime {vis['stillstand'][i]!r} → {gold['stillstand_minuten'][i]}")
        got = parse_eur(vis["kosten"][i])
        exp = gold["kosten_eur"][i]
        if (got is None) != (exp is None) or (got is not None and exp is not None and abs(got - exp) > 1e-9):
            bad.append(f"costs {vis['kosten'][i]!r} → {exp}")
    assert not bad, f"{len(bad)} mismatches, first: {bad[:5]}"


def test_deterministic_files(tmp_path: Path) -> None:
    a = gen.generate(REPO, "S", tmp_path / "a", workers=2)
    b = gen.generate(REPO, "S", tmp_path / "b", workers=3)  # different parallelism, same bytes
    for name in ("cp1252.csv", "utf8.csv", "parquet", "gold.parquet"):
        assert a["files"][name]["sha256"] == b["files"][name]["sha256"], name
    assert json.loads((tmp_path / "a/stoermeldungen/S/manifest.json").read_text())["rows"] == 10_000


def test_prefix_stable() -> None:
    small = dataclasses.replace(CFG, chunk_rows=100)
    full_v, full_g = sm.generate_chunk(LEX, small, 2, 100)
    part_v, part_g = sm.generate_chunk(LEX, small, 2, 37)  # shorter last chunk
    for c in sm.VISIBLE:
        assert part_v[c] == full_v[c][:37], c
    assert part_g["noise"] == full_g["noise"][:37]
    assert full_v["meldung_id"][0] == str(sm.ID_BASE + 200)  # row index = chunk * chunk_rows + j


def test_rates_match_config(chunk0: tuple[dict[str, list[Any]], dict[str, list[Any]]]) -> None:
    vis, gold = chunk0
    n = len(vis["meldung_id"])
    tags = [set(t.split(",")) if t else set[str]() for t in gold["noise"]]

    def rate(pred: list[bool]) -> float:
        return sum(pred) / n

    checks = {
        "mojibake": (rate(["mojibake" in t for t in tags]), CFG.noise["mojibake"], 0.01),
        "typo": (rate(["typo" in t for t in tags]), CFG.noise["typo"], 0.008),
        "translit": (rate(["translit" in t for t in tags]), CFG.noise["translit"], 0.02),
        "sicherheit": (rate(gold["sicherheitsrelevant"]), CFG.p["sicherheit"], 0.01),
        "stillstand_leer": (rate([v == "" for v in vis["stillstand"]]), CFG.p["stillstand_leer"], 0.02),
        "kosten_ka": (rate([v == "k.A." for v in vis["kosten"]]), CFG.p["kosten_ka"], 0.005),
        "datum_ungueltig": (rate([g is None for g in gold["datum_iso"]]), CFG.p["datum_ungueltig"], 0.003),
        "werk_selectivity": (rate([w == "Duisburg" for w in vis["werk"]]), 1 / 28, 0.012),
    }
    off = {k: v for k, v in checks.items() if abs(v[0] - v[1]) > v[2]}
    assert not off, off


def test_mojibake_rows_repair_cleanly(chunk0: tuple[dict[str, list[Any]], dict[str, list[Any]]]) -> None:
    vis, gold = chunk0
    moji = [t for t, g in zip(vis["meldungstext"], gold["noise"], strict=True) if "mojibake" in g]
    assert moji
    assert all("Ã" not in noise.repair_mojibake(t) and "â‚¬" not in noise.repair_mojibake(t) for t in moji)


def test_safety_rows_contain_trigger(chunk0: tuple[dict[str, list[Any]], dict[str, list[Any]]]) -> None:
    vis, gold = chunk0
    trig = [noise.transliterate(t.lower()) for t in LEX.sicherheit_trigger]
    for text, sicher, tags in zip(
        vis["meldungstext"], gold["sicherheitsrelevant"], gold["noise"], strict=True
    ):
        if sicher and "typo" not in tags:
            folded = noise.transliterate(noise.repair_mojibake(text).lower())
            assert any(t in folded for t in trig), text


def test_component_named_in_clean_text(chunk0: tuple[dict[str, list[Any]], dict[str, list[Any]]]) -> None:
    vis, gold = chunk0
    by_name = {k.name: k for k in LEX.komponenten}
    for text, name, tags in zip(vis["meldungstext"], gold["komponente"], gold["noise"], strict=True):
        if tags:
            continue
        k = by_name[name]
        forms = [f for f, _ in k.formen] + ([k.stamm] if k.stamm else [])
        assert any(f.lower() in text.lower() for f in forms), (name, text)
