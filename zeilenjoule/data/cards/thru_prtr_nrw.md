# Datensatzkarte / Dataset card: `thru_prtr_nrw` (real)

> Generiert von `task data:cards`.

- **Quelle / source:** thru.de (Umweltbundesamt, PRTR): https://thru.de/downloads/ (prtr_2024.zip, SQLite)
- **Bedingungen / terms:** Nutzung erlaubt mit Quellenangabe **thru.de**; Inhalte dürfen nicht verändert oder verfälscht werden (https://thru.de/nutzungsbedingungen/). Wir nutzen nur eine Teilmenge (NRW, Haupttätigkeit, letztes Berichtsjahr je Betrieb) mit unveränderten Werten; **kein Rauschen**.
- **Datenschutz / privacy (Regel 8):** Betriebsname, Straße und Hausnummer werden verworfen (landwirtschaftliche Betriebe können von Privatpersonen geführt werden).
- **Abruf / fetching:** ≤ 1 Anfrage/s, User-Agent `zeilenjoule-research/0.1`, Rohantworten in `data/raw/`.
- **Stand / status:** 3020 Zeilen / rows (0 Anfragen, 0 aus Cache)

```json
{
  "source": "https://thru.de/wp-content/uploads/2026/03/prtr_2024.zip",
  "rows": 3020,
  "file": "thru_betriebe_nrw.parquet",
  "network_requests": 0,
  "cache_hits": 0
}
```
