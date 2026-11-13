"""Track E (spec/track_e.md): inputs for the pipelines, scoring and the `e.rag` verifier.

    python -m zj_eval.track_e prepare [--scale L]     → data/track_e/<scale>/{questions.jsonl, prompts.json}
    python -m zj_eval.track_e verify --output … --reference spec/questions_e.L.jsonl

prompts.json holds everything the pipelines send to an LLM (router system prompt + schema, answer prompt +
schema, label prompt + schema), so every pipeline sends the same bytes. Scoring is programmatic (§4).
"""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from pathlib import Path
from typing import Any, cast

import yaml

from zj_reference import track_d as td

REPO = Path(__file__).resolve().parents[3]
SCALE = "L"
K_SEARCH = 100
REL_TOL = 0.05
ROUTES = ("sql", "search")
VIEW_COLUMNS = (
    ("meldung_id", "TEXT", "Meldungsnummer, z. B. '2000123456'"),
    ("werk", "TEXT", "Werk (Stadt), siehe Liste"),
    ("regierungsbezirk", "TEXT", "Düsseldorf, Köln, Münster, Detmold oder Arnsberg"),
    ("anlage", "TEXT", "Anlage im Werk, z. B. 'Walzstraße 2'"),
    (
        "equipment_nr",
        "TEXT",
        "Equipment, immer Buchstabe-Bindestrich-4 Ziffern[-Buchstabe], z. B. 'P-4711', 'M-0815-A'",
    ),
    ("datum", "DATE", "Meldungsdatum"),
    ("prioritaet", "INTEGER", "1 (hoch) bis 4"),
    ("stillstand_min", "INTEGER", "Stillstand in Minuten, NULL = keine Angabe"),
    ("kosten_eur", "DOUBLE", "Kosten in Euro, NULL = keine Angabe"),
    ("komponente", "TEXT", "Komponente (Katalogname), NULL = nicht erkannt"),
    ("schadensart", "TEXT", "Schadensart (Katalogwert, automatisch geschätzt)"),
    ("sicherheitsrelevant", "BOOLEAN", "Sicherheitsereignis ja/nein"),
    ("meldungstext", "TEXT", "Freitext der Meldung"),
)


def _werke(repo: Path) -> list[str]:
    data = cast("dict[str, Any]", yaml.safe_load((repo / "spec/lexicon/werke_nrw.yaml").read_text("utf-8")))
    return sorted(str(w["werk"]) for w in data["werke"])


EXAMPLES: list[tuple[str, dict[str, Any]]] = [
    (
        "Was war die Maßnahme bei der Störung an P 1234-B in Hamm am 3.2.2025?",
        {
            "route": "sql",
            "antwort_aus": "text",
            "suchtext": None,
            "werk": None,
            "regierungsbezirk": None,
            "datum_von": None,
            "datum_bis": None,
            "komponente": None,
            "schadensart": None,
            "sql": "SELECT meldung_id, meldungstext FROM meldungen WHERE equipment_nr = 'P-1234-B' AND werk = 'Hamm' "
            "AND datum = '2025-02-03'",
        },
    ),
    (
        "Wie viele Störungen mit Schadensart Korrosion gab es je Regierungsbezirk im 1. Halbjahr 2025?",
        {
            "route": "sql",
            "antwort_aus": "tabelle",
            "suchtext": None,
            "werk": None,
            "regierungsbezirk": None,
            "datum_von": None,
            "datum_bis": None,
            "komponente": None,
            "schadensart": None,
            "sql": "SELECT regierungsbezirk, count(*) AS n FROM meldungen WHERE schadensart = 'Korrosion' "
            "AND datum BETWEEN '2025-01-01' AND '2025-06-30' GROUP BY regierungsbezirk ORDER BY regierungsbezirk",
        },
    ),
    (
        "Welche Meldungen gibt es zu „Getriebe läuft heiß“ im Werk Dortmund im 2. Halbjahr 2025?",
        {
            "route": "search",
            "sql": None,
            "antwort_aus": None,
            "suchtext": "Getriebe läuft heiß",
            "werk": "Dortmund",
            "regierungsbezirk": None,
            "datum_von": "2025-07-01",
            "datum_bis": "2025-12-31",
            "komponente": "Getriebe",
            "schadensart": "Überhitzung",
        },
    ),
]


