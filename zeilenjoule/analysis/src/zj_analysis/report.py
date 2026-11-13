"""Generate reports/REPORT.de.md and reports/REPORT.md from results/zj.duckdb (`task report`).

Rules (CLAUDE.md): every number comes from results/ (rule 3); only verified, measured, non-warm-up runs
are ranked (rule 4, view ok_runs); disqualified/failed/DNF runs are listed, not hidden; prices and the
CO2 factor come from config/bench.yaml.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import duckdb
import yaml

from zj_analysis.load import build

NM = {"de": "nicht gemessen", "en": "not measured"}
FALLBACK = {"de": " ⚠CPU-Fallback", "en": " ⚠CPU fallback"}  # rule 6: GPU runs that partly ran on CPU
SCALE_ORDER = {"S": 0, "M": 1, "L": 2, "XL": 3}

# per (contestant, scale): medians over the measured reps of the most recent session that has counted runs
# for that pair (sessions are never mixed: each has its own idle baseline/machine state); E_total = gpu +
# cpu_pkg (+ dram), see METHODIK §2; CPU-only contestants get no GPU net energy (ADR 0014: the idle GPU's
# P-state drift between baselines is ±1 W, which made E_net negative on long CPU-only runs)
SUMMARY_SQL = """
WITH latest AS (
  SELECT contestant, scale, arg_max(session_id, ts) AS sid FROM ok_runs
  WHERE track = $1 AND coalesce(task, '') LIKE $2 GROUP BY ALL),
r AS (
  SELECT contestant, scale, rows, anti_pattern, wall_s, energy_confidence, engine,
         CASE WHEN energy_j.gpu IS NULL AND energy_j.cpu_pkg IS NULL THEN NULL
              ELSE coalesce(energy_j.gpu, 0) + coalesce(energy_j.cpu_pkg, 0) + coalesce(energy_j.dram, 0)
         END AS e_gross,
         CASE WHEN energy_net_j.gpu IS NULL AND energy_net_j.cpu_pkg IS NULL THEN NULL
              ELSE CASE WHEN uses_gpu THEN coalesce(energy_net_j.gpu, 0) ELSE 0 END
                   + coalesce(energy_net_j.cpu_pkg, 0) + coalesce(energy_net_j.dram, 0)
         END AS e_net
  FROM ok_runs JOIN latest USING (contestant, scale)
  WHERE track = $1 AND coalesce(task, '') LIKE $2 AND session_id = sid)
SELECT contestant, scale, any_value(rows), bool_or(anti_pattern), count(*), median(wall_s),
       quantile_cont(wall_s, 0.25), quantile_cont(wall_s, 0.75), stddev_samp(wall_s) / avg(wall_s),
       median(e_gross), median(e_net), avg((energy_confidence = 'low')::INT),
       bool_or(coalesce(engine, '') LIKE '%fallback%')
