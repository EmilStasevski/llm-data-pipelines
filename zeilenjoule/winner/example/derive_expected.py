"""Writes example/fragen.json: example questions with answers computed from the generator's gold data.

Run in the repository (needs data/synth from `task data:gen SCALE=S`):
    uv run --project analysis python winner/example/derive_expected.py
The 100 rows of stoermeldungen_100.cp1252.csv are the first 100 records of the S data set (meldung_id
2000000000–2000000099). Questions avoid `schadensart`: the stack estimates it (LLM + classifier),
the CSV has no such column.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[2]
S = ROOT / "data/synth/stoermeldungen/S"
OUT = Path(__file__).with_name("fragen.json")


def main() -> None:
    con = duckdb.connect()
    con.execute(
        f"""CREATE VIEW g AS SELECT g.*, r.meldungstext FROM '{S}/stoermeldungen_gold.parquet' g
        JOIN '{S}/stoermeldungen.parquet' r USING (meldung_id) WHERE meldung_id < '2000000100'"""
    )

    def one(sql: str) -> object:
        row = con.execute(sql).fetchone()
        assert row is not None
        return row[0]

    assert one("SELECT count(*) FROM g") == 100
    top_werk = con.execute("SELECT werk, count(*) n FROM g GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 2").fetchall()
    assert top_werk[0][1] > top_werk[1][1], "tie for the most reports"
    longest = con.execute(
        "SELECT meldung_id, stillstand_minuten FROM g ORDER BY stillstand_minuten DESC NULLS LAST LIMIT 2"
    ).fetchall()
    assert longest[0][1] > longest[1][1], "tie for the longest downtime"

    def ids(where: str) -> list[str]:
        return [r[0] for r in con.execute(f"SELECT meldung_id FROM g WHERE {where} ORDER BY 1").fetchall()]

    def n(sql: str) -> int | float:
        v = one(sql)
        assert isinstance(v, int | float)
        return v if isinstance(v, int) else round(v, 2)

    korrosion = (
        "meldungstext ILIKE '%druckluftleitung%' "
        "AND (meldungstext ILIKE '%korrosion%' OR meldungstext ILIKE '%rost%')"
    )
    fragen = [
        {"frage": "Wie viele Störmeldungen gibt es insgesamt?", "zahl": n("SELECT count(*) FROM g")},
        {
            "frage": "Wie viele Meldungen gab es im Werk Krefeld?",
            "zahl": n("SELECT count(*) FROM g WHERE werk = 'Krefeld'"),
        },
        {
            "frage": "Wie hoch waren die gesamten Kosten in Euro im Werk Lünen?",
            "zahl": n("SELECT sum(kosten_eur) FROM g WHERE werk = 'Lünen'"),
        },
        {
            "frage": "Wie viele Meldungen gab es im Jahr 2025?",
            "zahl": n("SELECT count(*) FROM g WHERE year(datum_iso) = 2025"),
        },
        {
            "frage": "Wie viele sicherheitsrelevante Meldungen gibt es?",
            "zahl": n("SELECT count(*) FILTER (sicherheitsrelevant) FROM g"),
        },
        {"frage": "Welches Werk hat die meisten Meldungen?", "text": top_werk[0][0]},
        {"frage": "Welche Meldung hatte den längsten Stillstand?", "text": longest[0][0]},
        {"frage": "Welche Meldungen beschreiben Korrosion an einer Druckluftleitung?", "ids": ids(korrosion)},
        {"frage": "Bei welchen Meldungen gab es einen Brand?", "ids": ids("meldungstext ILIKE '%brand%'")},
    ]
    OUT.write_text(json.dumps(fragen, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(fragen)} questions)")


if __name__ == "__main__":
    main()
