"""Phase 9 load curve of the production stack (task `e.load`, e07_stack_load, ADR 0026, PLAN §12.3).

Every rate step is a ZJMARK stage, so its wall time and GPU + CPU energy come from the harness like any other
stage; the per-step request counts and latencies come from the contestant's ZJRESULT (`extra.load_steps`).
Medians over the measured reps of the latest session per (contestant, scale). Knee = the highest offered rate
whose errors stay ≤ 1 % and whose p95 stays ≤ 2 × the p95 of the lowest rate; the next step is past the knee.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, cast

import duckdb

from zj_analysis.report import PRODUCTION_STACKS, fmt_num, median_of, summaries

LOAD_SQL = """
WITH latest AS (
  SELECT contestant, scale, arg_max(session_id, ts) AS sid FROM ok_runs WHERE task = 'e.load' GROUP BY ALL)
SELECT contestant, scale, rows, to_json(stages), json_extract(to_json(result), '$.extra'), to_json(idle_w)
FROM ok_runs JOIN latest USING (contestant, scale)
WHERE task = 'e.load' AND session_id = sid
ORDER BY contestant, scale
"""
# PLAN §12.3 targets
SEARCH_SLO_RPS, SEARCH_SLO_P95_MS = 20.0, 100.0
ASK_SLO_FIRST_P95_MS = 1500.0
INGEST_SLO_FACTOR = 0.8
KNEE_ERR, KNEE_P95 = 0.01, 2.0


@dataclass(frozen=True)
class Step:
    endpoint: str
    rate: float
    planned: float
    ok_per_s: float
    err_rate: float
    p50: float | None
    p95: float | None
    p99: float | None
    first_p95: float | None
    j_req: float | None  # gross stage energy (GPU + CPU) / planned requests
    j_req_net: float | None  # minus idle power × stage time


@dataclass(frozen=True)
class Curve:
    contestant: str
    scale: str
    n: int
    rows: int
    ingest_s: float | None
    steps: list[Step]


def _pct(d: Any, key: str) -> float | None:
    v = cast("dict[str, Any]", d or {}).get(key)
    return float(v) if isinstance(v, (int, float)) else None


def curves(con: duckdb.DuckDBPyConnection) -> list[Curve]:
    acc: dict[tuple[str, str], dict[str, Any]] = {}
    for c, sc, rows, st, ex, idle in con.execute(LOAD_SQL).fetchall():
        a = acc.setdefault((str(c), str(sc)), {"n": 0, "rows": int(rows or 0), "ingest": [], "steps": {}})
        a["n"] += 1
        stages = {
            str(s["name"]): s
            for s in cast("list[dict[str, Any]]", json.loads(st) or [])
            if s.get("depth", 0) == 0
        }
        if "ingest" in stages:
            a["ingest"].append(float(stages["ingest"]["t_s"]))
        iw = cast("dict[str, Any]", json.loads(idle) if idle else {})
        idle_w = (iw.get("gpu") or 0.0) + (iw.get("cpu_pkg") or 0.0)
        extra = cast("dict[str, Any]", json.loads(ex) if ex else {})
        for s in cast("list[dict[str, Any]]", extra.get("load_steps") or []):
            key = (str(s["endpoint"]), float(s["rate_rps"]))
            d = a["steps"].setdefault(
                key, {k: [] for k in ("planned", "ok_per_s", "err", "p50", "p95", "p99", "first", "j", "jn")}
            )
            planned = float(s["planned"])
            d["planned"].append(planned)
            d["ok_per_s"].append(float(s["ok_per_s"]))
            d["err"].append(1 - float(s["ok"]) / planned if planned else 1.0)
            lat = s.get("latency_ms")
            for k in ("p50", "p95", "p99"):
                if (v := _pct(lat, k)) is not None:
                    d[k].append(v)
            if (v := _pct(s.get("first_event_ms"), "p95")) is not None:
                d["first"].append(v)
            stg = stages.get(str(s["stage"]))
            parts = [float(x) for k in ("gpu_j", "cpu_j") if stg and (x := stg.get(k)) is not None]
            if stg and parts and planned:
                d["j"].append(sum(parts) / planned)
                d["jn"].append((sum(parts) - idle_w * float(stg["t_s"])) / planned)
    out: list[Curve] = []
    for (c, sc), a in acc.items():
        order = sorted(a["steps"].items(), key=lambda kv: (kv[0][0] != "search", kv[0][1]))
        steps = [Step(ep, rate, *(cast("float", median_of(d[k])) for k in ("planned", "ok_per_s", "err")),
                      *(median_of(d[k]) for k in ("p50", "p95", "p99", "first", "j", "jn")))
                 for (ep, rate), d in order]  # fmt: skip
        out.append(Curve(c, sc, a["n"], a["rows"], median_of(a["ingest"]), steps))
    return out


def knee(steps: list[Step], endpoint: str) -> tuple[Step | None, Step | None]:
    """(last step before the knee, first step past it); either may be None."""
    es = [s for s in steps if s.endpoint == endpoint]
    if not es or es[0].p95 is None:
        return None, es[0] if es else None
    base = es[0].p95
    last: Step | None = None
    for s in es:
        if s.err_rate > KNEE_ERR or s.p95 is None or s.p95 > KNEE_P95 * base:
            return last, s
        last = s
    return last, None


TXT: dict[str, dict[str, str]] = {
    "de": {
        "title": "## Phase 9: Lastkurve des Produktions-Stacks (`e.load`)",
        "intro": "Offene Last (ADR 0026): Anfrage i ist zum Zeitpunkt t0 + i/r fällig, egal ob frühere "
        "beantwortet sind; Latenz ab Fälligkeit (kein *coordinated omission*). Fragen = die 300 "
        "Track-E-Fragen (Suche: Fragetext als Suchtext, k = 10). Jede Stufe ist eine ZJMARK-Stufe; "
        "J/Anfrage = Energie der Stufe (GPU + CPU, brutto bzw. minus Leerlauf) / angebotene Anfragen;"
        " ok/s = richtige Antworten / Stufendauer (inkl. Abarbeiten des Rückstaus). Fehlerquote = "
        "Zeitüberschreitung, 5xx, Abbruch, Überlast des Generators. `/v1/ask` streamt die Router-"
        "Entscheidung als erstes SSE-Ereignis und dann die ganze Antwort (keine Token-Ströme): "
        "„erstes Ereignis“ ersetzt deshalb die TTFT aus PLAN §12.3.",
        "head": "Endpunkt|Angebot req/s|Anfragen|ok/s|Fehler|p50 ms|p95 ms|p99 ms|erstes Ereignis p95 ms|"
        "J/Anfrage|J/Anfrage netto",
        "knee": "Knie", "upto": "stabil bis", "past": "darüber",
        "none_ok": "schon die niedrigste Rate liegt darüber",
        "all_ok": "keine Stufe darüber", "slo": "SLOs (PLAN §12.3)", "target": "Ziel", "measured": "gemessen",
        "s_search": "`/v1/search` p95 < 100 ms bei 20 req/s",
        "s_ask": "`/v1/ask` erstes Ereignis p95 < 1,5 s",
        "s_ingest": "Ingest ≥ 0,8 × Zeilen/s des Track-E-Gewinners",
        "ask_rates": "erfüllt bis {r} req/s", "ask_none": "bei keiner Rate erfüllt",
        "ingest_val": "{v} Zeilen/s (`{c}`, Stufe ingest) gegen 0,8 × {w} = {t} Zeilen/s (`{wc}`, Wall-Zeit)",
        "none": "_Noch keine gewerteten Läufe._", "ns": "nicht gemessen", "runs": "Läufe", "scale": "Skala",
    },
    "en": {
        "title": "## Phase 9: load curve of the production stack (`e.load`)",
        "intro": "Open-loop load (ADR 0026): request i is due at t0 + i/r whether or not earlier ones "
        "returned; latency counts from the due time (no coordinated omission). Queries = the 300 "
        "Track E questions (search: question text as query, k = 10). Every step is a ZJMARK stage; "
        "J/request = stage energy (GPU + CPU, gross or minus idle) / offered requests; ok/s = correct"
        " answers / stage time (incl. draining the backlog). Error rate = timeouts, 5xx, aborted "
        "streams, generator overload. `/v1/ask` streams the router decision as the first SSE event "
        "and then the whole answer (no token stream), so \"first event\" stands in for the TTFT of PLAN"
        " §12.3.",
        "head": "endpoint|offered req/s|requests|ok/s|errors|p50 ms|p95 ms|p99 ms|first event p95 ms|"
        "J/request|J/request net",
        "knee": "knee", "upto": "stable up to", "past": "beyond",
        "none_ok": "even the lowest rate is past it",
        "all_ok": "no step beyond it", "slo": "SLOs (PLAN §12.3)", "target": "target", "measured": "measured",
        "s_search": "`/v1/search` p95 < 100 ms at 20 req/s",
        "s_ask": "`/v1/ask` first event p95 < 1.5 s",
        "s_ingest": "ingest ≥ 0.8 × rows/s of the Track E winner",
        "ask_rates": "met up to {r} req/s", "ask_none": "met at no rate",
        "ingest_val": "{v} rows/s (`{c}`, stage ingest) vs 0.8 × {w} = {t} rows/s (`{wc}`, wall time)",
        "none": "_No counted runs yet._", "ns": "not measured", "runs": "runs", "scale": "scale",
    },
}  # fmt: skip


def _ingest_slo(con: duckdb.DuckDBPyConnection, lang: str) -> tuple[str, str]:
    """Stack ingest rows/s (e06, stage ingest) vs 0.8 × the winner's end-to-end rows/s, Track E main scale."""
    t = TXT[lang]
    has: Any = con.execute("SELECT count(*) FROM ok_runs WHERE task = 'e.rag' AND scale = 'L'").fetchone()
    if not has or not has[0]:
        return t["ns"], "–"
    ss = [s for s in summaries(con, "E") if s.scale == "L"]
    cands = [s for s in ss if s.contestant not in PRODUCTION_STACKS and not s.anti]
    if not cands:
        return t["ns"], "–"
    w = min(cands, key=lambda s: s.wall)
    w_rps = w.rows / w.wall
    rates: list[tuple[str, float]] = []
    rows = con.execute(
        "SELECT contestant, rows, to_json(stages) FROM ok_runs WHERE task = 'e.rag' AND scale = 'L'"
    ).fetchall()
    per: dict[str, list[float]] = {}
    for c, n, st in rows:
        if str(c) not in PRODUCTION_STACKS:
            continue
        for s in cast("list[dict[str, Any]]", json.loads(st) or []):
            if s["name"] == "ingest" and s.get("depth", 0) == 0 and float(s["t_s"]) > 0:
                per.setdefault(str(c), []).append(int(n) / float(s["t_s"]))
    rates = [(c, cast("float", median_of(v))) for c, v in per.items()]
    if not rates:
        return t["ns"], "–"
    c, v = max(rates, key=lambda x: x[1])
    target = INGEST_SLO_FACTOR * w_rps
    txt = t["ingest_val"].format(
        v=fmt_num(v, 0, lang), c=c, w=fmt_num(w_rps, 0, lang), t=fmt_num(target, 0, lang), wc=w.contestant
    )
    return txt, "✓" if v >= target else "✗"


