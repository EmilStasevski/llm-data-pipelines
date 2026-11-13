"""Key numbers for prose documents (docs/BEWERBUNG.md, docs/blog/*.md) → generated block between markers.

Rule 3: prose never carries a typed number; it points at this block, which `task report` rewrites from
results/zj.duckdb (median of the latest session per contestant and scale, verified runs only).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb

from zj_analysis.readme import quality_d
from zj_analysis.report import PRODUCTION_STACKS, Summary, fmt_num, summaries

BEGIN, END = "<!-- zj:facts:begin -->", "<!-- zj:facts:end -->"
TARGETS = ("docs/BEWERBUNG.md", "docs/blog/wie-viel-energie-kostet-eine-zeile.md")
TRACKS = [
    ("A", "L", "Track A (Parsen + Bereinigen, Skala L)"),
    ("B", "M", "Track B (Embeddings, Skala M)"),
    ("C", "M", "Track C (Vektorsuche, Skala M)"),
    ("E", "L", "Track E (Roh-CSV → RAG, Skala L)"),
]


def _n(v: float | None, d: int = 0) -> str:
    return fmt_num(v, d, "de")


def _span(rs: list[Summary], what: str) -> str:
    fast, slow = rs[0], rs[-1]
    e = ""
    if fast.e_gross and slow.e_gross:
        e = (
            f"; Energie {_n(fast.e_gross)} J gegen {_n(slow.e_gross)} J "
            f"(Faktor {_n(slow.e_gross / fast.e_gross, 0 if slow.e_gross / fast.e_gross >= 10 else 1)})"
        )
    ratio = slow.wall / fast.wall
    return (
        f"- **{what}:** schnellster `{fast.contestant}` {_n(fast.wall, 1 if fast.wall < 100 else 0)} s, "
        f"langsamster `{slow.contestant}` {_n(slow.wall, 1 if slow.wall < 100 else 0)} s "
        f"(Faktor {_n(ratio, 0 if ratio >= 10 else 1)}){e}."
    )


def lines(con: duckdb.DuckDBPyConnection) -> list[str]:
    out: list[str] = []
    for sec, scale, what in TRACKS:
        rs = sorted(
            (s for s in summaries(con, sec) if s.scale == scale and s.contestant not in PRODUCTION_STACKS),
            key=lambda s: s.wall,
        )
        if len(rs) >= 2:
            out.append(_span(rs, what))
    jrow = {
        s.contestant: s.e_gross / s.rows
        for s in summaries(con, "D")
        if s.scale == "D2k" and s.e_gross and s.rows
    }
    q = quality_d(con, "D2k") if jrow else {}
    if jrow and q:
        best = max((c for c in q if c in jrow), key=lambda c: q[c][0])
        part = f"bestes macro-F1 `{best}` {_n(q[best][0], 3)} bei {_n(jrow[best], 1)} J/Zeile"
        if (c := "d11_cascade") in jrow and c in q and c != best:
            part += (
                f"; Kaskade `{c}` {_n(q[c][0], 3)} bei {_n(jrow[c], 1)} J/Zeile "
                f"({_n(100 * (1 - jrow[c] / jrow[best]), 0)} % weniger Energie)"
            )
        out.append(f"- **Track D (LLM-Extraktion, 2 000 Meldungen):** {part}.")
    e = {s.contestant: s for s in summaries(con, "E") if s.scale == "L"}
    if "e06_stack_go" in e and "e03_postgres_only" in e:
        a, b = e["e06_stack_go"], e["e03_postgres_only"]
        de = f", Energie {_n(100 * (a.e_gross / b.e_gross - 1), 1)} %" if a.e_gross and b.e_gross else ""
        out.append(
            f"- **Produktions-Stack (Go, Phase 9):** `e06_stack_go` {_n(a.wall)} s gegen Skript "
            f"`e03_postgres_only` {_n(b.wall)} s (Wall {_n(100 * (a.wall / b.wall - 1), 1)} %{de})."
        )
    has: Any = con.execute("SELECT count(*) FROM ok_runs WHERE task = 'e.load'").fetchone()
    if has and has[0]:
        from zj_analysis import load_curve  # noqa: PLC0415

        for cv in load_curve.curves(con):
            parts: list[str] = []
            for ep, name in (("search", "Suche"), ("ask", "Fragen")):
                last, _ = load_curve.knee(cv.steps, ep)
                if last:
                    parts.append(
                        f"{name} stabil bis {_n(last.rate, 1 if last.rate < 1 else 0)} req/s "
                        f"(p95 {_n(last.p95)} ms)"
                    )
            if parts:
                out.append(
                    f"- **Lastkurve `{cv.contestant}` (Skala {cv.scale}):** {'; '.join(parts)} – "
                    "SLO-Tabelle im Bericht."
                )
    return out


def block(con: duckdb.DuckDBPyConnection) -> str:
    return "\n".join(
        [
            BEGIN,
            "",
            "Generiert von `task report` aus `results/` (Median der neuesten Sitzung, nur "
            "geprüfte Läufe); Wall-Zeit und Energie brutto (GPU + CPU) einer Messung.",
            "",
            *lines(con),
            "",
            END,
        ]
    )


def update(repo: Path, con: duckdb.DuckDBPyConnection) -> list[str]:
    done: list[str] = []
    paths = [(rel, repo / rel) for rel in TARGETS if (repo / rel).exists()]
    if not paths:
        return done
    new = block(con)
    for rel, path in paths:
        text = path.read_text(encoding="utf-8")
        if BEGIN in text and END in text:
            path.write_text(
                text[: text.index(BEGIN)] + new + text[text.index(END) + len(END) :], encoding="utf-8"
            )
            done.append(rel)
    return done