def router_schema() -> dict[str, Any]:
    s: dict[str, Any] = {"type": ["string", "null"]}
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "route": {"type": "string", "enum": list(ROUTES)},
            "sql": s,
            "antwort_aus": {"type": ["string", "null"], "enum": ["tabelle", "text", None]},
            "suchtext": s,
            "werk": s,
            "regierungsbezirk": s,
            "datum_von": s,
            "datum_bis": s,
            "komponente": s,
            "schadensart": s,
        },
        "required": [
            "route",
            "sql",
            "antwort_aus",
            "suchtext",
            "werk",
            "regierungsbezirk",
            "datum_von",
            "datum_bis",
            "komponente",
            "schadensart",
        ],
    }


def router_prompt(repo: Path, dialect: str = "duckdb") -> str:
    """dialect "duckdb" = the benchmark prompt (sqlglot transpiles); "postgres" = the production prompt (Phase 9,
    ADR 0022: the Go guard checks but does not transpile)."""
    c = td.catalog()
    pg = dialect == "postgres"
    cols = "\n".join(f"  {n} {'NUMERIC' if pg and t == 'DOUBLE' else t} – {d}" for n, t, d in VIEW_COLUMNS)
    return "\n".join(
        [
            "Du beantwortest Fragen zu Störmeldungen aus Industriewerken in NRW. Wähle für jede Frage den Weg und "
            "antworte nur mit einem JSON-Objekt nach Schema.",
            "",
            "Wege:",
            '- "sql": Zählen, Summen, Rangfolgen und das Nachschlagen einer bestimmten Meldung (Equipment, Werk, '
            f"Datum). Schreibe genau eine SELECT-Anweisung ({'PostgreSQL' if pg else 'DuckDB'}-SQL) nur auf die Sicht "
            "meldungen. "
            '"antwort_aus": "tabelle", wenn das Ergebnis die Antwort ist; "text", wenn die Antwort im '
            "Meldungstext steht (dann meldung_id und meldungstext auswählen).",
            '- "search": inhaltliche Suche nach Meldungen zu einem Thema. "suchtext" = das Thema; Filter nur aus '
            "der Frage: werk, regierungsbezirk, datum_von/datum_bis (JJJJ-MM-TT, Quartal/Halbjahr/Jahr als "
            "Zeitraum), komponente und schadensart als Katalogwert, wenn das Thema sie eindeutig nennt.",
            "",
            "Sicht meldungen:",
            cols,
            "",
            "Regeln für SQL: Datumswerte als Text 'JJJJ-MM-TT' vergleichen (datum = '2025-02-03', BETWEEN … AND …), "
            "keine Datumsfunktionen; Texte exakt wie in den Listen schreiben; Equipment normalisieren "
            "(„P 1234-B“ → 'P-1234-B'); Summen mit coalesce(sum(x), 0); Geldbeträge mit round(…, 2).",
            f"Werke: {', '.join(_werke(repo))}.",
            f"Schadensarten: {', '.join(c['schadensarten'])}.",
            f"Komponenten: {', '.join(n for n, _ in c['komponenten'])}.",
        ]
    )


def answer_prompt() -> str:
    c = td.catalog()
    return "\n".join(
        [
            "Du beantwortest eine Frage zu Störmeldungen nur aus den gegebenen Meldungen. Antworte nur mit JSON: "
            '"antwort" = kurze Antwort oder null, wenn die Meldungen sie nicht enthalten; "zitiert" = die '
            "meldung_id der Meldungen, auf die sich die Antwort stützt.",
            f"Ursachen als Katalogwert: {', '.join(c['ursachen'])}.",
            f"Maßnahmen als Katalogwert: {', '.join(c['massnahmen'])}.",
        ]
    )


def answer_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["antwort", "zitiert"],
        "properties": {
            "antwort": {"type": ["string", "null"]},
            "zitiert": {"type": "array", "items": {"type": "string"}},
        },
    }


def label_prompt() -> str:
    c = td.catalog()
    return (
        "Du liest eine Störmeldung und bestimmst die Schadensart. Antworte nur mit JSON "
        f'{{"schadensart": …}}; genau einer dieser Werte: {", ".join(c["schadensarten"])}.'
    )


