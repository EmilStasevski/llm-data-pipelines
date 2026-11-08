"""Derived evaluation sets from the gold labels (all seeded, deterministic):

- D2k: fixed 2 000-row sample + disjoint 300-row dev set for the LLM tracks (PLAN §6.1)
- qrels_c.<scale>.jsonl: ~1 000 Track C retrieval queries, 250 per filter class (none / werk /
  date range / combined); relevance is a rule over gold labels (zj_eval.relevance), the file
  records the definition and the grade counts
- questions_e.<scale>.jsonl: ~300 Track E questions (lookup / filtered semantic / aggregation);
  aggregation questions carry their gold SQL
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import numpy as np

from zj_datagen import render
from zj_datagen.lexicon import Komponente, Lexicon
from zj_datagen.writer import read_table, write_table
from zj_eval.relevance import Filter

STREAM_D2K, STREAM_C, STREAM_E = 10, 11, 12
QUARTALE = [
    (y, q) for y in (2024, 2025, 2026) for q in (1, 2, 3, 4) if (y, q) != (2026, 4)
]  # 2026-Q4 incomplete
HALBJAHRE = [(y, h) for y in (2024, 2025, 2026) for h in (1, 2) if (y, h) != (2026, 2)]
SEMANTIC_CLASSES = ("werk", "datum", "kombiniert", "werk_quartal", "werk_halbjahr")
COMBINED_RB = ("Köln", "Münster", "Detmold")  # combined with a quarter ≈ 1 % selectivity (PLAN §9)


SCALE_KEY = {"S": 1, "M": 2, "L": 3, "XL": 4}


def _rng(seed: int, stream: int, scale: str) -> np.random.Generator:
    return np.random.default_rng(np.random.SeedSequence(seed, spawn_key=(stream, SCALE_KEY[scale])))


def _quarter(y: int, q: int) -> tuple[dt.date, dt.date]:
    start = dt.date(y, 3 * q - 2, 1)
    end = dt.date(y + (q == 4), (3 * q) % 12 + 1, 1) - dt.timedelta(days=1)
    return start, end


def _half(y: int, h: int) -> tuple[dt.date, dt.date]:
    return (dt.date(y, 1, 1), dt.date(y, 6, 30)) if h == 1 else (dt.date(y, 7, 1), dt.date(y, 12, 31))


def _de(d: dt.date) -> str:
    return f"{d.day:02d}.{d.month:02d}.{d.year}"


# --------------------------------------------------------------------------------------- D2k
def d2k(scale_dir: Path, out: Path, seed: int, rows: int, dev_rows: int) -> dict[str, Any]:
    vis = read_table(scale_dir / "stoermeldungen.parquet")
    gold = read_table(scale_dir / "stoermeldungen_gold.parquet")
    idx = _rng(seed, STREAM_D2K, "M").choice(vis.num_rows, rows + dev_rows, replace=False)
    out.mkdir(parents=True, exist_ok=True)
    parts = {"d2k": np.sort(idx[:rows]), "dev": np.sort(idx[rows:])}
    for name, ix in parts.items():
        take = [int(i) for i in ix]
        write_table(vis.take(take), out / f"{name}.parquet")
        write_table(gold.take(take), out / f"{name}_gold.parquet")
    return {"source": str(scale_dir.name), "rows": rows, "dev_rows": dev_rows, "disjoint": True}


# ------------------------------------------------------------------------------ query texts
def _target_phrase(lex: Lexicon, k: Komponente, schaden: str, rng: np.random.Generator) -> str:
    """German noun/verb phrase for (component, damage type) with variety incl. compounds."""
    sa = lex.schadensart(schaden)
    form, genus = k.formen[int(rng.integers(len(k.formen)))] if rng.random() < 0.4 else k.formen[0]
    if sa.kompositum and k.stamm and rng.random() < 0.25:
        return k.stamm + sa.kompositum
    tpl = sa.phrasen[int(rng.integers(len(sa.phrasen)))]
    return tpl.replace("{N}", render.nominativ(form, genus)).replace("{A}", render.an_dativ(form, genus))


@dataclass(frozen=True)
class _Window:
    flt: Filter
    phrase: str


def _time(rng: np.random.Generator, kind: str) -> tuple[dt.date, dt.date, int, int]:
    """(from, to, year, quarter-or-half) for kind 'quartal' or 'halbjahr'."""
    if kind == "quartal":
        y, q = QUARTALE[int(rng.integers(len(QUARTALE)))]
        return (*_quarter(y, q), y, q)
    y, h = HALBJAHRE[int(rng.integers(len(HALBJAHRE)))]
    return (*_half(y, h), y, h)


def _filter_for(cls: str, lex: Lexicon, rng: np.random.Generator) -> _Window:
    def werk() -> str:
        return lex.werke[int(rng.integers(len(lex.werke)))].werk

    match cls:
        case "none":
            return _Window(Filter(), "")
        case "werk":
            w = werk()
            return _Window(Filter(werk=w), f" in {w}" if rng.random() < 0.5 else f", Werk {w}")
        case "datum":
            kind = "quartal" if rng.random() < 0.5 else "halbjahr"
            a, b, y, n = _time(rng, kind)
            label = f"{n}. Quartal" if kind == "quartal" else f"{n}. Halbjahr"
            return _Window(Filter(date_from=a, date_to=b), f" im {label} {y}")
        case "kombiniert":
            rb = COMBINED_RB[int(rng.integers(len(COMBINED_RB)))]
            a, b, y, q = _time(rng, "quartal")
            return _Window(
                Filter(regierungsbezirk=rb, date_from=a, date_to=b),
                f" im Regierungsbezirk {rb}, {q}. Quartal {y}",
            )
        case "werk_quartal" | "werk_halbjahr":  # narrow filters for Track E at large scales
            w = werk()
            kind = cls.removeprefix("werk_")
            a, b, y, n = _time(rng, kind)
            phrase = (
                f" in {w} im {n}. Quartal {y}" if kind == "quartal" else f" im Werk {w}, {n}. Halbjahr {y}"
            )
            return _Window(Filter(werk=w, date_from=a, date_to=b), phrase)
        case _:
            raise ValueError(f"unknown filter class {cls!r}")


def _where(flt: Filter) -> tuple[str, list[Any]]:
    conds: list[str] = ["TRUE"]
    params: list[Any] = []
    if flt.werk is not None:
        conds.append("werk = ?")
        params.append(flt.werk)
    if flt.regierungsbezirk is not None:
        conds.append("regierungsbezirk = ?")
        params.append(flt.regierungsbezirk)
    if flt.date_from is not None:
        conds.append("datum_iso >= ?")
        params.append(flt.date_from)
    if flt.date_to is not None:
        conds.append("datum_iso <= ?")
        params.append(flt.date_to)
    return " AND ".join(conds), params


def grade_counts(con: duckdb.DuckDBPyConnection, k: str, s: str, flt: Filter) -> tuple[int, int]:
    """(# grade-2, # grade-1) rows: SQL form of zj_eval.relevance.grade (equivalence is tested)."""
    where, params = _where(flt)
    sql = f"""
        SELECT count(*) FILTER (WHERE g.komponente = ? AND g.schadensart = ?),
               count(*) FILTER (WHERE (g.komponente = ? AND g.schadensart <> ?)
                                   OR (g.schadensart = ? AND g.komponente <> ?
                                       AND e.eq = (SELECT eq FROM eq WHERE name = ?)))
        FROM gold g JOIN eq e ON e.name = g.komponente
        WHERE {where}"""
    r = con.execute(sql, [k, s, k, s, s, k, k, *params]).fetchone()
    assert r is not None
    return int(r[0]), int(r[1])


def _fetch(
    con: duckdb.DuckDBPyConnection, sql: str, params: list[Any] | None = None
) -> list[tuple[Any, ...]]:
    """fetchall() with an explicit row type."""
    return con.execute(sql, params or []).fetchall()


def _sample(rows: list[tuple[Any, ...]], k: int, rng: np.random.Generator) -> list[tuple[Any, ...]]:
    """k distinct rows, seeded (order follows the draw)."""
    return [rows[int(i)] for i in rng.choice(len(rows), k, replace=False)]


def connect(scale_dir: Path, lex: Lexicon) -> duckdb.DuckDBPyConnection:
    """DuckDB with views `gold`, `vis` (the scale's Parquet files) and table `eq` (component groups)."""
    con = duckdb.connect()
    con.read_parquet(str(scale_dir / "stoermeldungen_gold.parquet")).create_view("gold")
    con.read_parquet(str(scale_dir / "stoermeldungen.parquet")).create_view("vis")
    con.execute("CREATE TABLE eq (name VARCHAR, eq VARCHAR)")
    con.executemany("INSERT INTO eq VALUES (?, ?)", [(k.name, k.eq) for k in lex.komponenten])
    return con


# ------------------------------------------------------------------------------- Track C
def queries_c(
    scale_dir: Path, lex: Lexicon, seed: int, scale: str, per_class: int = 250
) -> list[dict[str, Any]]:
    rng = _rng(seed, STREAM_C, scale)
    con = connect(scale_dir, lex)
    out: list[dict[str, Any]] = []
    for cls in ("none", "werk", "datum", "kombiniert"):
        made = tries = 0
        while made < per_class:
            tries += 1
            if tries > per_class * 20:
                raise RuntimeError(
                    f"could not build {per_class} '{cls}' queries with relevant rows at {scale}"
                )
            k = lex.komponenten[int(rng.integers(len(lex.komponenten)))]
            s = k.schaeden[int(rng.integers(len(k.schaeden)))]
            win = _filter_for(cls, lex, rng)
            n2, n1 = grade_counts(con, k.name, s, win.flt)
            if n2 == 0:  # a query must have at least one fully relevant row
                continue
            out.append(
                {
                    "qid": f"c{len(out) + 1:04d}",
                    "text": _target_phrase(lex, k, s, rng) + win.phrase,
                    "filter_class": cls,
                    "filter": win.flt.to_json(),
                    "komponente": k.name,
                    "schadensart": s,
                    "n_grade2": n2,
                    "n_grade1": n1,
                }
            )
            made += 1
    return out


# ------------------------------------------------------------------------------- Track E
UNIQUE_CAUSE = """
    SELECT meldung_id, equipment_nr, werk, datum_iso, ursache FROM (
        SELECT v.meldung_id, v.equipment_nr, v.werk, g.datum_iso, g.ursache,
               count(*) OVER (PARTITION BY v.equipment_nr, v.werk, g.datum_iso) AS n_key
        FROM vis v JOIN gold g USING (meldung_id))
    WHERE n_key = 1 AND ursache IS NOT NULL AND datum_iso IS NOT NULL
    ORDER BY meldung_id"""
UNIQUE_DOWNTIME = """
    SELECT meldung_id, anlage, werk, datum_iso, stillstand_minuten FROM (
        SELECT v.meldung_id, v.anlage, v.werk, g.datum_iso, g.stillstand_minuten,
               count(*) OVER (PARTITION BY v.anlage, v.werk, g.datum_iso) AS n_key
        FROM vis v JOIN gold g USING (meldung_id))
    WHERE n_key = 1 AND stillstand_minuten IS NOT NULL AND datum_iso IS NOT NULL
    ORDER BY meldung_id"""


def _lookup(con: duckdb.DuckDBPyConnection, rng: np.random.Generator, n: int) -> list[dict[str, Any]]:
    """Lookups whose natural key (equipment/line + plant + date) is unique at this scale."""
    cause = _fetch(con, UNIQUE_CAUSE)
    action = _fetch(con, "SELECT meldung_id, massnahme FROM gold WHERE massnahme IS NOT NULL ORDER BY 1")
    downtime = _fetch(con, UNIQUE_DOWNTIME)
    third = n // 3
    out: list[dict[str, Any]] = []
    for mid, eq, werk, d, ursache in _sample(cause, third, rng):
        text = f"Was war die Ursache der Störung an {eq} in {werk} am {_de(d)}?"
        out.append(
            {"type": "lookup", "text": text, "gold_ids": [mid], "answer": ursache, "answer_type": "text"}
        )
    for mid, massnahme in _sample(action, third, rng):
        text = f"Welche Maßnahme wurde bei Meldung {mid} ergriffen?"
        out.append(
            {"type": "lookup", "text": text, "gold_ids": [mid], "answer": massnahme, "answer_type": "text"}
        )
    for mid, anlage, werk, d, minutes in _sample(downtime, n - 2 * third, rng):
        text = f"Wie viele Minuten stand {anlage} in {werk} am {_de(d)} still?"
        out.append(
            {
                "type": "lookup",
                "text": text,
                "gold_ids": [mid],
                "answer": int(minutes),
                "answer_type": "integer",
            }
        )
    return out


def _semantic(
    con: duckdb.DuckDBPyConnection, lex: Lexicon, rng: np.random.Generator, n: int
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    tries = 0
    while len(out) < n:
        tries += 1
        if tries > n * 50:
            raise RuntimeError("could not build enough filtered semantic questions")
        k = lex.komponenten[int(rng.integers(len(lex.komponenten)))]
        s = k.schaeden[int(rng.integers(len(k.schaeden)))]
        # the 3–150 bound below selects the classes that are viable at this scale
        win = _filter_for(SEMANTIC_CLASSES[int(rng.integers(len(SEMANTIC_CLASSES)))], lex, rng)
        where, params = _where(win.flt)
        sql = f"SELECT meldung_id FROM gold WHERE komponente = ? AND schadensart = ? AND {where} ORDER BY 1"
        ids = [str(r[0]) for r in _fetch(con, sql, [k.name, s, *params])]
        if not 3 <= len(ids) <= 150:
            continue
        out.append(
            {
                "type": "filtered_semantic",
                "text": f"Welche Meldungen gibt es zu „{_target_phrase(lex, k, s, rng)}“{win.phrase}?",
                "gold_ids": ids,
                "komponente": k.name,
                "schadensart": s,
                "filter": win.flt.to_json(),
                "answer_type": "id_set",
            }
        )
    return out


# (question template, gold SQL over the gold table, answer type). Parameters come from the lexicon
# (no quotes inside), the SQL is stored with each question so every answer is auditable.
AGG_TEMPLATES = (
    (
        "Wie viele sicherheitsrelevante Störungen gab es je Werk im {q}. Quartal {y}?",
        "SELECT werk, count(*) AS n FROM gold WHERE sicherheitsrelevant "
        "AND year(datum_iso) = {y} AND quarter(datum_iso) = {q} GROUP BY werk ORDER BY werk",
        "table",
    ),
    (
        "Wie hoch waren die Gesamtkosten in Euro für Störungen mit Schadensart {s} "
        "im Regierungsbezirk {rb} im Jahr {y}?",
        "SELECT round(coalesce(sum(kosten_eur), 0), 2) FROM gold "
        "WHERE schadensart = '{s}' AND regierungsbezirk = '{rb}' AND year(datum_iso) = {y}",
        "number",
    ),
    (
        "Wie viele Minuten Stillstand verursachte die Komponente {k} im Werk {w} im Jahr {y}?",
        "SELECT coalesce(sum(stillstand_minuten), 0) FROM gold "
        "WHERE komponente = '{k}' AND werk = '{w}' AND year(datum_iso) = {y}",
        "integer",
    ),
    (
        "Welches Werk hatte im Jahr {y} die meisten Störungen mit Schadensart {s}?",
        "SELECT werk FROM gold WHERE schadensart = '{s}' AND year(datum_iso) = {y} "
        "GROUP BY werk ORDER BY count(*) DESC, werk LIMIT 1",
        "text",
    ),
)


def _aggregation(
    con: duckdb.DuckDBPyConnection, lex: Lexicon, rng: np.random.Generator, n: int
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    rbs = sorted({w.regierungsbezirk for w in lex.werke})
    for i in range(n):
        text_t, sql_t, atype = AGG_TEMPLATES[i % len(AGG_TEMPLATES)]
        y, q = QUARTALE[int(rng.integers(len(QUARTALE)))]
        params = {
            "y": y
            if atype == "table"
            else (2024, 2025)[int(rng.integers(2))],  # sums/max over complete years
            "q": q,
            "s": lex.schadensarten[int(rng.integers(len(lex.schadensarten)))].key,
            "rb": rbs[int(rng.integers(len(rbs)))],
            "k": lex.komponenten[int(rng.integers(len(lex.komponenten)))].name,
            "w": lex.werke[int(rng.integers(len(lex.werke)))].werk,
        }
        sql = sql_t.format(**params)
        rows = _fetch(con, sql)
        if atype == "table":
            answer: Any = [list(r) for r in rows]
        elif atype == "number":
            answer = float(rows[0][0])
        else:
            answer = rows[0][0]
        out.append(
            {
                "type": "aggregation",
                "text": text_t.format(**params),
                "gold_sql": sql,
                "answer": answer,
                "answer_type": atype,
            }
        )
    return out


def questions_e(
    scale_dir: Path, lex: Lexicon, seed: int, scale: str, per_type: int = 100
) -> list[dict[str, Any]]:
    rng = _rng(seed, STREAM_E, scale)
    con = connect(scale_dir, lex)
    qs = (
        _lookup(con, rng, per_type)
        + _semantic(con, lex, rng, per_type)
        + _aggregation(con, lex, rng, per_type)
    )
    return [{"qid": f"e{i + 1:04d}", **q} for i, q in enumerate(qs)]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
