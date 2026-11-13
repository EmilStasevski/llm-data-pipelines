"""Skills analysis of the BA Werkstudent ads in NRW (PLAN §6.2, §13) → generated block in docs/BEWERBUNG.md.

The BA list endpoint returns titles and occupations, no ad text, so the analysis classifies `titel` +
`alle_berufe` with keyword groups. Counts only; no company names or other fields leave the dataset.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import duckdb

BEGIN, END = "<!-- zj:skills:begin -->", "<!-- zj:skills:end -->"
# (group, pattern); an ad can fall into several groups
GROUPS: list[tuple[str, str]] = [
    ("Daten & Analytics (Data, BI, Reporting, Statistik)",
     r"\bdata|datenanaly|datenbank|datenmanagement|daten- und|business.?analy|business intelligence|\bbi\b|"
     r"statisti|reporting|power bi|controlling.?analy"),
    ("Softwareentwicklung (Entwicklung, Informatik, Cloud, DevOps)",
     r"software|entwickl|developer|programmier|informatik|full.?stack|backend|frontend|devops|cloud|web"),
    ("IT-Betrieb & Support (Systeme, Netzwerk, Administration)",
     r"\bit\b|\bit-|systemintegration|systemadministra|netzwerk|it.?admin|helpdesk|client management|"
     r"cyber|it.?security|informationssicherheit"),
    ("KI / Machine Learning", r"\bki\b|künstliche intelligenz|machine learning|\bml\b|\bai\b|\bllm"),
    ("Ingenieurwesen & Technik (Maschinenbau, Elektro, Produktion)",
     r"ingenieur|maschinenbau|elektro|technik|mechatron|produktion|konstruk|verfahrens|fertigung|qualität"),
    ("Energie, Umwelt, Nachhaltigkeit", r"energie|nachhaltig|umwelt|esg|klima|sustainab|emission"),
    ("Controlling, Finanzen, Einkauf", r"controll|finanz|buchhalt|accounting|einkauf|procurement"),
    ("Marketing, Vertrieb, Kommunikation", r"marketing|vertrieb|sales|social media|kommunikation|content"),
    ("Personal / HR", r"personal|\bhr\b|recruit|human resources"),
    ("Aushilfe Gastronomie / Handel / Logistik",
     r"gastro|servicekraft|kellner|verkäuf|kassier|barista|theke|barkeeper|lager|kommissionier|minijob"),
]  # fmt: skip
TECH = (0, 1, 2, 3)  # groups the project speaks to directly


COMPANY_WORDS = ("ntt data",)  # company names that some titles carry and that would match a group


def classify(text: str) -> list[int]:
    t = text.lower()
    for w in COMPANY_WORDS:
        t = t.replace(w, "")
    return [i for i, (_, pat) in enumerate(GROUPS) if re.search(pat, t)]


def block(repo: Path) -> str | None:
    pq = repo / "data" / "real" / "ba" / "ba_werkstudent_nrw.parquet"
    if not pq.exists():
        return None
    manifest = json.loads((pq.parent / "manifest.json").read_text(encoding="utf-8"))
    rows = (
        duckdb.connect()
        .execute(
            f"SELECT coalesce(titel, '') || ' | ' || coalesce(alle_berufe, '') FROM read_parquet('{pq}')"
        )
        .fetchall()
    )
    n = len(rows)
    counts = [0] * len(GROUPS)
    tech = none = 0
    for (text,) in rows:
        groups = classify(str(text))
        for g in groups:
            counts[g] += 1
        tech += any(g in TECH for g in groups)
        none += not groups

    def pct(k: int) -> str:
        return f"{100 * k / n:.1f} %".replace(".", ",")

    out = [
        BEGIN,
        "",
        f"Datensatz: {n} Werkstudenten-Anzeigen der BA-Jobsuche in NRW ({manifest['cities']} Städte, "
        f"Umkreis {manifest['umkreis_km']} km), abgerufen mit ≤ 1 Anfrage/s; ausgewertet werden nur Titel "
        "und Berufsbezeichnungen (der Listen-Endpunkt liefert keinen Anzeigentext). Eine Anzeige kann in "
        "mehrere Gruppen fallen. Generiert von `task report` (`analysis/…/skills.py`).",
        "",
        "| Gruppe (Schlüsselwörter in Titel/Beruf) | Anzeigen | Anteil |",
        "|---|---|---|",
    ]
    for i in sorted(range(len(GROUPS)), key=lambda i: -counts[i]):
        out.append(f"| {GROUPS[i][0]} | {counts[i]} | {pct(counts[i])} |")
    out += [
        "",
        f"- Mindestens eine der Gruppen Daten, Software, IT-Betrieb oder KI: **{tech}** Anzeigen "
        f"({pct(tech)}).",
        f"- Keiner Gruppe zugeordnet: {none} ({pct(none)}).",
        "",
        END,
    ]
    return "\n".join(out)


def update(repo: Path) -> list[str]:
    path = repo / "docs" / "BEWERBUNG.md"
    if not path.exists() or (new := block(repo)) is None:
        return []
    text = path.read_text(encoding="utf-8")
    if BEGIN in text and END in text:
        text = text[: text.index(BEGIN)] + new + text[text.index(END) + len(END) :]
        path.write_text(text, encoding="utf-8")
        return ["docs/BEWERBUNG.md"]
    return []