FROM r GROUP BY contestant, scale
"""
PROBLEM_SQL = """
SELECT contestant, scale, status, count(*), any_value(status_detail)
FROM runs WHERE track = ? AND coalesce(task, '') LIKE ? AND NOT warmup AND status <> 'ok'
GROUP BY ALL ORDER BY contestant, scale, status
"""


@dataclass(frozen=True)
class Prices:
    household: float
    industry: float
    co2_g_per_kwh: float


@dataclass(frozen=True)
class Summary:
    contestant: str
    scale: str
    rows: int
    anti: bool
    n: int
    wall: float
    q1: float
    q3: float
    cv: float | None
    e_gross: float | None
    e_net: float | None
    low_conf: float
    cpu_fallback: bool


def fmt_num(v: float | None, digits: int, lang: str) -> str:
    if v is None:
        return NM[lang]
    s = f"{v:,.{digits}f}"
    return s.replace(",", " ").replace(".", ",") if lang == "de" else s.replace(",", " ")


def fmt_sig(v: float | None, digits: int, lang: str) -> str:
    """Significant digits for tiny values (€ per 1M rows are often < 0.001)."""
    if v is None:
        return NM[lang]
    s = f"{v:.{digits}g}"
    if "e" in s:  # avoid scientific notation in tables
        s = f"{v:.{max(0, digits - 1 - int(f'{v:e}'.split('e')[1]))}f}"
    return s.replace(".", ",") if lang == "de" else s


def _table(rows: list[list[str]], header: list[str]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    return "\n".join(lines + ["| " + " | ".join(r) + " |" for r in rows])


def section_filter(section: str) -> tuple[str, str]:
    """(track, task LIKE pattern) of a report section; plain tracks cover all their tasks."""
    return SECTION_TASK.get(section, (section, "%"))


def summaries(con: duckdb.DuckDBPyConnection, section: str) -> list[Summary]:
    rows = con.execute(SUMMARY_SQL, list(section_filter(section))).fetchall()
    return [
        Summary(
            str(r[0]),
            str(r[1]),
            int(r[2]),
            bool(r[3]),
            int(r[4]),
            float(r[5]),
            float(r[6]),
            float(r[7]),
            None if r[8] is None else float(r[8]),
            None if r[9] is None else float(r[9]),
            None if r[10] is None else float(r[10]),
            float(r[11]),
            bool(r[12]),
        )
        for r in rows
    ]


# per track: section title, plural/singular work unit (Track A counts rows, Track B documents), note
TRACKS: dict[str, dict[str, tuple[str, str, str, str]]] = {
    "A": {
        "de": ("Track A: Dataframe- und Zeilenverarbeitung (`a.full`)", "Zeilen", "Zeile", ""),
        "en": ("Track A: dataframe and row processing (`a.full`)", "rows", "row", ""),
    },
    "B": {
        "de": ("Track B: Embeddings (`b.embed`, bge-m3)", "Dokumente", "Dok.",
               "Wall-Zeit inklusive Modell-Laden (Stufe `load`). Verifikation: mittlere Kosinus-Ähnlichkeit "
               "≥ 0,995 zur fp32-Referenz; seit 2026-11-08 zusätzlich der Retrieval-Teil "
               "(spec/track_b.md §3, "
               "M und real). Davor gemessene Läufe wurden ohne ihn geprüft."),
        "en": ("Track B: embeddings (`b.embed`, bge-m3)", "docs", "doc",
               "Wall time includes model loading (stage `load`). Verification: mean cosine similarity "
               "≥ 0.995 to the fp32 reference; since 2026-11-08 also the retrieval part (spec/track_b.md §3, "
               "M and real). Runs measured before were verified without it."),
    },
    "C": {
        "de": ("Track C: Speicherung, Index, Suche (`c.dense`)", "Dokumente", "Dok.",
               "Ein Lauf = leerer Speicher → Laden (`ingest`) → Index (`index`) → 1 000 gefilterte "
               "Top-10-Anfragen nacheinander (`query`) → dieselben aus 16 Client-Threads (`query_c16`), "
               "Wall-Zeit inkl. Server-Start (`load`). "
               "Gewertet nur bei recall@10 ≥ 0,95 "
               "in jeder Filterklasse gegenüber exakter Suche (spec/track_c.md)."),
        "en": ("Track C: storage, indexing, retrieval (`c.dense`)", "docs", "doc",
               "One run = empty store → load (`ingest`) → index (`index`) → 1,000 filtered top-10 "
               "queries one after another (`query`) → the same from 16 client threads (`query_c16`); "
               "wall time includes server start (`load`). "
               "Counted only with recall@10 "
               "≥ 0.95 in every filter class vs. exact search (spec/track_c.md)."),
    },
    "C-lexical": {
        "de": ("Track C: lexikalische Suche (`c.lexical`, BM25)", "Dokumente", "Dok.",
               "Wie `c.dense`, aber gefilterte BM25-Top-10 (Tantivy, deutscher Snowball-Stemmer). "
               "Gewertet nur bei gleichstandsfestem BM25-Recall@10 ≥ 0,95 je Filterklasse "
               "gegenüber der Referenz "
               "(spec/track_c.md, ADR 0017)."),
        "en": ("Track C: lexical search (`c.lexical`, BM25)", "docs", "doc",
               "Like `c.dense` but filtered BM25 top-10 (Tantivy, German Snowball stemmer). "
               "Counted only with tie-aware BM25 recall@10 ≥ 0.95 per filter class vs. the reference "
               "(spec/track_c.md, "
               "ADR 0017)."),
    },
    "C-hybrid": {
        "de": ("Track C: Hybrid-Suche (`c.hybrid`, Dense + lexikalisch, RRF k = 60)", "Dokumente", "Dok.",
               "Jede Engine mit eigenem Vektorindex und eigener Volltextsuche, je 100 Kandidaten, fusioniert "
               "per RRF. Wall-Zeit enthält die Prüfstufe `legs` (beide Kandidatenlisten erneut, bei allen "
               "gleich); Latenz aus Stufe `query`. Postgres `ts_rank` ist kein BM25; SQLite FTS5 hat keinen "
               "deutschen Stemmer. Gewertet nur, wenn Dense-Bein (Recall ≥ 0,95), Fusion (≥ 0,95) und "
               "lexikalisches Bein (Plausibilität) bestehen (ADR 0017)."),
        "en": ("Track C: hybrid search (`c.hybrid`, dense + lexical, RRF k = 60)", "docs", "doc",
               "Each engine with its own vector index and its own full-text search, 100 candidates each, "
               "fused by RRF. Wall time includes the check stage `legs` (both candidate lists again, "
               "the same for "
               "all); latency from stage `query`. Postgres `ts_rank` is not BM25; SQLite FTS5 has no German "
               "stemmer. Counted only if the dense leg (recall ≥ 0.95), the fusion (≥ 0.95) and the lexical "
               "leg (sanity floor) pass (ADR 0017)."),
    },
    "D": {
        "de": ("Track D: LLM-Extraktion je Zeile (`d.extract`)", "Zeilen", "Zeile",
               "Ein Lauf = Server-Start + Modell laden (`load`) → alle Zeilen mit N gleichzeitigen Anfragen "
               "(`extract`) → TTFT-Probe. Gleiche Prompts, temperature 0, max_tokens 256, JSON-Schema als "
               "Constrained Decoding, Reasoning aus. Gewertet nur bei JSON-Gültigkeit ≥ 99,5 % ohne "
               "Reasoning-Ausstoß (spec/track_d.md, ADR 0018). Skala `dev` = Konkurrenz-Sweep (300 Zeilen)."),
        "en": ("Track D: LLM row-wise extraction (`d.extract`)", "rows", "row",
               "One run = server start + model load (`load`) → all rows with N requests in flight "
               "(`extract`) → TTFT probe. Same prompts, temperature 0, max_tokens 256, JSON-schema "
               "constrained "
               "decoding, reasoning off. Counted only with JSON validity ≥ 99.5 % and no reasoning output "
               "(spec/track_d.md, ADR 0018). Scale `dev` = concurrency sweep (300 rows)."),
    },
    "E": {
        "de": ("Track E: End-to-End-RAG (`e.rag`)", "Zeilen", "Zeile",
               "Ein Lauf = Roh-CSV → Track-A-Kandidat (S1–S3) → Track-B-Kandidat (S4, bge-m3) → Schadensart per "
               "Destillation (LLM etikettiert 2 000 Zeilen, linearer Klassifikator den Rest) → Index + SQL-Sicht "
               "(S5) → 300 Fragen über einen Router (SQL ↔ Hybrid-Suche), alle mit Qwen3.5-9B. Wall-Zeit = alles; "
               "Time-to-RAG = Stufen ingest … index. Gewertet bei vollständigen Antworten ohne unsichere SQL "
               "(spec/track_e.md, ADR 0020)."),
        "en": ("Track E: end-to-end RAG (`e.rag`)", "rows", "row",
               "One run = raw CSV → Track A contestant (S1–S3) → Track B contestant (S4, bge-m3) → damage type by "
               "distillation (the LLM labels 2,000 rows, a linear classifier the rest) → index + SQL view (S5) → "
               "300 questions through a router (SQL ↔ hybrid search), all with Qwen3.5-9B. Wall time = everything; "
               "time-to-RAG = stages ingest … index. Counted with complete answers and no unsafe SQL "
               "(spec/track_e.md, ADR 0020)."),
    },
}  # fmt: skip
SECTION_TASK = {
    "C": ("C", "c.dense"),
    "C-lexical": ("C", "c.lexical"),
    "C-hybrid": ("C", "c.hybrid"),
    "D": ("D", "d.extract"),
    "E": ("E", "e.rag"),
}
DETAIL_SQL = """
WITH latest AS (
  SELECT contestant, scale, arg_max(session_id, ts) AS sid FROM ok_runs
  WHERE track = $1 AND coalesce(task, '') LIKE $2 GROUP BY ALL)