def label_schema() -> dict[str, Any]:
    enum = td.catalog()["schadensarten"]
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["schadensart"],
        "properties": {"schadensart": {"type": "string", "enum": enum}},
    }


def prepare(scale: str = SCALE, repo: Path = REPO) -> dict[str, Any]:
    qs = [
        json.loads(x)
        for x in (repo / "spec" / f"questions_e.{scale}.jsonl").read_text("utf-8").splitlines()
        if x
    ]
    out = repo / "data" / "track_e" / scale
    out.mkdir(parents=True, exist_ok=True)
    (out / "questions.jsonl").write_text(
        "".join(json.dumps({"qid": q["qid"], "text": q["text"]}, ensure_ascii=False) + "\n" for q in qs),
        "utf-8",
    )
    prompts = prompt_set(repo)
    (out / "prompts.json").write_text(json.dumps(prompts, ensure_ascii=False, indent=1) + "\n", "utf-8")
    return {"questions": len(qs), "router_prompt_chars": len(router_prompt(repo))}


def prompt_set(repo: Path, dialect: str = "duckdb") -> dict[str, Any]:
    shots: list[dict[str, str]] = []
    for text, ans in EXAMPLES:
        shots += [
            {"role": "user", "content": text},
            {"role": "assistant", "content": json.dumps(ans, ensure_ascii=False)},
        ]
    return {
        "router": {"system": router_prompt(repo, dialect), "shots": shots, "schema": router_schema()},
        "answer": {"system": answer_prompt(), "schema": answer_schema(), "max_rows": 5},
        "label": {"system": label_prompt(), "schema": label_schema(), "sample": 2000, "seed": 0},
        "search": {"k": K_SEARCH, "rrf_k": 60},
        "catalog": {
            "komponente": [n for n, _ in td.catalog()["komponenten"]],
            "schadensart": list(td.catalog()["schadensarten"]),
            "werk": _werke(repo),
            "regierungsbezirk": ["Düsseldorf", "Köln", "Münster", "Detmold", "Arnsberg"],
        },
        "max_tokens": 384,
    }


# ---------------------------------------------------------------- scoring
def _key(v: Any) -> str:
    return unicodedata.normalize("NFC", str(v)).strip().casefold()


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", "."))
    except ValueError:
        return None


def _close(a: Any, b: Any) -> tuple[bool, float]:
    x, y = _num(a), _num(b)
    if x is None or y is None:
        return False, 1.0
    err = abs(x - y) / max(abs(y), 1e-9) if y else float(x != 0)
    return err <= REL_TOL, err


def _scalar(ans: object) -> object:
    out: object = ans
    while isinstance(out, list):
        items = cast("list[object]", out)
        if len(items) != 1:
            break
        out = items[0]
    return cast("object", out)


def _cell(ans: object) -> object:
    """First cell of a one-row answer (a router SQL result is a table)."""
    a = _scalar(ans)
    if isinstance(a, list):
        items = cast("list[object]", a)
        return items[0] if items else None
    return a


def score_value(ans: Any, gold: dict[str, Any]) -> tuple[float, float]:
    """(correct 0/1, relative error) of one answer: table (keys + values within REL_TOL), number/integer
    (within REL_TOL) or text (exact after NFC + case folding)."""
    g = gold["answer"]
    kind = gold.get("answer_type")
    if kind == "table":
        rows = cast("list[object]", ans) if isinstance(ans, list) else []
        pairs = [cast("list[Any]", r) for r in rows if isinstance(r, list)]
        got: dict[str, Any] = {_key(r[0]): r[-1] for r in pairs if len(r) >= 2}
        want = {_key(r[0]): r[-1] for r in g}
        if set(got) != set(want):
            return 0.0, 1.0
        errs = [_close(got[k], want[k]) for k in want]
        return float(all(ok for ok, _ in errs)), max(e for _, e in errs) if errs else 0.0
    a = _cell(ans)
    if kind == "text":
        hit = a is not None and _key(a) == _key(g)
        return float(hit), 0.0 if hit else 1.0
    ok, err = _close(a, g)
    return float(ok), min(err, 1.0)


score_aggregation = score_value  # backwards name


