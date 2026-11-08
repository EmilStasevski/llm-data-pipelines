# Datensatzkarte / Dataset card: `ba_werkstudent_nrw` (real)

> Generiert von `task data:cards`.

- **Quelle / source:** Bundesagentur für Arbeit, Jobsuche (inoffiziell dokumentiert): https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v6/jobs
- **Bedingungen / terms:** Keine offizielle Dokumentation/Lizenz der BA (Community-Doku: https://github.com/bundesAPI/jobsuche-api); best effort, kleines Volumen, nur Suchergebnisse.
- **Datenschutz / privacy (Regel 8):** Nur Stellen-/Firmenfelder; Detailseiten (Kontaktpersonen) werden nie abgerufen; Firmennamen ohne Rechtsform (möglicher Personenname) → null; Koordinaten und Arbeitgeber-Hash verworfen.
- **Abruf / fetching:** ≤ 1 Anfrage/s, User-Agent `zeilenjoule-research/0.1`, Rohantworten in `data/raw/`.
- **Stand / status:** 786 Zeilen / rows (53 Anfragen, 0 aus Cache)

```json
{
  "source": "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v6/jobs",
  "query": "was=Werkstudent",
  "cities": 28,
  "umkreis_km": 25,
  "pages": 53,
  "rows": 786,
  "file": "ba_werkstudent_nrw.parquet",
  "network_requests": 53,
  "cache_hits": 0
}
```
