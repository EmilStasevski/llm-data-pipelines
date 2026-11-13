"""reports/WINNER.md (German) and reports/WINNER.en.md from results/zj.duckdb (PLAN §11.1, `task report`).

Per track (main scale): drop runs that failed verification (view ok_runs) or the winner gate (Tracks D/E:
quality ≥ 0.9 × best); fastest = min median wall time; most efficient = min median gross energy; Pareto front
over (wall, energy); ZJ score = √((T/T_best)·(E/E_best)). Anti-pattern baselines are never winners. For
Track E the production rule (rule 5): the fastest passing pipeline, flagged for an ADR + user decision when
its energy is > 1.5× the most efficient or it needs > 2 more services than the next-fastest.
Phase 9 production stacks (ADR 0025) are excluded from every ranking and compared with the candidate instead.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import duckdb

from zj_analysis.report import (
    DETAIL_SQL,
    GATE,
    PRODUCTION_STACKS,
    TRACKS,
    Summary,
    accumulate,
    fmt_num,
    median_of,
    passes_gate,
    section_filter,
    summaries,
)

MAIN_SCALE = {"A": "L", "B": "M", "C": "M", "C-hybrid": "M", "C-lexical": "M", "D": "D2k", "E": "L"}
ENERGY_RATIO, EXTRA_SERVICES = 1.5, 2


@dataclass(frozen=True)
class Entry:
    s: Summary
    gate: bool | None  # None = no quality gate for this track
    quality: float | None
    services: list[str]


def _details(
    con: duckdb.DuckDBPyConnection, section: str, scale: str
) -> tuple[dict[str, dict[str, list[float]]], dict[str, list[str]]]:
    acc: dict[str, dict[str, list[float]]] = {}
    svc: dict[str, list[str]] = {}
    for c, sc, st, ex, q, rows in con.execute(DETAIL_SQL, list(section_filter(section))).fetchall():
        if sc != scale:
            continue
        accumulate(acc.setdefault(str(c), {}), st, ex, q, rows)
        extra = cast("dict[str, Any]", json.loads(ex) if ex else {})
        if isinstance(extra.get("services"), list):
            svc[str(c)] = [str(x) for x in cast("list[Any]", extra["services"])]
    return acc, svc


def entries(con: duckdb.DuckDBPyConnection, section: str, include_stacks: bool = False) -> list[Entry]:
    scale = MAIN_SCALE[section]
    rows = [s for s in summaries(con, section) if s.scale == scale and not s.anti]
    if not include_stacks:
        rows = [s for s in rows if s.contestant not in PRODUCTION_STACKS]
    task = section_filter(section)[1]
    gate = GATE.get(task)
    acc, svc = _details(con, section, scale) if rows and (gate or section == "E") else ({}, {})
    best = max(
        (q for d in acc.values() if gate and (q := median_of(d.get(gate[0], []))) is not None), default=None
    )
    out: list[Entry] = []
    for s in rows:
        d = acc.get(s.contestant, {})
        q = median_of(d.get(gate[0], [])) if gate else None
        out.append(Entry(s, passes_gate(d, gate, best) if gate else None, q, svc.get(s.contestant, [])))
    return out


def pareto(es: list[Entry]) -> list[Entry]:
    pts = [e for e in es if e.s.e_gross is not None]
    front = [e for e in pts if not any(
        o.s.wall <= e.s.wall and cast("float", o.s.e_gross) <= cast("float", e.s.e_gross)
        and (o.s.wall < e.s.wall or cast("float", o.s.e_gross) < cast("float", e.s.e_gross)) for o in pts)]  # fmt: skip
    return sorted(front, key=lambda e: e.s.wall)


HEAD = {
    "de": ["Wettbewerber", "Wall s", "Energie J", "ZJ-Score", "Qualität", "Gate"],
    "en": ["Contestant", "wall s", "energy J", "ZJ score", "quality", "gate"],
}
TXT: dict[str, dict[str, str]] = {
    "de": {"title": "# ZeilenJoule: Gewinner je Track", "gen": "> Generiert von `task report` aus `results/zj.duckdb` (PLAN §11.1); keine Zahl von Hand.",
           "scale": "Skala", "fast": "Schnellster", "eff": "Effizientester", "front": "Pareto-Front (Zeit ↔ Energie)",
           "none": "_Noch keine gewerteten Läufe._",
           "prod": "## Produktions-Stack (Regel 5)", "cand": "Kandidat = schnellste End-to-End-Pipeline, die alle Gates besteht",
           "ratio": "Energie im Verhältnis zur effizientesten Pipeline", "svc": "Dienste", "next": "nächstschnellere Pipeline",
           "flag": "⚠ Entscheidung nötig (ADR + Gate 2)", "ok": "✓ keine Abwägung nötig", "e2e_none": "_Noch keine gewertete End-to-End-Pipeline._",
           "arch": "Architekturvorschlag: `docs/ARCHITEKTUR.md`.", "ns": "nicht gemessen",
           "stack": "## Phase 9: Produktions-Stack gegenüber dem Gewinner als Skript",
           "stack_intro": "Gleiche CSV-Bytes, gleiche 300 Fragen, gleicher Prüfer; der Stack läuft als Dienst (Compose: Gateway, Worker, River, PostgreSQL, TEI, vLLM) und wird über seine HTTP-API bedient (ADR 0025). Seit ADR 0027 (Common-Terms-Volltext) weicht die lexikalische Kandidatenmenge des Stacks vom Skript ab; die Tabelle zeigt die neueste Sitzung. Δ = Stack ÷ Skript - 1.",
           "metric": "Kennzahl", "stack_none": "_Noch kein gewerteter Lauf des Produktions-Stacks._",
           "e04": "Nicht gebaut: e04_pure_rust (reines Rust, PLAN §11) – vor Gate 2 bewusst ausgelassen (ADR 0020); die Rust-Bausteine a16/a17 sind in e03/e05 enthalten."},
    "en": {"title": "# ZeilenJoule: winners per track", "gen": "> Generated by `task report` from `results/zj.duckdb` (PLAN §11.1); no number typed by hand.",
           "scale": "scale", "fast": "Fastest", "eff": "Most efficient", "front": "Pareto front (time ↔ energy)",
           "none": "_No counted runs yet._",
           "prod": "## Production stack (rule 5)", "cand": "Candidate = fastest end-to-end pipeline passing every gate",
           "ratio": "energy relative to the most efficient pipeline", "svc": "services", "next": "next-fastest pipeline",
           "flag": "⚠ decision needed (ADR + Gate 2)", "ok": "✓ no trade-off needed", "e2e_none": "_No counted end-to-end pipeline yet._",
           "arch": "Architecture proposal: `docs/ARCHITEKTUR.md`.", "ns": "not measured",
           "stack": "## Phase 9: production stack versus the winner as a script",
           "stack_intro": "Same CSV bytes, same 300 questions, same scorer; the stack runs as a service (compose: gateway, worker, River, PostgreSQL, TEI, vLLM) and is driven through its HTTP API (ADR 0025). Since ADR 0027 (common-terms full text) the stack's lexical candidate set differs from the script; the table shows the latest session. Δ = stack ÷ script - 1.",
           "metric": "metric", "stack_none": "_No counted run of the production stack yet._",
           "e04": "Not built: e04_pure_rust (pure Rust, PLAN §11) – deliberately left out before Gate 2 (ADR 0020); the Rust building blocks a16/a17 are part of e03/e05."},
}  # fmt: skip


def _row(e: Entry, best_t: float, best_e: float | None, lang: str) -> list[str]:
    zj = ((e.s.wall / best_t) * (e.s.e_gross / best_e)) ** 0.5 if e.s.e_gross is not None and best_e else None
    gate = "–" if e.gate is None else ("✓" if e.gate else "✗")
    return [f"`{e.s.contestant}`", fmt_num(e.s.wall, 2, lang), fmt_num(e.s.e_gross, 1, lang), fmt_num(zj, 2, lang),
            "–" if e.quality is None else fmt_num(e.quality, 3, lang), gate]  # fmt: skip


def section_md(con: duckdb.DuckDBPyConnection, section: str, lang: str) -> tuple[list[str], list[Entry]]:
    t = TXT[lang]
    title = TRACKS[section][lang][0]
    out = [f"## {title} – {t['scale']} {MAIN_SCALE[section]}", ""]
    es = [e for e in entries(con, section) if e.gate is not False]
    if not es:
        return [*out, t["none"], ""], []
    fast = min(es, key=lambda e: e.s.wall)
    eff = min(
        (e for e in es if e.s.e_gross is not None), key=lambda e: cast("float", e.s.e_gross), default=None
    )
    best_e = eff.s.e_gross if eff else None
    out += [f"- **{t['fast']}:** `{fast.s.contestant}` ({fmt_num(fast.s.wall, 2, lang)} s)",
            f"- **{t['eff']}:** " + (f"`{eff.s.contestant}` ({fmt_num(eff.s.e_gross, 1, lang)} J)" if eff else t["ns"]),
            "", f"**{t['front']}:**", "", "| " + " | ".join(HEAD[lang]) + " |", "|" + "---|" * len(HEAD[lang])]  # fmt: skip
    out += ["| " + " | ".join(_row(e, fast.s.wall, best_e, lang)) + " |" for e in pareto(es)]
    return [*out, ""], es


def production_md(es: list[Entry], lang: str) -> list[str]:
    t = TXT[lang]
    out = [t["prod"], "", f"_{t['e04']}_", ""]
    if not es:
        return [*out, t["e2e_none"], ""]
    ranked = sorted(es, key=lambda e: e.s.wall)
    cand = ranked[0]
    eff = min((e.s.e_gross for e in es if e.s.e_gross is not None), default=None)
    ratio = cand.s.e_gross / eff if cand.s.e_gross is not None and eff else None
    nxt = ranked[1] if len(ranked) > 1 else None
    extra = len(cand.services) - len(nxt.services) if nxt else 0
    flag = (ratio is not None and ratio > ENERGY_RATIO) or extra > EXTRA_SERVICES
    out += [f"- {t['cand']}: **`{cand.s.contestant}`** ({fmt_num(cand.s.wall, 1, lang)} s, "
            f"{fmt_num(cand.s.e_gross, 0, lang)} J)",
            f"- {t['ratio']}: {fmt_num(ratio, 2, lang)} (> {fmt_num(ENERGY_RATIO, 1, lang)} → ADR)",
            f"- {t['svc']}: {', '.join(cand.services) or '–'}"
            + (f"; {t['next']} `{nxt.s.contestant}`: {', '.join(nxt.services) or '–'} (Δ {extra:+d}, > {EXTRA_SERVICES} → ADR)" if nxt else ""),
            f"- {t['flag'] if flag else t['ok']}", "", t["arch"], ""]  # fmt: skip
    return out


def stack_md(stacks: list[Entry], cand: Entry | None, lang: str) -> list[str]:
    """Phase 9: each production stack next to the production candidate (same scale, same gates)."""
    t = TXT[lang]
    out = [t["stack"], "", t["stack_intro"], ""]
    if not stacks or cand is None:
        return [*out, t["stack_none"], ""]

    def delta(a: float | None, b: float | None) -> str:
        return (
            "–"
            if a is None or not b
            else f"{(a / b - 1) * 100:+.1f} %".replace(".", "," if lang == "de" else ".")
        )

    for st in stacks:
        rows = [("Wall s", st.s.wall, cand.s.wall, 1), (HEAD[lang][2], st.s.e_gross, cand.s.e_gross, 0),
                (HEAD[lang][4], st.quality, cand.quality, 3)]  # fmt: skip
        out += [f"| {t['metric']} | `{st.s.contestant}` | `{cand.s.contestant}` | Δ |", "|---|---|---|---|"]
        out += [
            f"| {n} | {fmt_num(a, d, lang)} | {fmt_num(b, d, lang)} | {delta(a, b)} |" for n, a, b, d in rows
        ]
        gate = "✓" if st.gate else "✗"
        out += [f"| Gate | {gate} | {'✓' if cand.gate else '✗'} | |", ""]
    return out


def render(con: duckdb.DuckDBPyConnection, lang: str) -> str:
    t = TXT[lang]
    out = [t["title"], "", t["gen"], ""]
    e2e: list[Entry] = []
    for section in ("A", "B", "C", "C-hybrid", "C-lexical", "D", "E"):
        lines, es = section_md(con, section, lang)
        out += lines
        if section == "E":
            e2e = es
    cand = min(e2e, key=lambda e: e.s.wall) if e2e else None
    stacks = [e for e in entries(con, "E", include_stacks=True) if e.s.contestant in PRODUCTION_STACKS]
    return "\n".join(out + production_md(e2e, lang) + stack_md(stacks, cand, lang))


def write(repo: Path, con: duckdb.DuckDBPyConnection) -> list[str]:
    (repo / "reports" / "WINNER.md").write_text(render(con, "de"), encoding="utf-8")
    (repo / "reports" / "WINNER.en.md").write_text(render(con, "en"), encoding="utf-8")
    return ["reports/WINNER.md", "reports/WINNER.en.md"]
