"""Track D level 0/1 field extraction (no GPU, no model)."""

from __future__ import annotations

from zj_ner import fields as fx
from zj_reference import track_a as ta


def test_catalogue_match_needs_every_key_word() -> None:
    cat = fx.Catalogue.build()
    assert cat.match("schadensart", "Waermetauscherleckage") == "Leckage"  # folded, compound-aware
    assert cat.match("ursache", "fehlende Schmierung") == "fehlende Schmierung"
    assert cat.match("ursache", "Unwuchtig") == "Unwucht"
    assert cat.match("massnahme", "Lager gewechselt") is None  # catalogue name is „Lager ersetzt“
    assert cat.match("massnahme", "Filter gereinigt") == "Filter gereinigt"  # longest of two matches


def test_level0_and_level1() -> None:
    lex, cat = ta.load_lexicon(), fx.Catalogue.build()
    raw = {
        "meldung_id": "1",
        "werk": "Essen",
        "anlage": "A",
        "equipment_nr": "P-4711",
        "meldungsdatum": "",
        "schicht": "",
        "prioritaet": "",
        "meldungstext": "Leckage an der Kreiselpumpe. Mat.-Nr. 10836919 verbaut.",
        "stillstand": "1,5 h",
        "kosten": "",
        "status": "",
    }
    r = fx.level0(raw, lex)
    assert r.values["komponente"] == "Kreiselpumpe"
    assert r.values["stillstand_minuten"] == 90
    assert r.values["ersatzteil_materialnr"] == "10836919"
    assert "schadensart" not in r.resolved
    fx.level1(
        r, [{"label": "Ursache", "text": "Leckage", "score": 0.9}], cat, 0.3
    )  # label ignored for damage
    assert r.values["schadensart"] == "Leckage"
    assert r.values["ursache"] is None  # „Leckage“ is no cause → stays open
    assert "ursache" not in r.resolved
    assert r.as_json()["sicherheitsrelevant"] is False
