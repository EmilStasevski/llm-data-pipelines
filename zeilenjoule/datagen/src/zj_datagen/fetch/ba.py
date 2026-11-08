"""BA Jobsuche (Bundesagentur für Arbeit): Werkstudent ads in NRW, search results only.

The API is not officially documented by the BA (community docs: github.com/bundesAPI/jobsuche-api), so
this is best-effort with a small volume. Only job/company fields are kept; ad details (which can
contain contact persons) are never requested; company names without a legal form (possibly a
private person's name) are set to null; coordinates and employer hashes are dropped."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pyarrow as pa

from zj_datagen.fetch.http import PoliteClient
from zj_datagen.writer import write_table

SEARCH_URL = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v6/jobs"
API_KEY = {"X-API-Key": "jobboerse-jobsuche"}  # public client id used by the BA web app (not a secret)
LEGAL_FORM = re.compile(
    r"\b(GmbH|gGmbH|AG|SE|KG|KGaA|OHG|GbR|UG|e\.\s?V\.|eG|mbH|Ltd\.?|Inc\.?|B\.V\.|S\.A\.|AöR|Stiftung|"
    r"Universität|Hochschule|Stadt|Kreis|Klinikum|Institut)\b"
)
PAGE_SIZE = 100


def firma(name: str | None) -> str | None:
    return name if name and LEGAL_FORM.search(name) else None


def normalize(j: dict[str, Any]) -> dict[str, Any]:
    locs: list[dict[str, Any]] = j.get("stellenlokationen") or [{}]
    loc: dict[str, Any] = locs[0].get("adresse", {})
    eintritt: dict[str, Any] = j.get("eintrittszeitraum") or {}
    return {
        "referenznummer": j.get("referenznummer"),
        "titel": j.get("stellenangebotsTitel"),
        "hauptberuf": j.get("hauptberuf"),
        "alle_berufe": ",".join(j.get("alleBerufe", [])),
        "firma": firma(j.get("firma")),
        "ort": loc.get("ort"),
        "region": loc.get("region"),
        "angebotsart": j.get("stellenangebotsart"),
        "eintritt": eintritt.get("von"),
        "erstveroeffentlichung": j.get("datumErsteVeroeffentlichung"),
        "verguetung": j.get("verguetungsangabe"),
        "vertragsdauer": j.get("vertragsdauer"),
    }


def fetch(
    out: Path, client: PoliteClient, cities: list[str], umkreis_km: int, max_ads: int
) -> dict[str, Any]:
    seen: dict[str, dict[str, Any]] = {}
    requests = 0
    for city in cities:
        page = 1
        while len(seen) < max_ads:
            params = {
                "was": "Werkstudent",
                "wo": city,
                "umkreis": umkreis_km,
                "size": PAGE_SIZE,
                "page": page,
            }
            res = client.get_json(SEARCH_URL, params=params, headers=API_KEY)
            requests += 1
            jobs: list[dict[str, Any]] = res.get("ergebnisliste") or []
            for j in jobs:
                ref: str | None = j.get("referenznummer")
                if ref and ref not in seen:
                    row = normalize(j)
                    if row["region"] == "NORDRHEIN_WESTFALEN":  # radius can cross state borders
                        seen[ref] = row
            if len(jobs) < PAGE_SIZE or page * PAGE_SIZE >= int(res.get("maxErgebnisse", 0)):
                break
            page += 1
    rows = sorted(seen.values(), key=lambda r: str(r["referenznummer"]))[:max_ads]
    out.mkdir(parents=True, exist_ok=True)
    write_table(pa.Table.from_pylist(rows), out / "ba_werkstudent_nrw.parquet")
    return {"source": SEARCH_URL, "query": "was=Werkstudent", "cities": len(cities), "umkreis_km": umkreis_km,
            "pages": requests, "rows": len(rows), "file": "ba_werkstudent_nrw.parquet"}  # fmt: skip
