"""Track D reference (spec/track_d.md): schema, German prompt, few-shot examples and scoring for `d.extract`.

The prompt is built once from the lexicons (spec/lexicon/*.yaml) and written to data/track_d/prompts.*.jsonl,
so every engine sends the same bytes. Scoring is strict and dependency-free:
- a row is *valid* if the whole model text (outer whitespace stripped) is one JSON object that satisfies
  spec/schema_stoermeldung.json (types, enum, all keys, no extra keys);
- exact match for schadensart / sicherheitsrelevant / stillstand_minuten / ersatzteil_materialnr,
  token-F1 for komponente / ursache / massnahme (case-folded words); null vs. null = 1, null vs. value = 0;
- an invalid row scores 0 in every field; macro-F1 = mean of the seven per-field means.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from collections.abc import Callable
from functools import cache
from pathlib import Path
from typing import Any, cast

import yaml

REPO = Path(__file__).resolve().parents[3]
LEX = REPO / "spec" / "lexicon"
SCHEMA_PATH = REPO / "spec" / "schema_stoermeldung.json"
FIELDS = ("komponente", "schadensart", "ursache", "massnahme", "sicherheitsrelevant", "stillstand_minuten",
          "ersatzteil_materialnr")  # fmt: skip
TEXT_FIELDS = ("komponente", "ursache", "massnahme")  # token-F1
MAX_TOKENS = 256
_WORD = re.compile(r"[^\W_]+")


def _load(name: str) -> dict[str, Any]:
    return cast("dict[str, Any]", yaml.safe_load((LEX / name).read_text(encoding="utf-8")))


@cache
def catalog() -> dict[str, Any]:
    """Canonical values (gold vocabulary) + surface forms for the prompt."""
    komp = [
        (k["name"], [f for f in k["formen"] if f != k["name"]])
        for k in _load("komponenten.yaml")["komponenten"]
    ]
    return {
        "komponenten": komp,
        "schadensarten": list(_load("schadensarten.yaml")["schadensarten"]),
        "ursachen": [u["name"] for u in _load("ursachen.yaml")["ursachen"]],
        "massnahmen": [m["name"] for m in _load("massnahmen.yaml")["massnahmen"]],
        "sicherheit": list(_load("sicherheit.yaml")["trigger"]),
    }


def schema() -> dict[str, Any]:
    c = catalog()
    nullable_str = {"type": ["string", "null"]}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Störmeldung (Track D d.extract)",
        "type": "object",
        "properties": {
            "komponente": nullable_str,
            "schadensart": {"type": "string", "enum": c["schadensarten"]},
            "ursache": nullable_str,
            "massnahme": nullable_str,
            "sicherheitsrelevant": {"type": "boolean"},
            "stillstand_minuten": {"type": ["integer", "null"]},
            "ersatzteil_materialnr": nullable_str,
        },
        "required": list(FIELDS),
        "additionalProperties": False,
    }


def system_prompt() -> str:
    c = catalog()
    komp = "; ".join(f"{n} ({', '.join(f)})" if f else n for n, f in c["komponenten"])
    return "\n".join([
        "Du bist Assistenz der Instandhaltung in einem Industriebetrieb in NRW. Du liest eine Störmeldung "
        "und extrahierst strukturierte Felder. Antworte ausschließlich mit einem JSON-Objekt mit genau "
        "diesen Schlüsseln, ohne Erklärung und ohne Markdown.",
        "",
        "Felder:",
        "- komponente: betroffene Komponente, nur der Katalogname ohne Klammerzusatz; in Klammern stehen "
        f"Schreibweisen aus Meldungen, die zu diesem Namen gehören. Katalog: {komp}. null, wenn keine "
        "erkennbar.",
        f"- schadensart: genau einer dieser Werte: {', '.join(c['schadensarten'])}.",
        f"- ursache: Ursache als Katalogwert: {', '.join(c['ursachen'])}. null, wenn keine Ursache genannt "
        "oder sie unklar ist (z. B. „Ursache in Klärung“).",
        f"- massnahme: durchgeführte Maßnahme als Katalogwert: {', '.join(c['massnahmen'])}. null, wenn "
        "keine Maßnahme genannt oder sie noch offen ist (z. B. „IH informiert“).",
        "- sicherheitsrelevant: true, wenn die Meldung ein Sicherheitsereignis nennt (z. B. "
        f"{', '.join(c['sicherheit'])}), sonst false.",
        "- stillstand_minuten: Stillstandsdauer in ganzen Minuten aus dem Feld Stillstand oder dem Text "
        "(1,5 h = 90; „ca.“ ignorieren; bei Bereichen wie „1-2h“ die Obergrenze). null, wenn keine Angabe.",
        "- ersatzteil_materialnr: Materialnummer eines verbauten Ersatzteils, nur die Ziffern als Text. "
        "null, wenn keine genannt.",
        "",
        "Die Meldungen können Tippfehler, Abkürzungen (IH, MA, ET, FU, SPS …), englische Wörter, "
        "fehlende Umlaute (ae, oe, ue) oder kaputte Zeichenkodierung (Ã¼ statt ü) enthalten.",
    ])  # fmt: skip


def user_message(row: dict[str, Any]) -> str:
    return "\n".join([
        f"Anlage: {row.get('anlage') or ''}",
        f"Equipment: {row.get('equipment_nr') or ''}",
        f"Meldungstext: {row.get('meldungstext') or ''}",
        f"Stillstand: {row.get('stillstand') or ''}",
    ])  # fmt: skip


def gold_json(g: dict[str, Any]) -> str:
    return json.dumps({k: g[k] for k in FIELDS}, ensure_ascii=False)


def pick_shots(dev: list[dict[str, Any]], gold: dict[str, dict[str, Any]]) -> list[str]:
    """Three dev rows (first match in id order): a full one with spare part, a safety event with nulls, and a
    noisy one (transliteration/mojibake/English)."""
    rows = sorted(dev, key=lambda r: r["meldung_id"])
    tests: list[Callable[[dict[str, Any]], Any]] = [
        lambda g: g["ursache"] and g["massnahme"] and g["ersatzteil_materialnr"] and g["stillstand_minuten"],
        lambda g: g["sicherheitsrelevant"] and g["ursache"] is None,
        lambda g: g["noise"] in {"translit", "mojibake", "englisch"} and g["massnahme"] is None,
    ]
    out: list[str] = []
    for t in tests:
        out.append(
            next(r["meldung_id"] for r in rows if r["meldung_id"] not in out and t(gold[r["meldung_id"]]))
        )
    return out


def messages(row: dict[str, Any], shots: list[tuple[dict[str, Any], dict[str, Any]]]) -> list[dict[str, str]]:
    m = [{"role": "system", "content": system_prompt()}]
    for r, g in shots:
        m += [{"role": "user", "content": user_message(r)}, {"role": "assistant", "content": gold_json(g)}]
    return [*m, {"role": "user", "content": user_message(row)}]


# ---------------------------------------------------------------- scoring
def validate(obj: Any) -> bool:
    if not isinstance(obj, dict) or set(cast("dict[str, Any]", obj)) != set(FIELDS):
        return False
    o = cast("dict[str, Any]", obj)
    for k in ("komponente", "ursache", "massnahme", "ersatzteil_materialnr"):
        if o[k] is not None and not isinstance(o[k], str):
            return False
    if o["schadensart"] not in catalog()["schadensarten"] or not isinstance(o["sicherheitsrelevant"], bool):
        return False
    v = o["stillstand_minuten"]
    return v is None or (isinstance(v, int) and not isinstance(v, bool))


def parse(text: str) -> dict[str, Any] | None:
    try:
        obj = json.loads(text.strip())
    except (json.JSONDecodeError, ValueError):
        return None
    return cast("dict[str, Any]", obj) if validate(obj) else None


def _tokens(s: str) -> list[str]:
    return _WORD.findall(unicodedata.normalize("NFC", s).casefold())


def token_f1(pred: str | None, gold: str | None) -> float:
    if pred is None or gold is None:
        return float(pred is None and gold is None)
    p, g = Counter(_tokens(pred)), Counter(_tokens(gold))
    common = sum((p & g).values())
    if not common:
        return 0.0
    prec, rec = common / sum(p.values()), common / sum(g.values())
    return 2 * prec * rec / (prec + rec)


def _norm_matnr(v: str | None) -> str | None:
    return None if v is None else re.sub(r"\D", "", v) or v.strip()


def score_row(pred: dict[str, Any] | None, gold: dict[str, Any]) -> dict[str, float]:
    if pred is None:
        return dict.fromkeys(FIELDS, 0.0)
    s = {k: token_f1(pred[k], gold[k]) for k in TEXT_FIELDS}
    s["schadensart"] = float(pred["schadensart"] == gold["schadensart"])
    s["sicherheitsrelevant"] = float(pred["sicherheitsrelevant"] == bool(gold["sicherheitsrelevant"]))
    s["stillstand_minuten"] = float(pred["stillstand_minuten"] == gold["stillstand_minuten"])
    s["ersatzteil_materialnr"] = float(_norm_matnr(pred["ersatzteil_materialnr"])
                                       == _norm_matnr(gold["ersatzteil_materialnr"]))  # fmt: skip
    return s


def score(preds: list[dict[str, Any] | None], golds: list[dict[str, Any]]) -> dict[str, float]:
    rows = [score_row(p, g) for p, g in zip(preds, golds, strict=True)]
    out = {k: sum(r[k] for r in rows) / len(rows) for k in FIELDS}
    out["macro_f1"] = sum(out[k] for k in FIELDS) / len(FIELDS)
    out["json_validity"] = sum(p is not None for p in preds) / len(preds)
    return out
