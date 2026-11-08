"""Authoring source of spec/track_a_cases.jsonl (golden vectors for every Track A contestant).

Expected values are written BY HAND from spec/track_a.md, not computed by the reference, so the
reference implementation is tested against them too (reference/tests/test_track_a.py).
Run: uv run python spec/build_track_a_cases.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

NBSP = " "
CASES: list[tuple[str, Any, Any]] = []  # (fn, input, expected)


def add(fn: str, inp: Any, exp: Any) -> None:
    CASES.append((fn, inp, exp))


# ---- norm: mojibake, NFC, whitespace --------------------------------------------------------
for inp, exp in [
    ("Ãœberhitzung am Getriebe", "Überhitzung am Getriebe"),
    ("WÃ¤lzlager", "Wälzlager"),
    ("GerÃ¶ll", "Geröll"),
    ("StrÃ¶mung GrÃ¼n MaÃŸnahme", "Strömung Grün Maßnahme"),
    ("Ã„nderung Ã–l", "Änderung Öl"),
    ("Kosten 1.200 â‚¬", "Kosten 1.200 €"),
    ("â‚¬â‚¬", "€€"),
    ("Pumpe  undicht", "Pumpe undicht"),
    ("  Pumpe undicht  ", "Pumpe undicht"),
    ("Pumpe\tundicht", "Pumpe undicht"),
    ("Pumpe\r\nundicht", "Pumpe undicht"),
    (f"1{NBSP}234", "1 234"),
    (f"a {NBSP} \t b", "a b"),
    ("Überhitzung", "Überhitzung"),  # NFD → NFC
    ("", ""),
    ("   ", ""),
    ("ohne Änderung", "ohne Änderung"),
    ("Ã", "Ã"),  # lone Ã is not in the table
]:
    add("norm", inp, exp)

# ---- equipment ------------------------------------------------------------------------------
for inp, exp in [
    ("P-4711", "P-4711"), ("M 0815-A", "M-0815-A"), ("V0815", "V-0815"), ("S-1234-B", "S-1234-B"),
    (" P-4711 ", "P-4711"), ("p-4711", None), ("P-471", None), ("PP-4711", None), ("P 47111", None), ("", None),
]:  # fmt: skip
    add("equipment", inp, exp)

# ---- dates ----------------------------------------------------------------------------------
for inp, exp in [
    ("12.03.2026", "2026-03-12"), ("2.3.2026", "2026-03-02"), ("02.03.26", "2026-03-02"), ("2.3.26", "2026-03-02"),
    ("2026-03-02", "2026-03-02"), ("29.02.2024", "2024-02-29"), ("29.02.2025", None), ("31.02.2026", None),
    ("30.02.25", None), ("31.04.2025", None), ("00.05.2026", None), ("2026-13-01", None), ("2026-3-2", None),
    ("12.03.026", None), ("12/03/2026", None), ("", None), (" 12.03.2026 ", "2026-03-12"), ("31.12.99", "2099-12-31"),
    ("1.1.00", "2000-01-01"), ("123.03.2026", None),
]:  # fmt: skip
    add("date", inp, exp)

# ---- priorities -----------------------------------------------------------------------------
for inp, exp in [
    ("1", 1),
    ("4", 4),
    ("0", None),
    ("5", None),
    ("", None),
    (" 2 ", 2),
    ("2a", None),
    ("²", None),
]:
    add("prio", inp, exp)

# ---- German numbers (costs) -----------------------------------------------------------------
for inp, exp in [
    ("1.234,56 €", 1234.56), ("1234,56 EUR", 1234.56), ("1 234,56 €", 1234.56), (f"1{NBSP}234,56 €", 1234.56),
    ("1.234 €", 1234.0), ("850,00", 850.0), ("850 EUR", 850.0), ("-12,50 €", -12.5), ("1.234.567,89 €", 1234567.89),
    ("0,99", 0.99), ("12,5", 12.5), ("850€", 850.0), ("k.A.", None), ("", None), ("12.34", None), ("1.2345,00", None),
    ("12,", None), ("€ 12,00", None), ("1,234.56", None), ("1 234 567", 1234567.0), ("--5", None), (" 7,10 € ", 7.1),
]:  # fmt: skip
    add("eur", inp, exp)

# ---- durations ------------------------------------------------------------------------------
for inp, exp in [
    ("1,5 h", 90), ("0,25 h", 15), ("2 h", 120), ("6,75 h", 405), ("90 min", 90), ("ca. 2 Std.", 120),
    ("ca.2 Std.", 120), ("1-2h", 120), ("1 - 2 h", 120), ("3-4h", 240), ("1 Stunde", 60), ("3 Stunden", 180),
    ("45 Minuten", 45), ("7 min", 7), ("2H", 120), ("90 MIN", 90), ("1,51 h", 91), ("0,125 h", 8), ("", None),
    ("ca.", None), ("2 Std", None), ("zwei Stunden", None), ("1,5", None), ("1-2", None), ("120 sek", None),
    ("0,025 h", 2), ("2,5 min", 3), ("0,5 min", 1), ("1,25 min", 1), ("0,0125 h", 1),  # exact half-up, no float drift
]:  # fmt: skip
    add("minutes", inp, exp)

# ---- abbreviation expansion (input already normalised) ---------------------------------------
for inp, exp in [
    ("ca. 2 Stunden", "circa 2 Stunden"),
    ("lt. MA Fehlbedienung", "laut Mitarbeiter Fehlbedienung"),
    ("Prüfung i.O.", "Prüfung in Ordnung"),
    ("Prüfung n.i.O.", "Prüfung nicht in Ordnung"),
    ("Funktionsprüfung n.i.O., Teil gesperrt", "Funktionsprüfung nicht in Ordnung, Teil gesperrt"),
    ("(ET) bestellt", "(Ersatzteil) bestellt"),
    ("ET Mat.-Nr. 10810812 verbaut.", "Ersatzteil Mat.-Nr. 10810812 verbaut."),
    ("KW-Pumpe undicht", "KW-Pumpe undicht"),
    ("GLRD erneuert", "Gleitringdichtung erneuert"),
    ("Störung am FU", "Störung am Frequenzumrichter"),
    ("Reset der SPS durchgeführt", "Reset der speicherprogrammierbare Steuerung durchgeführt"),
    ("SPS-Baugruppe defekt", "SPS-Baugruppe defekt"),
    ("wg. Fremdkörper", "wegen Fremdkörper"),
    ("WG. FREMDKÖRPER", "wegen FREMDKÖRPER"),
    ("vmtl. Materialermüdung", "vermutlich Materialermüdung"),
    ("IH informiert", "Instandhaltung informiert"),
    ("wird an AV übergeben", "wird an Arbeitsvorbereitung übergeben"),
    ("Maßnahme offen", "Maßnahme offen"),
    ("MAßNAHME", "MAßNAHME"),
    ("ca.", "circa"),
    ('"ca."', '"circa"'),
    ("ca.,", "circa,"),
    ("ca..", "ca.."),  # '.' is not stripped: core 'ca..' is no key
    ("z.B. Lager", "zum Beispiel Lager"),
    ("u.a. Lager", "unter anderem Lager"),
    ("Fa. beauftragt", "Firma beauftragt"),
    ("FA 4711", "Fertigungsauftrag 4711"),
    ("Pos. 3", "Position 3"),
    ("Nr. 7", "Nummer 7"),
    ("HD-Leitung", "HD-Leitung"),
    ("HD Leitung", "Hochdruck Leitung"),
    ("QS prüft", "Qualitätssicherung prüft"),
    ("ma", "Mitarbeiter"),
    ("Ma.", "Ma."),
    ("inkl. Dichtung", "inklusive Dichtung"),
    ("bzgl. Auftrag", "bezüglich Auftrag"),
    ("ggf. tauschen", "gegebenenfalls tauschen"),
    ("[Std.]", "[Stunden]"),
    ("10 Min.", "10 Minuten"),
    ("", ""),
]:
    add("expand", inp, exp)

# ---- lower / fold ---------------------------------------------------------------------------
for inp, exp in [
    ("Überhitzung am Getriebe", "ueberhitzung am getriebe"),
    ("MASSNAHME", "massnahme"),
    ("Maßnahme", "massnahme"),
    ("ÄÖÜ äöü ß", "aeoeue aeoeue ss"),
    ("Wärmetauscher", "waermetauscher"),
]:
    add("fold", inp, exp)
for inp, exp in [("Maßnahme", "maßnahme"), ("ÜBER", "über"), ("GmbH", "gmbh")]:
    add("lower", inp, exp)

# ---- material numbers (on text_norm) --------------------------------------------------------
for inp, exp in [
    ("Ersatzteil Mat.-Nr. 10810812 verbaut.", "10810812"),
    ("Material-Nr.: 10234567", "10234567"),
    ("10234567", "10234567"),
    ("Mat.Nr. 10000000 getauscht", "10000000"),
    ("Nummer 102345678", None),  # 9 digits
    ("Nummer 1023456", None),  # 7 digits
    ("Nummer 20234567", None),  # wrong prefix
    ("a10234567b", "10234567"),
    ("x 110234567", None),
    ("erst 10111111 dann 10222222", "10111111"),
    ("ohne Nummer", None),
]:
    add("materialnr", inp, exp)

# ---- safety (on text_fold) ------------------------------------------------------------------
for inp, exp in [
    ("mitarbeiter leicht verletzt", True),
    ("beinahe-unfall, bereich abgesperrt", True),
    ("verbruehung durch heisses medium (beinaheunfall)", True),
    ("kleiner brand, sofort geloescht", True),
    ("rauchentwicklung festgestellt", True),
    ("lager ersetzt", False),
    ("verletzungsgefahr", True),  # substring rule
    ("", False),
]:
    add("safety", inp, exp)

# ---- component (on text_fold) ---------------------------------------------------------------
for inp, exp in [
    ("leckage an der kreiselpumpe", "Kreiselpumpe"),
    ("die pumpe tropft", "Kreiselpumpe"),
    ("leckage an der kuehlwasserpumpe", "Kühlwasserpumpe"),
    ("pumpenleckage in kesselhaus 3", "Kreiselpumpe"),
    ("druckluftleitungsleckage", "Druckluftleitung"),
    ("kw-pumpe undicht", "Kühlwasserpumpe"),
    ("verschleiss am waelzlager", "Wälzlager"),
    ("lager ersetzt", "Wälzlager"),
    ("getriebe laeuft heiss. lager ersetzt.", "Getriebe"),  # longer form wins
    ("lager und getriebe", "Getriebe"),
    ("motorschutzschalter ausgeloest", "Motorschutzschalter"),
    ("motor vibriert", "Elektromotor"),
    (
        "bruch am kran in hochregallager 2",
        "Wälzlager",
    ),  # documented rule limitation: 'lager' (5) > 'kran' (4)
    ("pt100-temperaturfuehler defekt", "PT100-Temperaturfühler"),
    ("temperaturfuehler defekt", "PT100-Temperaturfühler"),
    ("reset der speicherprogrammierbare steuerung durchgefuehrt", "SPS-Baugruppe"),
    ("sps-baugruppe defekt", "SPS-Baugruppe"),
    ("stoerung am frequenzumrichter", "Frequenzumrichter"),
    ("fehlalarm an der lichtschranke", "Lichtschranke"),
    ("ventil undicht, regelventil getauscht", "Regelventil"),
    ("filter gereinigt", "Filter"),
    ("oelfilter zugesetzt", "Filter"),
    ("dichtungsleckage", None),  # stem 'dichtungs' is not a surface form
    ("ohne bauteil", None),
    ("", None),
    ("walze und arbeitswalze", "Walze"),
    ("zylinder klemmt", "Hydraulikzylinder"),
    ("hubzylinder undicht", "Hydraulikzylinder"),
]:
    add("komponente", inp, exp)

# ---- doc_text -------------------------------------------------------------------------------
add("doc_text", {"werk": "Duisburg", "anlage": "Beizlinie 2", "equipment_nr": "P-4711", "datum": "2026-03-12",
                 "prioritaet": 1, "text_norm": "Pumpe undicht."},
    "Werk: Duisburg | Anlage: Beizlinie 2 | Equipment: P-4711 | Datum: 2026-03-12 | Priorität: 1 | Meldung: Pumpe undicht.")  # fmt: skip
add("doc_text", {"werk": "Essen", "anlage": "", "equipment_nr": None, "datum": None, "prioritaet": None,
                 "text_norm": "Kurzschluss."}, "Werk: Essen | Meldung: Kurzschluss.")  # fmt: skip
add("doc_text", {"werk": "Hamm", "anlage": "Kühlturm 1", "equipment_nr": None, "datum": "2025-01-31",
                 "prioritaet": 3, "text_norm": ""}, "Werk: Hamm | Anlage: Kühlturm 1 | Datum: 2025-01-31 | Priorität: 3")  # fmt: skip

# ---- whole rows (raw CSV fields → selected output fields) ----------------------------------
add("row", {"meldung_id": "2000000007", "werk": "Duisburg", "anlage": "Beizlinie 2", "equipment_nr": "M 0815-A",
            "meldungsdatum": "2.3.26", "schicht": "Früh", "prioritaet": "2",
            "meldungstext": "Ãœberhitzung  am Getriebe. Lt. MA Fehlbedienung. ET Mat.-Nr. 10810812 verbaut.",
            "stillstand": "ca. 2 Std.", "kosten": f"1{NBSP}234,56 €", "status": "abgeschlossen"},
    {"equipment_nr": "M-0815-A", "datum": "2026-03-02", "prioritaet": 2, "stillstand_min": 120, "kosten_eur": 1234.56,
     "text_norm": "Überhitzung am Getriebe. laut Mitarbeiter Fehlbedienung. Ersatzteil Mat.-Nr. 10810812 verbaut.",
     "text_fold": "ueberhitzung am getriebe. laut mitarbeiter fehlbedienung. ersatzteil mat.-nr. 10810812 verbaut.",
     "materialnr": "10810812", "sicherheitsrelevant_rule": False, "komponente_rule": "Getriebe",
     "regierungsbezirk": "Düsseldorf", "nuts2": "DEA1",
     "doc_text": "Werk: Duisburg | Anlage: Beizlinie 2 | Equipment: M-0815-A | Datum: 2026-03-02 | Priorität: 2 | "
                 "Meldung: Überhitzung am Getriebe. laut Mitarbeiter Fehlbedienung. Ersatzteil Mat.-Nr. 10810812 verbaut."})  # fmt: skip
add("row", {"meldung_id": "2000000008", "werk": "Unbekannt", "anlage": "X 1", "equipment_nr": "kaputt",
            "meldungsdatum": "31.02.2026", "schicht": "Nacht", "prioritaet": "9",
            "meldungstext": "MA LEICHT VERLETZT", "stillstand": "", "kosten": "k.A.", "status": "offen"},
    {"equipment_nr": None, "datum": None, "prioritaet": None, "stillstand_min": None, "kosten_eur": None,
     "text_norm": "Mitarbeiter LEICHT VERLETZT", "text_fold": "mitarbeiter leicht verletzt", "materialnr": None,
     "sicherheitsrelevant_rule": True, "komponente_rule": None, "regierungsbezirk": None, "nuts2": None,
     "doc_text": "Werk: Unbekannt | Anlage: X 1 | Meldung: Mitarbeiter LEICHT VERLETZT"})  # fmt: skip


def main() -> None:
    out = Path(__file__).with_name("track_a_cases.jsonl")
    with out.open("w", encoding="utf-8") as f:
        for i, (fn, inp, exp) in enumerate(CASES, 1):
            f.write(
                json.dumps({"id": f"a{i:03d}", "fn": fn, "input": inp, "expected": exp}, ensure_ascii=False)
                + "\n"
            )
    print(f"{out.name}: {len(CASES)} cases")


if __name__ == "__main__":
    main()