def set_f1(got: list[str], gold: list[str]) -> tuple[float, float, float]:
    a, b = set(got), set(gold)
    if not a or not b:
        return 0.0, 0.0, 0.0
    tp = len(a & b)
    p, r = tp / len(a), tp / len(b)
    return (2 * p * r / (p + r) if tp else 0.0), p, r


def score(out: list[dict[str, Any]], gold: list[dict[str, Any]]) -> dict[str, float]:
    per: dict[str, list[float]] = {t: [] for t in ("lookup", "filtered_semantic", "aggregation")}
    route_ok: list[float] = []
    rel_err: list[float] = []
    cite_p: list[float] = []
    cite_r: list[float] = []
    for o, g in zip(out, gold, strict=True):
        t = g["type"]
        route_ok.append(float(o.get("route") == ("search" if t == "filtered_semantic" else "sql")))
        cited = [str(x) for x in cast("list[Any]", o.get("cited_ids") or [])]
        if t == "lookup":
            per[t].append(score_value(o.get("answer"), g)[0])
        elif t == "filtered_semantic":
            per[t].append(set_f1(cited, g["gold_ids"])[0])
        else:
            ok, err = score_aggregation(o.get("answer"), g)
            per[t].append(ok)
            rel_err.append(err)
        if t != "aggregation":
            _, p, r = set_f1(cited, g["gold_ids"])
            cite_p.append(p)
            cite_r.append(r)
    q = {f"{t}_score": sum(v) / len(v) for t, v in per.items() if v}
    q["overall"] = sum(q.values()) / len(q)
    q["router_accuracy"] = sum(route_ok) / len(route_ok)
    q["aggregation_mean_rel_error"] = sum(rel_err) / len(rel_err) if rel_err else 0.0
    q["citation_precision"] = sum(cite_p) / len(cite_p) if cite_p else 0.0
    q["citation_recall"] = sum(cite_r) / len(cite_r) if cite_r else 0.0
    return q


def _jsonl(p: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in p.read_text("utf-8").splitlines() if x.strip()]


def check(output: Path, reference: Path) -> dict[str, Any]:
    try:
        out = _jsonl(output)
    except Exception as e:
        return {"passed": False, "detail": f"cannot read output: {e}"}
    gold = _jsonl(reference)
    if [o.get("qid") for o in out] != [g["qid"] for g in gold]:
        return {
            "passed": False,
            "detail": f"{len(out)} answers / qids or order differ from {len(gold)} questions",
        }
    bad_route = sum(o.get("route") not in ROUTES for o in out)
    ingest = output.with_name("ingest.json")
    info = cast("dict[str, Any]", json.loads(ingest.read_text("utf-8"))) if ingest.exists() else {}
    unsafe = int(info.get("sql_unsafe_executed", -1))
    problems: list[str] = []
    if bad_route:
        problems.append(f"{bad_route} answers without a valid route")
    if unsafe != 0:
        problems.append(
            "ingest.json missing or SQL outside the rules executed"
            if unsafe < 0
            else f"{unsafe} unsafe SQL executed"
        )
    q = {k: round(v, 4) for k, v in score(out, gold).items()}
    flat = {
        **info,
        **cast("dict[str, Any]", info.get("label") or {}),
    }  # label stats are nested in ingest.json
    for k in ("sql_executed", "sql_rejected", "sql_failed", "label_classifier_holdout_acc"):
        if k in flat:
            q[k] = flat[k]
    passed = not problems
    detail = (
        f"{len(out)} answers, overall {q['overall']:.3f}, router {q['router_accuracy']:.3f}"
        if passed
        else "; ".join(problems)
    )
    return {"passed": passed, "detail": detail, "quality": q}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m zj_eval.track_e")
    p.add_argument("cmd", choices=["prepare", "verify"])
    p.add_argument("--scale", default=SCALE)
    p.add_argument("--output", type=Path)
    p.add_argument("--reference", type=Path)
    a = p.parse_args(argv)
    if a.cmd == "prepare":
        print(json.dumps(prepare(a.scale), ensure_ascii=False))
        return 0
    res = check(a.output, a.reference)
    print(json.dumps(res, ensure_ascii=False))
    return 0 if res["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