SELECT contestant, scale, to_json(stages), json_extract(to_json(result), '$.extra'), to_json(quality), rows
FROM ok_runs JOIN latest USING (contestant, scale)
WHERE track = $1 AND coalesce(task, '') LIKE $2 AND session_id = sid
"""
DETAIL_STAGES = ("load", "ingest", "index", "query")
N_QUERIES = 1000  # spec/track_c.md
# per track: top-level stages shown, and stages whose energy becomes a per-unit column
# (stage → (key, unit divisor: "queries" = N_QUERIES, "rows" = the run's rows, scale factor))
STAGES_BY_TRACK = {"C": DETAIL_STAGES, "D": ("load", "extract", "ttft"),
                   "E": ("ingest", "embed", "label", "index", "answer")}  # fmt: skip
RAG_STAGES = ("ingest", "embed", "label", "index")  # Track E time-to-RAG (spec/track_e.md §2)
# Phase 9 production stacks measured as Track E contestants (ADR 0025): the deployed form of the winner, compared
# with it in WINNER.md but never a winner candidate themselves. Their worker runs parse → embed → label → index
# behind one asynchronous job, so only the top-level stage `ingest` exists.
PRODUCTION_STACKS = frozenset({"e06_stack_go"})
N_QUESTIONS_E = 300
STAGE_ENERGY: dict[str, tuple[str, str, float]] = {
    "query": ("mj_query", "queries", 1e3), "query_c16": ("mj_query_c16", "queries", 1e3),
    "extract": ("j_row", "rows", 1.0),
    "answer": ("j_question", "questions", 1.0),
}  # fmt: skip


def headers(track: str, lang: str) -> list[str]:
    _, units, unit, _ = TRACKS[track][lang]
    if lang == "de":
        return [
            "Wettbewerber",
            "n",
            "Wall s (Median)",
            "IQR s",
            f"{units}/s",
            "E brutto J",
            "E netto J",
            f"mJ/{unit}",
            f"€/1 Mio. {units} (Haushalt)",
            f"€/1 Mio. {units} (Industrie)",
            f"g CO₂/1 Mio. {units}",
            "ZJ-Score",
            "niedr. Konfidenz",
        ]
    return ["Contestant", "n", "wall s (median)", "IQR s", f"{units}/s", "E gross J", "E net J", f"mJ/{unit}",
            f"€/1M {units} (household)", f"€/1M {units} (industry)", f"g CO₂/1M {units}", "ZJ score",
            "low confidence"]  # fmt: skip


def _row(s: Summary, best_wall: float, best_e: float | None, prices: Prices, lang: str) -> list[str]:
    j_row = s.e_gross / s.rows if s.e_gross is not None and s.rows else None

    def per_million(factor: float) -> float | None:
        return j_row * 1e6 / 3.6e6 * factor if j_row is not None else None  # J → kWh × €/kWh or g/kWh

    score = None
    if s.e_gross is not None and best_e:
        score = ((s.wall / best_wall) * (s.e_gross / best_e)) ** 0.5
    cv_flag = " (CV>5 %)" if s.cv is not None and s.cv > 0.05 else ""
    return [
        f"`{s.contestant}`" + (" ⚠anti" if s.anti else "") + (FALLBACK[lang] if s.cpu_fallback else ""),
        str(s.n),
        fmt_num(s.wall, 3, lang) + cv_flag,
        f"{fmt_num(s.q1, 3, lang)}–{fmt_num(s.q3, 3, lang)}",
        fmt_num(s.rows / s.wall if s.wall else None, 2 if s.wall and s.rows / s.wall < 100 else 0, lang),
        fmt_num(s.e_gross, 1, lang),
        fmt_num(s.e_net, 1, lang),
        fmt_num(j_row * 1e3 if j_row is not None else None, 3, lang),
        fmt_sig(per_million(prices.household), 3, lang),
        fmt_sig(per_million(prices.industry), 3, lang),
        fmt_num(per_million(prices.co2_g_per_kwh), 2, lang),
        fmt_num(score, 2, lang),
        f"{100 * s.low_conf:.0f} %",
    ]


def track_section(con: duckdb.DuckDBPyConnection, track: str, prices: Prices, lang: str) -> str:
    de = lang == "de"
    summary = summaries(con, track)
    problems = con.execute(PROBLEM_SQL, list(section_filter(track))).fetchall()
    title_t, units, _, note = TRACKS[track][lang]
    out: list[str] = [f"## {title_t}", ""] + ([note, ""] if note else [])
    if not summary and not problems:
        return "\n".join(out) + ("_Noch keine gemessenen Läufe._" if de else "_No measured runs yet._") + "\n"
    for scale in sorted({s.scale for s in summary}, key=lambda x: SCALE_ORDER.get(x, 9)):
        rows = sorted((s for s in summary if s.scale == scale), key=lambda s: s.wall)  # fastest first
        energies = [s.e_gross for s in rows if s.e_gross is not None]
        best_e = min(energies) if energies else None
        label = "Skala" if de else "Scale"
        title = f"### {label} {scale} ({fmt_num(float(rows[0].rows), 0, lang)} {units})"
        body = [_row(s, rows[0].wall, best_e, prices, lang) for s in rows]
        out += [title, "", _table(body, headers(track, lang)), ""]
    if track.startswith(("C", "D", "E")):
        out += _details(con, track, lang)
    if problems:
        title = (
            "Nicht gewertete Läufe (Regel 4: nicht versteckt)"
            if de
            else "Runs not counted (rule 4: not hidden)"
        )
        header = ["Wettbewerber" if de else "Contestant", "Skala" if de else "Scale", "Status", "n", "Detail"]
        body = [[f"`{p[0]}`", str(p[1]), str(p[2]), str(p[3]), str(p[4] or "")[:120]] for p in problems]
        out += [f"### {title}", "", _table(body, header), ""]
    return "\n".join(out)


def median_of(xs: list[float]) -> float | None:
    return float(statistics.median(xs)) if xs else None


# extra detail columns per Track C task: (key in the accumulated run data, header); "{q}" = mJ per query
DETAIL_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "c.dense": [("qps_c1", "QPS c1"), ("qps_c16", "QPS c16"), ("mj_query", "{q} c1"),
                ("mj_query_c16", "{q} c16"), ("recall_at_10", "recall@10"), ("ndcg_at_10", "nDCG@10"),
                ("hit_at_10", "hit@10")],
    "c.lexical": [("mj_query", "{q} c1"), ("bm25_recall_at_10", "BM25-recall@10"), ("ndcg_at_10", "nDCG@10"),
                  ("ndcg_at_10_kompositum", "nDCG@10 Kompositum"), ("hit_at_10", "hit@10")],
    "c.hybrid": [("mj_query", "{q} c1"), ("ndcg_at_10", "nDCG@10"),
                 ("ndcg_at_10_kompositum", "nDCG Kompositum"),
                 ("lex_ndcg_at_10", "nDCG lex"), ("lex_ndcg_at_10_kompositum", "nDCG lex Komp."),
                 ("dense_ndcg_at_10", "nDCG dense"), ("hit_at_10", "hit@10"), ("lex_hit_at_10", "hit lex"),
                 ("dense_hit_at_10", "hit dense"), ("fusion_agreement", "Fusion")],
    "d.extract": [("rows_per_s", "{rows}/s"), ("output_tok_per_s", "Tok/s"), ("j_row", "{j}"),
                  ("ttft_ms", "TTFT ms"), ("vram_mb", "VRAM MiB"), ("completion_tokens_median", "Tok/{row}"),
                  ("json_validity", "JSON ok"), ("schadensart", "schadensart"), ("komponente", "komponente"),
                  ("macro_f1", "macro-F1")],
}  # fmt: skip
DETAIL_COLUMNS["e.rag"] = [
    ("time_to_rag_s", "Time-to-RAG s"), ("rag_wh", "Energy-to-RAG Wh"), ("j_question", "{jq}"),
    ("label_classifier_holdout_acc", "{cls}"), ("lookup_score", "lookup"), ("filtered_semantic_score", "semantic F1"),
    ("aggregation_score", "{agg}"), ("aggregation_mean_rel_error", "{relerr}"), ("router_accuracy", "Router"),
    ("citation_precision", "{citp}"), ("citation_recall", "{citr}"), ("overall", "{overall}"),
]  # fmt: skip
WINNER_F1 = 0.9  # PLAN §10: macro-F1 ≥ 0.9 × best macro-F1 in the track (per scale)
# winner gate per task: (quality key, optional (validity key, minimum))
GATE = {"d.extract": ("macro_f1", ("json_validity", 0.995)), "e.rag": ("overall", None)}
DETAIL_NOTE_C = {
    "de": "Latenz: client-seitig je Anfrage (Konkurrenz 1), vom Wettbewerber gemeldet; QPS c16 = 1 000 "
    "Anfragen "
    "aus 16 Client-Threads / Wall-Zeit; mJ/Anfrage = Energie der Stufe (brutto, GPU + CPU) / 1 000. nDCG@10 "
    "(synthetisch, abgestufte Relevanz) bzw. hit@10 (real, Known-Item) = Qualität der Treffer; die exakte "
    "Suche ist die Obergrenze des Dense-Teils. Kompositum = Anfragen aus einem Wort (spec/track_c.md).",
    "en": "Latency: client-side per query (concurrency 1), reported by the contestant; QPS c16 = 1,000 "
    "queries "
    "from 16 client threads / wall time; mJ/query = stage energy (gross, GPU + CPU) / 1,000. nDCG@10 "
    "(synthetic, graded relevance) / hit@10 (real, known item) = quality of the hits; exact search is the "
    "ceiling of the dense part. Kompositum = one-word queries (spec/track_c.md).",
}
DETAIL_NOTE_D = {
    "de": "Zeilen/s, Tok/s aus Stufe `extract` (N Anfragen gleichzeitig); J/Zeile = Energie der Stufe "
    "`extract` (brutto, GPU + CPU) / Zeilen; Latenz je Zeile unter Last; TTFT = Zeit bis zum ersten Token, "
    "20 Zeilen einzeln gestreamt; VRAM = Server nach dem Laden. Qualität: Feld-Mittel und macro-F1 "
    "(spec/track_d.md §4). Gewinner-Gate: macro-F1 ≥ 0,9 × bestes macro-F1 der Skala und JSON-Gültigkeit "
    "≥ 99,5 % (PLAN §10).",
    "en": "Rows/s, tok/s from stage `extract` (N requests in flight); J/row = energy of stage `extract` "
    "(gross, GPU + CPU) / rows; latency per row under load; TTFT = time to first token, 20 rows streamed one "
    "at a time; VRAM = server after loading. Quality: field means and macro-F1 (spec/track_d.md §4). Winner "
    "gate: macro-F1 ≥ 0.9 × best macro-F1 of the scale and JSON validity ≥ 99.5 % (PLAN §10).",
}
DETAIL_NOTE_E = {
    "de": "Stufen in s; Time-to-RAG und Energy-to-RAG = ingest + embed + label + index (brutto, GPU + CPU); "
    "J/Frage = Energie der Stufe `answer` / 300; Latenz je Frage = Router + Ausführung + ggf. Antwortaufruf "
    "(Fragen laufen gebündelt, N gleichzeitig). Klassifikator = Übereinstimmung mit den LLM-Etiketten auf 10 % "
    "Rückhalt. lookup/Aggregation = Anteil richtig, semantic = Mengen-F1, rel. Fehler = mittlerer relativer Fehler "
    "der Aggregationen; Gesamt = Mittel der drei Typen. Gewinner-Gate: Gesamt ≥ 0,9 × bestes Gesamt. "
    "Produktions-Stack `e06_stack_go` (Phase 9, ADR 0025): Der Worker erledigt Parsen, Einbetten, Etikettieren "
    "und Indexieren in einem Job, daher nur `ingest`; der Stack-Start (Stufe `load`, Modelle laden) zählt "
    "zur Wall-Zeit, nicht zu Time-to-RAG. Kein Gewinnerkandidat (er ist der Gewinner als Dienst).",
    "en": "Stages in s; time-to-RAG and energy-to-RAG = ingest + embed + label + index (gross, GPU + CPU); "
    "J/question = energy of stage `answer` / 300; latency per question = router + execution + answer call where "
    "needed (questions run as a batch, N in flight). Classifier = agreement with the LLM labels on a 10 % "
    "hold-out. lookup/aggregation = share correct, semantic = set F1, rel. error = mean relative error of the "
    "aggregations; overall = mean of the three types. Winner gate: overall ≥ 0.9 × best overall. "
    "Production stack `e06_stack_go` (Phase 9, ADR 0025): its worker parses, embeds, labels and indexes in one "
    "job, hence only `ingest`; the stack start (stage `load`, model loading) counts towards wall time, not "
    "time-to-RAG. Not a winner candidate (it is the winner run as a service).",
}
DETAIL_NOTE = {"c.dense": DETAIL_NOTE_C, "c.lexical": DETAIL_NOTE_C, "c.hybrid": DETAIL_NOTE_C,
               "d.extract": DETAIL_NOTE_D, "e.rag": DETAIL_NOTE_E}  # fmt: skip


def _numbers(d: dict[str, list[float]], blob: dict[str, Any]) -> None:
    for k, v in blob.items():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            d.setdefault(k, []).append(float(v))


def accumulate(d: dict[str, list[float]], st: Any, extra: Any, q: Any, rows: Any) -> None:
    """One run's stage times, per-unit stage energy, throughput, latency, TTFT, VRAM and quality into `d`."""
    run_t: dict[str, float] = {}
    run_e: dict[str, float] = {}
    for stg in cast("list[dict[str, Any]]", json.loads(st) or []):
        name = str(stg["name"])
        if stg.get("depth", 0) != 0:
            continue
        d.setdefault(f"t_{name}", []).append(float(stg["t_s"]))
        parts = [float(x) for k in ("gpu_j", "cpu_j", "dram_j") if (x := stg.get(k)) is not None]
        run_t[name] = float(stg["t_s"])
        if parts:
            run_e[name] = sum(parts)
        if name in STAGE_ENERGY and parts:  # energy per query / per row from the stage's energy
            key, unit, factor = STAGE_ENERGY[name]
            n = {"queries": N_QUERIES, "questions": N_QUESTIONS_E}.get(unit, int(rows or 0))
            if n:
                d.setdefault(key, []).append(factor * sum(parts) / n)
    # Track E (only track with `answer`): raw CSV → queryable index = the RAG stages a pipeline has
    # (production stacks: all of them inside `ingest`)
    if "answer" in run_t and (have := [x for x in RAG_STAGES if x in run_t]):
        d.setdefault("time_to_rag_s", []).append(sum(run_t[x] for x in have))
        if all(x in run_e for x in have):
            d.setdefault("rag_wh", []).append(sum(run_e[x] for x in have) / 3600)
    ex = cast("dict[str, Any]", json.loads(extra) if extra else {})
    for k in ("throughput", "latency_ms"):
        _numbers(d, cast("dict[str, Any]", ex.get(k) or {}))
    if (
        isinstance(t := ex.get("ttft_ms"), dict)
        and (m := cast("dict[str, Any]", t).get("median")) is not None
    ):
        d.setdefault("ttft_ms", []).append(float(m))
    if (v := ex.get("server_vram_mb_loaded")) is not None:
        d.setdefault("vram_mb", []).append(float(v))
    if (v := ex.get("label_classifier_holdout_acc")) is not None:  # Track E: distilled damage-type classifier
        d.setdefault("label_classifier_holdout_acc", []).append(float(v))
    _numbers(d, cast("dict[str, Any]", json.loads(q) if q else {}))


