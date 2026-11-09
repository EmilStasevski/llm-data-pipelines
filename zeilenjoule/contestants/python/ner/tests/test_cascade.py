"""Cascade: parsing of the reduced (semantic) LLM answer."""

from __future__ import annotations

import json

from zj_ner import d11_cascade as cascade


def test_semantic_schema_and_parse() -> None:
    assert cascade.SEM_FIELDS == ("komponente", "schadensart", "ursache", "massnahme")
    ok = {
        "komponente": "Filter",
        "schadensart": "Verstopfung",
        "ursache": None,
        "massnahme": "Filter gereinigt",
    }
    assert cascade.parse_semantic(json.dumps(ok)) == ok
    assert cascade.parse_semantic(json.dumps({**ok, "schadensart": "Verstopft"})) is None
    assert cascade.parse_semantic(json.dumps({**ok, "stillstand_minuten": 5})) is None
    assert cascade.parse_semantic("nope") is None
