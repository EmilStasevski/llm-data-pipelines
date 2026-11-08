# Datensatzkarte / Dataset card: Evaluationssets / evaluation sets

> Generiert von `task data:cards`.

| Skala | Track-C-Anfragen | Filterklassen | Track-E-Fragen | Typen |
|---|---|---|---|---|
| M | 1000 | none 250, werk 250, datum 250, kombiniert 250 | 300 | lookup 100, filtered_semantic 100, aggregation 100 |
| L | 1000 | none 250, werk 250, datum 250, kombiniert 250 | 300 | lookup 100, filtered_semantic 100, aggregation 100 |

- **Track C** (`spec/qrels_c.<Skala>.jsonl`): Relevanz ist eine Regel über Gold-Labels (`zj_eval.relevance.grade`: 2 = Komponente + Schadensart, 1 = eins davon bzw. gleiche Equipment-Gruppe); die Datei speichert Definition und Trefferzahlen statt Millionen (Anfrage, Zeile)-Paaren.
- **Track E** (`spec/questions_e.<Skala>.jsonl`): Lookups nur mit eindeutigem natürlichem Schlüssel; gefilterte semantische Fragen mit 3–150 Gold-IDs; Aggregationen mit Gold-SQL.
- **D2k** (`data/synth/d2k/`): 2 000 + 300 Dev-Zeilen aus Skala M, disjunkt. Vorhanden.