def section_md(con: duckdb.DuckDBPyConnection, lang: str) -> str:
    t = TXT[lang]
    out = [t["title"], "", t["intro"], ""]
    cs = curves(con)
    if not cs:
        return "\n".join([*out, t["none"], ""])
    for cv in cs:
        head = t["head"].split("|")
        out += [f"### `{cv.contestant}` – {t['scale']} {cv.scale} ({cv.n} {t['runs']})", "",
                "| " + " | ".join(head) + " |", "|" + "---|" * len(head)]  # fmt: skip
        for s in cv.steps:
            cells = [f"`/v1/{s.endpoint}`", fmt_num(s.rate, 1 if s.rate < 1 else 0, lang),
                     fmt_num(s.planned, 0, lang), fmt_num(s.ok_per_s, 1, lang),
                     f"{fmt_num(100 * s.err_rate, 1, lang)} %",
                     *(fmt_num(v, 0, lang) for v in (s.p50, s.p95, s.p99, s.first_p95)),
                     fmt_num(s.j_req, 2, lang), fmt_num(s.j_req_net, 2, lang)]  # fmt: skip
            out.append("| " + " | ".join(cells) + " |")
        out.append("")
        for ep in ("search", "ask"):
            last, past = knee(cv.steps, ep)
            if last is None and past is None:
                continue
            if last is None:
                txt = t["none_ok"]
            else:
                txt = f"{t['upto']} {fmt_num(last.rate, 1 if last.rate < 1 else 0, lang)} req/s"
                txt += (
                    f", {t['past']} {fmt_num(past.rate, 1 if past.rate < 1 else 0, lang)} req/s"
                    if past
                    else f" ({t['all_ok']})"
                )
            out.append(f"- {t['knee']} `/v1/{ep}`: {txt}")
        out.append("")
        # SLOs
        s20 = next((s for s in cv.steps if s.endpoint == "search" and s.rate == SEARCH_SLO_RPS), None)
        srch = (t["ns"], "–")
        if s20:
            met = s20.p95 is not None and s20.p95 < SEARCH_SLO_P95_MS and s20.err_rate <= KNEE_ERR
            srch = (f"p95 {fmt_num(s20.p95, 0, lang)} ms, {fmt_num(100 * s20.err_rate, 1, lang)} %",
                    "✓" if met else "✗")  # fmt: skip
        ok_rates = [s.rate for s in cv.steps if s.endpoint == "ask" and s.first_p95 is not None
                    and s.first_p95 < ASK_SLO_FIRST_P95_MS and s.err_rate <= KNEE_ERR]  # fmt: skip
        has_ask = any(s.endpoint == "ask" for s in cv.steps)
        ask = (
            (
                (t["ask_rates"].format(r=fmt_num(max(ok_rates), 1, lang)), "✓")
                if ok_rates
                else (t["ask_none"], "✗")
            )
            if has_ask
            else (t["ns"], "–")
        )
        ing = _ingest_slo(con, lang)
        out += [f"**{t['slo']}:**", "", f"| SLO | {t['measured']} | |", "|---|---|---|",
                f"| {t['s_search']} | {srch[0]} | {srch[1]} |", f"| {t['s_ask']} | {ask[0]} | {ask[1]} |",
                f"| {t['s_ingest']} | {ing[0]} | {ing[1]} |", ""]  # fmt: skip
    return "\n".join(out)