def _digits(key: str) -> int:
    if "_at_10" in key or key == "fusion_agreement" or key in TRACK_D_FIELDS:
        return 4
    if key in TRACK_E_FIELDS:
        return 3
    if key in ("time_to_rag_s", "j_question"):
        return 0 if key == "time_to_rag_s" else 1
    if key.startswith("qps") or key in ("vram_mb", "output_tok_per_s", "ttft_ms", "completion_tokens_median"):
        return 0
    return 2 if key.startswith(("p", "mj", "rows_per", "j_")) else 1


TRACK_E_FIELDS = (
    "lookup_score",
    "filtered_semantic_score",
    "aggregation_score",
    "aggregation_mean_rel_error",
    "router_accuracy",
    "citation_precision",
    "citation_recall",
    "overall",
    "label_classifier_holdout_acc",
)
TRACK_D_FIELDS = ("komponente", "schadensart", "ursache", "massnahme", "sicherheitsrelevant",
                  "stillstand_minuten", "ersatzteil_materialnr", "macro_f1", "json_validity")  # fmt: skip


def passes_gate(
    d: dict[str, list[float]], gate: tuple[str, tuple[str, float] | None], best: float | None
) -> bool:
    """Winner gate: quality ≥ WINNER_F1 × best of the scale (and the validity minimum where there is one)."""
    q = median_of(d.get(gate[0], []))
    if q is None or best is None or q < WINNER_F1 * best:
        return False
    if gate[1] is None:
        return True
    v = median_of(d.get(gate[1][0], []))
    return v is not None and v >= gate[1][1]


