import dataclasses
import datetime as dt
from pathlib import Path

import pytest

from zj_datagen import lexicon, render

LEX = Path(__file__).resolve().parents[2] / "spec" / "lexicon"


@pytest.mark.parametrize(
    ("cents", "style", "visible"),
    [
        (123456, 0, "1.234,56 €"),
        (123456, 1, "1234,56 EUR"),
        (123456, 2, "1 234,56 €"),
        (123456, 3, "1 234,56 €"),
        (85000, 4, "850 €"),
        (85050, 4, "850,50 €"),  # whole-euro style cannot show cents → falls back
        (85000, 5, "850,00"),
        (-1250, 0, "-12,50 €"),
        (123456789, 0, "1.234.567,89 €"),
    ],
)
def test_kosten(cents: int, style: int, visible: str) -> None:
    assert render.kosten(cents, style) == (visible, cents / 100)


@pytest.mark.parametrize(
    ("minutes", "style", "visible"),
    [
        (90, 0, "1,5 h"),
        (15, 0, "0,25 h"),
        (120, 0, "2 h"),
        (90, 1, "90 min"),
        (120, 2, "ca. 2 Std."),
        (120, 3, "1-2h"),
        (60, 3, "ca. 1 Std."),  # a range needs ≥ 2 h
        (60, 4, "1 Stunde"),
        (180, 4, "3 Stunden"),
        (90, 2, "1,5 h"),  # 'ca. X Std.' only for whole hours
        (7, 0, "7 min"),
        (45, 5, "45 Minuten"),
    ],
)
def test_stillstand(minutes: int, style: int, visible: str) -> None:
    assert render.stillstand(minutes, style) == (visible, minutes)


def test_datum_styles() -> None:
    d = dt.date(2026, 3, 2)
    got = [render.datum(d, s)[0] for s in range(render.DATUM_STYLES)]
    assert got == ["02.03.2026", "2.3.2026", "02.03.26", "2.3.26", "2026-03-02"]
    assert render.datum(d, 0)[1] == "2026-03-02"


def test_invalid_dates_are_invalid() -> None:
    for s in render.UNGUELTIGE_DATEN:
        parts = s.replace("-", ".").split(".")
        nums = [int(p) for p in parts]
        y, mth, day = (nums[0], nums[1], nums[2]) if len(parts[0]) == 4 else (nums[2], nums[1], nums[0])
        with pytest.raises(ValueError, match=r"day|month"):
            dt.date(2000 + y if y < 100 else y, mth, day)


def test_articles() -> None:
    assert render.an_dativ("Druckluftleitung", "f") == "an der Druckluftleitung"
    assert render.an_dativ("Getriebe", "n") == "am Getriebe"
    assert render.nominativ("Rührer", "m") == "der Rührer"


def test_lexicon_loads_and_validates() -> None:
    lex = lexicon.load(LEX)
    assert len(lex.werke) == 28
    assert {w.nuts2 for w in lex.werke} == {"DEA1", "DEA2", "DEA3", "DEA4", "DEA5"}
    assert lex.schadensart("Leckage").kompositum == "leckage"
    assert len(lex.sha256) == 64
    for k in lex.komponenten:
        for form, _ in k.formen:
            form.encode("cp1252")  # every surface form must survive the cp1252 export


def test_lexicon_validation_catches_errors() -> None:
    lex = lexicon.load(LEX)
    bad = lexicon.Komponente("X", (("X", "q"),), None, ("Explosion",), "Z")
    with pytest.raises(lexicon.LexiconError, match="unbekannte Schadensart"):
        lexicon.validate(dataclasses.replace(lex, komponenten=(bad,)))
