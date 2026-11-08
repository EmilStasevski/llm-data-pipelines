import datetime as dt

from zj_eval.relevance import Filter, grade

EQ = {"Kreiselpumpe": "P", "Kühlwasserpumpe": "P", "Getriebe": "M"}


def row(
    k: str, s: str, werk: str = "Duisburg", rb: str = "Düsseldorf", d: dt.date | None = dt.date(2025, 5, 1)
) -> dict[str, object]:
    return {"komponente": k, "schadensart": s, "werk": werk, "regierungsbezirk": rb, "datum_iso": d}


def test_grades() -> None:
    f = Filter()
    assert grade(row("Kreiselpumpe", "Leckage"), "Kreiselpumpe", "Leckage", f, EQ) == 2
    assert grade(row("Kreiselpumpe", "Verschleiß"), "Kreiselpumpe", "Leckage", f, EQ) == 1
    assert grade(row("Kühlwasserpumpe", "Leckage"), "Kreiselpumpe", "Leckage", f, EQ) == 1  # same group
    assert grade(row("Getriebe", "Leckage"), "Kreiselpumpe", "Leckage", f, EQ) == 0  # other group
    assert grade(row("Getriebe", "Bruch"), "Kreiselpumpe", "Leckage", f, EQ) == 0


def test_filters() -> None:
    q2 = Filter(date_from=dt.date(2025, 4, 1), date_to=dt.date(2025, 6, 30))
    r = row("Kreiselpumpe", "Leckage")
    assert grade(r, "Kreiselpumpe", "Leckage", q2, EQ) == 2
    assert grade(r, "Kreiselpumpe", "Leckage", Filter(werk="Essen"), EQ) == 0
    assert grade(row("Kreiselpumpe", "Leckage", d=None), "Kreiselpumpe", "Leckage", q2, EQ) == 0
    combined = Filter(
        regierungsbezirk="Düsseldorf", date_from=dt.date(2025, 1, 1), date_to=dt.date(2025, 12, 31)
    )
    assert combined.matches(r)
    assert Filter.from_json(combined.to_json()) == combined
