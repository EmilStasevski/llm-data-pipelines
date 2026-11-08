# Datensatzkarte / Dataset card: `real_nrw` (realer Korpus für Track C/E / real corpus)

> Generiert von `task data:cards`. Erzeugt mit `task data:real-corpus` (ADR 0012).

- **Herkunft / derived from:** `thru_prtr_nrw` (nur letztes Berichtsjahr, identische Texte dedupliziert), `ted_nrw`, `ba_werkstudent_nrw`; Bedingungen und Datenschutz siehe deren Karten.
- **Schema:** `doc_id` (`thru:<sha1-12>` | `ted:<publication_number>` | `ba:<referenznummer>`), `quelle`, `ort`, `datum` (TED: Veröffentlichung, BA: Erstveröffentlichung, thru: null), `titel`, `doc_text`.
- **Anfragen / queries:** `spec/queries_c.real.jsonl`, 4 × 250 Known-Item-Anfragen (eindeutiger Titel eines TED-/BA-Dokuments), Filterklassen none / ort / datum (±15 Tage) / kombiniert.
- **Keine Gold-Labels / no gold labels:** Qualität nur als Recall@10 gegen exakte (gefilterte) Brute-Force und Known-Item-Trefferquote; nDCG/Antwortqualität nur auf synthetischen Daten.
- **Stand / status:** 15305 Dokumente / documents

```json
{
  "rows": 15305,
  "by_source": {
    "thru": 1005,
    "ted": 13514,
    "ba": 786
  },
  "queries": 1000,
  "median_filter_selectivity": {
    "none": 1.0,
    "ort": 0.0133,
    "datum": 0.0957,
    "kombiniert": 0.0008
  },
  "seed": 20261106,
  "sources": [
    "data/real/thru",
    "data/real/ted",
    "data/real/ba"
  ]
}
```
