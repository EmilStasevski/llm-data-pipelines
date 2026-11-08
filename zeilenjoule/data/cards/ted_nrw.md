# Datensatzkarte / Dataset card: `ted_nrw` (real)

> Generiert von `task data:cards`.

- **Quelle / source:** TED, Supplement zum Amtsblatt der EU: https://api.ted.europa.eu/v3/notices/search
- **Bedingungen / terms:** Bekanntmachungen frei wiederverwendbar; redaktionelle Inhalte unter **CC BY 4.0** (https://ted.europa.eu/en/legal-notice). Änderung: E-Mail-Adressen und Telefonnummern im Text → „[entfernt]“.
- **Datenschutz / privacy (Regel 8):** Kontaktfelder werden nie abgefragt; Kontaktangaben im Freitext werden entfernt.
- **Abruf / fetching:** ≤ 1 Anfrage/s, User-Agent `zeilenjoule-research/0.1`, Rohantworten in `data/raw/`.
- **Stand / status:** 13514 Zeilen / rows (36 Anfragen, 20 aus Cache)

```json
{
  "source": "https://api.ted.europa.eu/v3/notices/search",
  "query": "place-of-performance IN (DEA1 DEA2 DEA3 DEA4 DEA5) AND notice-type=cn-standard AND publication-date>=20260101 AND publication-date<=20261031",
  "total_available": 13514,
  "rows": 13514,
  "redactions": 478,
  "file": "ted_nrw.parquet",
  "network_requests": 36,
  "cache_hits": 20
}
```
