"""Real-data corpus + label-free query set for Tracks C/E (ADR 0012).

`data/real/{thru,ted,ba}` → `data/real/corpus/real_nrw.parquet`: one German `doc_text` per facility /
tender / job ad, with the filter columns `quelle`, `ort`, `datum` (same role as werk/datum in the
synthetic data). There are no gold labels, so the queries are **known-item** queries: the title of a
document whose title is unique in the corpus. That gives two label-free metrics: recall@10 vs. exact
(filtered) brute force, and the known-item hit rate.

thru.de: only the latest reporting year (2024; its rows carry no `kennnummer`, so the id is a content
hash). Identical facility texts are de-duplicated.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

STREAM_REAL = 13
CLASSES = ("none", "ort", "datum", "kombiniert")
DATE_WINDOW_DAYS = 15

SCHEMA = pa.schema(
    [
        ("doc_id", pa.string()),
        ("quelle", pa.string()),
        ("ort", pa.string()),
        ("datum", pa.date32()),
        ("titel", pa.string()),
        ("doc_text", pa.string()),
    ]
)


def _parts(*kv: tuple[str, Any]) -> str:
    return " | ".join(f"{k}: {v}" for k, v in kv if v not in (None, ""))


def _date(s: str | None) -> dt.date | None:
    return dt.date.fromisoformat(s) if s else None


def build_rows(real: Path) -> list[dict[str, Any]]:
    con = duckdb.connect()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    thru = con.execute(
        "SELECT ort, nace_text, prtr_beschreibung, ivu_beschreibung, branchengruppe FROM read_parquet(?) "
        "WHERE jahr = (SELECT max(jahr) FROM read_parquet(?)) "
        "ORDER BY ort, nace_text, prtr_beschreibung, ivu_beschreibung",
        [str(real / "thru" / "thru_betriebe_nrw.parquet")] * 2,
    ).fetchall()
    for ort, nace, prtr, ivu, branche in thru:
        text = _parts(
            ("Betrieb", ort), ("Branche", branche), ("Wirtschaftszweig", nace),
            ("PRTR-Tätigkeit", prtr), ("IVU-Tätigkeit", ivu),
        )  # fmt: skip
        if text in seen:
            continue
        seen.add(text)
        h = hashlib.sha1(text.encode(), usedforsecurity=False).hexdigest()[:12]
        rows.append({"doc_id": f"thru:{h}", "quelle": "thru", "ort": ort, "datum": None,
                     "titel": prtr or nace, "doc_text": text})  # fmt: skip
    ted = con.execute(
        "SELECT publication_number, publication_date, title, description, buyer_name, buyer_city, cpv "
        "FROM read_parquet(?) ORDER BY publication_number",
        [str(real / "ted" / "ted_nrw.parquet")],
    ).fetchall()
    for pub, date, title, desc, buyer, city, cpv in ted:
        text = _parts(
            ("Ausschreibung", title), ("Auftraggeber", f"{buyer}, {city}" if buyer else city),
            ("CPV", cpv), ("Beschreibung", desc),
        )  # fmt: skip
        rows.append({"doc_id": f"ted:{pub}", "quelle": "ted", "ort": city, "datum": _date(date),
                     "titel": title, "doc_text": text})  # fmt: skip
    ba = con.execute(
        "SELECT referenznummer, titel, alle_berufe, firma, ort, angebotsart, erstveroeffentlichung, "
        "vertragsdauer FROM read_parquet(?) ORDER BY referenznummer",
        [str(real / "ba" / "ba_werkstudent_nrw.parquet")],
    ).fetchall()
    for ref, titel, berufe, firma, ort, art, erst, dauer in ba:
        text = _parts(
            ("Stellenanzeige", titel), ("Berufe", berufe), ("Firma", firma), ("Ort", ort),
            ("Angebotsart", art), ("Vertragsdauer", dauer),
        )  # fmt: skip
        rows.append({"doc_id": f"ba:{ref}", "quelle": "ba", "ort": ort, "datum": _date(erst),
                     "titel": titel, "doc_text": text})  # fmt: skip
    return rows


def _filter_matches(rows: list[dict[str, Any]], flt: dict[str, Any]) -> int:
    lo = dt.date.fromisoformat(flt["datum_von"]) if "datum_von" in flt else None
    hi = dt.date.fromisoformat(flt["datum_bis"]) if "datum_bis" in flt else None
    n = 0
    for r in rows:
        if "ort" in flt and r["ort"] != flt["ort"]:
            continue
        if lo is not None and (r["datum"] is None or not lo <= r["datum"] <= hi):
            continue
        n += 1
    return n


def queries(rows: list[dict[str, Any]], seed: int, per_class: int = 250) -> list[dict[str, Any]]:
    """Known-item queries: unique titles of TED/BA documents (they have a date for the date filters)."""
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["titel"]] = counts.get(r["titel"], 0) + 1
    pool = [r for r in rows if r["quelle"] in ("ted", "ba") and counts[r["titel"]] == 1 and r["ort"]]
    rng = np.random.default_rng([seed, STREAM_REAL])
    picks = rng.permutation(len(pool))
    if len(picks) < per_class * len(CLASSES):
        raise RuntimeError(f"only {len(picks)} unique-title documents for {per_class * len(CLASSES)} queries")
    out: list[dict[str, Any]] = []
    for i, cls in enumerate(CLASSES):
        for j in picks[i * per_class : (i + 1) * per_class]:
            doc = pool[int(j)]
            flt: dict[str, Any] = {}
            if cls in ("ort", "kombiniert"):
                flt["ort"] = doc["ort"]
            if cls in ("datum", "kombiniert"):
                d, w = doc["datum"], dt.timedelta(days=DATE_WINDOW_DAYS)
                flt |= {"datum_von": (d - w).isoformat(), "datum_bis": (d + w).isoformat()}
            out.append({"qid": f"r{len(out) + 1:04d}", "text": doc["titel"], "filter_class": cls,
                        "filter": flt, "known_item": doc["doc_id"],
                        "n_filter_match": _filter_matches(rows, flt)})  # fmt: skip
    return out


def build(repo: Path, seed: int) -> dict[str, Any]:
    real = repo / "data" / "real"
    rows = build_rows(real)
    out = real / "corpus"
    out.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=SCHEMA), out / "real_nrw.parquet", compression="zstd")  # pyright: ignore[reportUnknownMemberType]
    qs = queries(rows, seed)
    (repo / "spec" / "queries_c.real.jsonl").write_text(
        "".join(json.dumps(q, ensure_ascii=False) + "\n" for q in qs), encoding="utf-8"
    )
    by_src: dict[str, int] = {}
    for r in rows:
        by_src[r["quelle"]] = by_src.get(r["quelle"], 0) + 1
    sel: dict[str, float] = {}
    for cls in CLASSES:
        ns = [q["n_filter_match"] for q in qs if q["filter_class"] == cls]
        sel[cls] = round(float(np.median(ns)) / len(rows), 4)
    info = {"rows": len(rows), "by_source": by_src, "queries": len(qs),
            "median_filter_selectivity": sel, "seed": seed,
            "sources": ["data/real/thru", "data/real/ted", "data/real/ba"]}  # fmt: skip
    (out / "manifest.json").write_text(
        json.dumps(info, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return info