def _details(con: duckdb.DuckDBPyConnection, track: str, lang: str) -> list[str]:
    """Per-stage medians, query latency percentiles, throughput and retrieval quality (Track C)."""
    de = lang == "de"
    acc: dict[tuple[str, str], dict[str, list[float]]] = {}
    for c, scale, st, ex, q, rows in con.execute(DETAIL_SQL, list(section_filter(track))).fetchall():
        accumulate(acc.setdefault((str(c), str(scale)), {}), st, ex, q, rows)
    if not acc:
        return []
    task = section_filter(track)[1]
    extra = DETAIL_COLUMNS.get(task, [])
    stages = STAGES_BY_TRACK.get(track[0], DETAIL_STAGES)
    words = {"q": "mJ/Anfrage" if de else "mJ/query", "rows": "Zeilen" if de else "rows",
             "row": "Zeile" if de else "row", "j": "J/Zeile" if de else "J/row",
             "jq": "J/Frage" if de else "J/question", "cls": "Klassifikator" if de else "classifier",
             "agg": "Aggregation" if de else "aggregation", "relerr": "rel. Fehler" if de else "rel. error",
             "citp": "Zitat-P" if de else "citation P", "citr": "Zitat-R" if de else "citation R",
             "overall": "Gesamt" if de else "overall"}  # fmt: skip
    keys = [*(f"t_{x}" for x in stages), "p50", "p95", "p99", *(k for k, _ in extra)]
    metrics = [*(f"{x} s" for x in stages), "p50 ms", "p95 ms", "p99 ms",
               *(h.format(**words) for _, h in extra)]  # fmt: skip
    gate = GATE.get(task)
    if gate:
        metrics.append("Gewinner-Gate" if de else "winner gate")
    best: dict[str, float] = {}
    for (_, scale), d in acc.items():
        if gate and (f1 := median_of(d.get(gate[0], []))) is not None:
            best[scale] = max(best.get(scale, 0.0), f1)
    title = "Stufen, Latenz, Qualität (Mediane)" if de else "Stages, latency, quality (medians)"
    header = ["Wettbewerber" if de else "Contestant", "Skala" if de else "Scale", *metrics]
    body: list[list[str]] = []
    for (c, scale), d in sorted(acc.items(), key=lambda kv: (SCALE_ORDER.get(kv[0][1], 9), kv[0][0])):
        cells = [
            ("–" if (m := median_of(d.get(k, []))) is None else fmt_num(m, _digits(k), lang)) for k in keys
        ]
        if gate:
            cells.append("✓" if passes_gate(d, gate, best.get(scale)) else "✗")
        body.append([f"`{c}`", scale, *cells])
    return [f"### {title}", "", DETAIL_NOTE.get(task, DETAIL_NOTE_C)[lang], "", _table(body, header), ""]


