"""Graded relevance for Track C retrieval queries (spec/qrels_c.*.jsonl), computed from gold labels.

Queries store their *definition* (target component + damage type + metadata filter) instead of
millions of explicit (query, row) pairs; grade() is the single source of truth for both the
generator (counts in the qrels file) and the evaluator (nDCG@10).

Grades:
  2  komponente and schadensart both match the target
  1  komponente matches but the damage type differs, or the damage type matches and the component
     belongs to the same equipment group (eq prefix: P pumps, M drives, V valves …)
  0  otherwise; rows outside the metadata filter are always 0
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Filter:
    werk: str | None = None
    regierungsbezirk: str | None = None
    date_from: dt.date | None = None  # inclusive
    date_to: dt.date | None = None  # inclusive

    def matches(self, row: Mapping[str, Any]) -> bool:
        if self.werk is not None and row["werk"] != self.werk:
            return False
        if self.regierungsbezirk is not None and row["regierungsbezirk"] != self.regierungsbezirk:
            return False
        if self.date_from is not None or self.date_to is not None:
            d = row["datum_iso"]
            if d is None:  # unparseable date never satisfies a date filter
                return False
            if self.date_from is not None and d < self.date_from:
                return False
            if self.date_to is not None and d > self.date_to:
                return False
        return True

    def to_json(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for k in ("werk", "regierungsbezirk"):
            if (v := getattr(self, k)) is not None:
                out[k] = v
        for k in ("date_from", "date_to"):
            if (v := getattr(self, k)) is not None:
                out[k] = v.isoformat()
        return out

    @classmethod
    def from_json(cls, d: Mapping[str, str]) -> Filter:
        return cls(
            werk=d.get("werk"),
            regierungsbezirk=d.get("regierungsbezirk"),
            date_from=dt.date.fromisoformat(d["date_from"]) if "date_from" in d else None,
            date_to=dt.date.fromisoformat(d["date_to"]) if "date_to" in d else None,
        )


def grade(
    row: Mapping[str, Any], komponente: str, schadensart: str, flt: Filter, eq_group: Mapping[str, str]
) -> int:
    """Relevance grade of one gold row; eq_group maps component name → equipment group prefix."""
    if not flt.matches(row):
        return 0
    k_ok = row["komponente"] == komponente
    s_ok = row["schadensart"] == schadensart
    if k_ok and s_ok:
        return 2
    if k_ok or (s_ok and eq_group.get(row["komponente"]) == eq_group.get(komponente)):
        return 1
    return 0
