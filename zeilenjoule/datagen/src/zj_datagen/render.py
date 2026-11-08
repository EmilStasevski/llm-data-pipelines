"""Visible formats of a German SAP-like export, rendered from gold values.

Each function returns the visible string together with the gold value it encodes, so the two
cannot drift apart. The formats are exactly what Track A (PLAN §7.2) must parse back.
"""

from __future__ import annotations

import datetime as dt

NBSP = " "  # PLAN says "thin space"; U+2009 is not encodable in cp1252, NBSP is (dataset card)

ARTIKEL_NOM = {"m": "der", "f": "die", "n": "das"}
AN_DAT = {"m": "am", "f": "an der", "n": "am"}


def nominativ(form: str, genus: str) -> str:
    return f"{ARTIKEL_NOM[genus]} {form}"


def an_dativ(form: str, genus: str) -> str:
    return f"{AN_DAT[genus]} {form}"


def _de_decimal(x: float, decimals: int) -> str:
    return f"{x:.{decimals}f}".replace(".", ",")


def _group(intpart: int, sep: str) -> str:
    s = f"{intpart:,}"  # 1,234,567
    return s.replace(",", sep)


KOSTEN_STYLES = 6


def kosten(eur_cents: int, style: int) -> tuple[str, float]:
    """eur_cents → (visible, gold €). Styles: 0 '1.234,56 €' 1 '1234,56 EUR' 2 '1 234,56 €'
    3 '1<NBSP>234,56 €' 4 '1.234 €' (whole euros only) 5 '850,00' (no currency)."""
    gold = eur_cents / 100
    neg, cents = eur_cents < 0, abs(eur_cents)
    whole, frac = divmod(cents, 100)
    sign = "-" if neg else ""
    if style == 4 and frac != 0:
        style = 0
    match style:
        case 0:
            v = f"{sign}{_group(whole, '.')},{frac:02d} €"
        case 1:
            v = f"{sign}{whole},{frac:02d} EUR"
        case 2:
            v = f"{sign}{_group(whole, ' ')},{frac:02d} €"
        case 3:
            v = f"{sign}{_group(whole, NBSP)},{frac:02d} €"
        case 4:
            v = f"{sign}{_group(whole, '.')} €"
        case _:
            v = f"{sign}{_group(whole, '.')},{frac:02d}"
    return v, gold


STILLSTAND_STYLES = 6


def stillstand(minutes: int, style: int) -> tuple[str, int]:
    """minutes → (visible, gold minutes). Styles: 0 '1,5 h' 1 '90 min' 2 'ca. 2 Std.' 3 '1-2h' (gold =
    upper bound) 4 '2 Stunden' 5 '90 Minuten'. Styles that cannot express the value fall back to minutes."""
    hours = minutes / 60
    whole_h = minutes % 60 == 0
    quarter_h = minutes % 15 == 0
    if style in (2, 3, 4) and not whole_h:
        style = 0 if quarter_h else 1
    if style == 0 and not quarter_h:
        style = 1
    if style == 3 and minutes < 120:
        style = 2
    match style:
        case 0:
            h = _de_decimal(hours, 2).rstrip("0").rstrip(",")
            return f"{h} h", minutes
        case 1:
            return f"{minutes} min", minutes
        case 2:
            return f"ca. {minutes // 60} Std.", minutes
        case 3:
            b = minutes // 60
            return f"{b - 1}-{b}h", minutes  # range → upper bound (PLAN §7.2)
        case 4:
            n = minutes // 60
            return (f"{n} Stunde" if n == 1 else f"{n} Stunden"), minutes
        case _:
            return f"{minutes} Minuten", minutes


DATUM_STYLES = 5


def datum(d: dt.date, style: int) -> tuple[str, str]:
    """date → (visible, ISO gold). 0 DD.MM.YYYY 1 D.M.YYYY 2 DD.MM.YY 3 D.M.YY 4 YYYY-MM-DD."""
    match style:
        case 0:
            v = f"{d.day:02d}.{d.month:02d}.{d.year}"
        case 1:
            v = f"{d.day}.{d.month}.{d.year}"
        case 2:
            v = f"{d.day:02d}.{d.month:02d}.{d.year % 100:02d}"
        case 3:
            v = f"{d.day}.{d.month}.{d.year % 100:02d}"
        case _:
            v = d.isoformat()
    return v, d.isoformat()


# impossible calendar dates (gold null): rendered as-is in the export
UNGUELTIGE_DATEN = ("31.02.2026", "30.02.25", "2026-13-01", "31.04.2025", "00.05.2026")