def _intro(prices: Prices, lang: str) -> list[str]:
    if lang == "de":
        return [
            "# ZeilenJoule: Bericht",
            "",
            "> Generiert von `task report` aus `results/zj.duckdb`; keine Zahl ist von Hand eingetragen.",
            f"> Strompreise {prices.household} €/kWh (Haushalt) und {prices.industry} €/kWh (Industrie), "
            f"{prices.co2_g_per_kwh} g CO₂/kWh (`config/bench.yaml`, Quellen in PLAN §3.4).",
            "",
            "Methodik: `docs/METHODIK.md`. Energie = GPU + CPU-Paket im Messfenster (systemweit); GPU aus "
            "integrierten NVML-Leistungswerten (ADR 0006). Netto = Brutto minus Leerlaufleistung mal Zeit; "
            "Wettbewerber ohne GPU-Nutzung bekommen keinen GPU-Nettoanteil (ADR 0014). "
            "ZJ-Score = √((T/T_best)·(E/E_best)), 1,00 = bestes in beidem.",
            "",
        ]
    return [
        "# ZeilenJoule: report",
        "",
        "> Generated by `task report` from `results/zj.duckdb`; no number is typed by hand.",
        f"> Electricity {prices.household} €/kWh (household) and {prices.industry} €/kWh (industry), "
        f"{prices.co2_g_per_kwh} g CO₂/kWh (`config/bench.yaml`, sources in PLAN §3.4).",
        "",
        "Method: `docs/METHODIK.md`. Energy = GPU + CPU package in the run window (system-wide); GPU from "
        "integrated NVML power samples (ADR 0006). Net = gross minus idle power times time; "
        "contestants that do not use the GPU get no GPU net share (ADR 0014). "
        "ZJ score = √((T/T_best)·(E/E_best)), 1.00 = best at both.",
        "",
    ]


