"""Turn one downloaded ESEF xhtml report into text, facts and gold KPIs + a manifest row."""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl

from . import ixbrl
from .lang import guess_lang

KPI_NAMES = list(ixbrl.KPI_CONCEPTS.values())


def process_report(report: Path, interim: Path, gold: Path) -> dict:
    data = report.read_bytes()
    interim.mkdir(parents=True, exist_ok=True)
    gold.mkdir(parents=True, exist_ok=True)

    facts = ixbrl.parse_facts(data)
    text = ixbrl.xhtml_to_text(data)
    kpis = ixbrl.gold_kpis(facts)

    stem = report.stem
    (interim / f"{stem}.txt").write_text(text, encoding="utf-8")
    facts.write_parquet(interim / f"{stem}.facts.parquet")
    (gold / f"{stem}.kpis.json").write_text(json.dumps(kpis, indent=2), encoding="utf-8")

    return {
        "file": report.name,
        "lang": guess_lang(text),
        "n_chars": len(text),
        "n_facts": facts.height,
        "n_kpis": len(kpis),
        **{k: kpis.get(k) for k in KPI_NAMES},
    }


def manifest_schema() -> dict:
    return {
        "file": pl.Utf8,
        "lang": pl.Utf8,
        "n_chars": pl.Int64,
        "n_facts": pl.Int64,
        "n_kpis": pl.Int64,
        **{k: pl.Float64 for k in KPI_NAMES},
    }
