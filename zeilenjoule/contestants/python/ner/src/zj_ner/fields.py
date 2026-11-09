"""Non-LLM field extraction for Track D (spec/track_d.md §7): level 0 rules + level 1 GLiNER spans.

Level 0 = the Track A rules (spec/track_a.md, zj_reference.track_a): komponente (dictionary, longest match),
sicherheitsrelevant (trigger terms), stillstand_minuten (duration column), ersatzteil_materialnr (regex).
Level 1 = GLiNER spans for schadensart / ursache / massnahme, mapped to the catalogue the prompt also gets
(spec/lexicon, catalogue *names* only, no phrase lists): a span maps to a catalogue value when it contains
every word (≥ 4 letters, folded, substring = compound-aware) of exactly one value's name; several matches →
the longest name. A field is *resolved* when a span with score ≥ threshold maps; otherwise it stays open.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from zj_reference import track_a as ta
from zj_reference import track_d as ref

LABELS = {"schadensart": "Schaden", "ursache": "Ursache", "massnahme": "Maßnahme", "komponente": "Bauteil"}
SPAN_FIELDS = ("schadensart", "ursache", "massnahme")
_WORD = re.compile(r"[^\W\d_]{4,}")


def _fold(s: str) -> str:
    return ta.fold(ta.norm(s).lower())


@dataclass(frozen=True)
class Catalogue:
    names: dict[str, list[tuple[str, list[str]]]]  # field → [(value, folded key words)]

    @staticmethod
    def build() -> Catalogue:
        c = ref.catalog()
        lists = {"schadensart": c["schadensarten"], "ursache": c["ursachen"], "massnahme": c["massnahmen"]}
        return Catalogue({f: [(v, _WORD.findall(_fold(v))) for v in vals] for f, vals in lists.items()})

    def match(self, fld: str, span: str) -> str | None:
        s = _fold(span)
        hits = [(v, ws) for v, ws in self.names[fld] if ws and all(w in s for w in ws)]
        if not hits:
            return None
        return max(hits, key=lambda h: (sum(len(w) for w in h[1]), h[0]))[0]


@dataclass
class Row:
    values: dict[str, Any] = field(default_factory=dict[str, Any])
    resolved: set[str] = field(default_factory=set[str])

    def complete(self) -> bool:
        return set(ref.FIELDS) <= self.resolved

    def as_json(self) -> dict[str, Any]:
        return {k: self.values.get(k) for k in ref.FIELDS} | {
            "sicherheitsrelevant": bool(self.values.get("sicherheitsrelevant"))
        }


def level0(raw: dict[str, Any], lex: dict[str, Any]) -> Row:
    o = ta.process_row({k: ("" if v is None else str(v)) for k, v in raw.items()}, lex)
    r = Row()
    r.values = {
        "komponente": o["komponente_rule"],
        "sicherheitsrelevant": o["sicherheitsrelevant_rule"],
        "stillstand_minuten": o["stillstand_min"],
        "ersatzteil_materialnr": o["materialnr"],
    }
    r.resolved = {"sicherheitsrelevant", "ersatzteil_materialnr"}
    if o["komponente_rule"] is not None:
        r.resolved.add("komponente")
    if o["stillstand_min"] is not None or not ta.norm(str(raw.get("stillstand") or "")):
        r.resolved.add("stillstand_minuten")  # parsed, or the column is empty (→ null)
    return r


def level1(r: Row, ents: list[dict[str, Any]], cat: Catalogue, threshold: float) -> None:
    """Map GLiNER entities onto the open span fields (schadensart takes spans of any label: the model often
    labels a damage word as cause)."""
    by_label = {lab: f for f, lab in LABELS.items()}
    for fld in SPAN_FIELDS:
        best: tuple[float, str] | None = None
        for e in ents:
            if e["score"] < threshold or (fld != "schadensart" and by_label.get(e["label"]) != fld):
                continue
            if (v := cat.match(fld, e["text"])) is not None and (best is None or e["score"] > best[0]):
                best = (float(e["score"]), v)
        if best is not None:
            r.values[fld] = best[1]
            r.resolved.add(fld)
        else:
            r.values.setdefault(fld, ref.catalog()["schadensarten"][-1] if fld == "schadensart" else None)


def text_of(raw: dict[str, Any]) -> str:
    return ta.norm(str(raw.get("meldungstext") or ""))