def render(repo: Path, lang: str) -> str:
    bench = cast(
        "dict[str, Any]", yaml.safe_load((repo / "config" / "bench.yaml").read_text(encoding="utf-8"))
    )
    e = bench["energy"]
    prices = Prices(
        e["price_eur_per_kwh"]["household"], e["price_eur_per_kwh"]["industry"], e["co2_g_per_kwh"]
    )
    con = duckdb.connect(str(repo / "results" / "zj.duckdb"), read_only=True)
    has_ok = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = 'ok_runs'"
    ).fetchone()
    if has_ok and has_ok[0]:
        present = {f"{t[0]}|{t[1]}" for t in con.execute("SELECT DISTINCT track, task FROM runs").fetchall()}

        def has(section: str) -> bool:
            track, task = section_filter(section)
            return any(p.startswith(f"{track}|") and (task == "%" or p == f"{track}|{task}") for p in present)

        body = "\n".join(track_section(con, t, prices, lang) for t in TRACKS if t == "A" or has(t))
        if "E|e.load" in present:
            from zj_analysis import load_curve  # noqa: PLC0415  (load_curve imports this module)

            body += "\n" + load_curve.section_md(con, lang)
        from zj_analysis import figures  # noqa: PLC0415  (figures imports this module)

        body += "\n" + figures.section_md(con, repo / "reports" / "figures", lang)
    else:
        body = f"## {TRACKS['A'][lang][0]}\n\n" + ("_Keine Daten._\n" if lang == "de" else "_No data._\n")
    con.close()
    return "\n".join(_intro(prices, lang)) + body


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m zj_analysis.report")
    p.add_argument("--repo", type=Path, default=Path.cwd())
    a = p.parse_args(argv)
    counts = build(a.repo)
    out = a.repo / "reports"
    out.mkdir(exist_ok=True)
    (out / "REPORT.de.md").write_text(render(a.repo, "de"), encoding="utf-8")
    (out / "REPORT.md").write_text(render(a.repo, "en"), encoding="utf-8")
    from zj_analysis import readme  # noqa: PLC0415  (readme imports this module)

    con = duckdb.connect(str(a.repo / "results" / "zj.duckdb"), read_only=True)
    done = readme.update(a.repo, con) if readme.has_runs(con) else []
    if readme.has_runs(con):
        from zj_analysis import winner  # noqa: PLC0415

        done += winner.write(a.repo, con)
        from zj_analysis import facts  # noqa: PLC0415

        done += facts.update(a.repo, con)
    con.close()
    from zj_analysis import skills  # noqa: PLC0415

    done += skills.update(a.repo)
    print(
        f"reports/REPORT.de.md, reports/REPORT.md{''.join(', ' + d for d in done)} from {counts['runs']} runs"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
