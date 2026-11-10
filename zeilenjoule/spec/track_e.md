# Track E: Spezifikation / specification (`e.rag`)

> End-to-End-RAG über Störmeldungen: Roh-CSV → abfragbarer Index → 300 Fragen mit Router (SQL ↔ Vektor).
> Bewertung: `eval/src/zj_eval/track_e.py`, Eingaben: `task trackE:prep`. Entscheidungen: ADR 0020.

## 1. Eingabe / Ausgabe
- **Skala L** (1 000 000 Zeilen, PLAN §11). Eingabe: `data/synth/stoermeldungen/L/stoermeldungen.cp1252.csv`
  (dieselbe Datei wie Track A). Fragen: `data/track_e/L/questions.jsonl` = `{"qid", "text"}` aus
  `spec/questions_e.L.jsonl` **ohne** Gold-Felder; Router- und Antwort-Prompts:
  `data/track_e/L/prompts.json` (für alle Pipelines gleich).
- **Ausgabe:** `out.jsonl`, je Frage in Eingabereihenfolge:
  `{"qid", "route": "sql"|"search", "sql", "answer", "cited_ids", "latency_ms", "llm_tokens"}`.
  `answer` = Text (Nachschlagen), Tabelle `[[…], …]` (Aggregation) oder null (Suche: die Antwort sind
  `cited_ids`). Neben `out.jsonl`: `ingest.json` (Zeilen je Stufe, Klassifikator-Kennzahlen).

## 2. Stufen (ZJMARK, PLAN §2)
| Stufe | Inhalt |
|---|---|
| `ingest` | S1–S3: CSV → Track-A-Spalten + `doc_text` (ein Track-A-Kandidat, Ausgabe = Track-A-Referenz) |
| `embed` | S4: bge-m3 dense für alle `doc_text` (ein Track-B-Kandidat) |
| `label` | `schadensart` für alle Zeilen: LLM etikettiert 2 000 zufällige Zeilen (Seed 0, Schema nur `schadensart`), ein linearer Klassifikator (Softmax, L2 1e-4) auf den bge-m3-Vektoren etikettiert den Rest |
| `index` | S5: Vektorindex + Volltext + Metadaten + SQL-Sicht `meldungen` |
| `answer` | S6/S8: 300 Fragen, je ein Router-Aufruf (+ ggf. Antwort-Aufruf) |

**Time-to-RAG** = Summe `ingest` … `index` (s und Wh, brutto); **J/Frage** = Energie von `answer` / 300.

## 3. SQL-Sicht und Router
Sicht `meldungen` (nur lesen): `meldung_id, werk, regierungsbezirk, anlage, equipment_nr, datum DATE,
prioritaet, stillstand_min, kosten_eur, komponente, schadensart, sicherheitsrelevant, meldungstext`
(`komponente`, `sicherheitsrelevant` = Track-A-Regeln; `schadensart` = Klassifikator aus `label`).

Router = **ein** LLM-Aufruf je Frage, JSON nach Schema (Constrained Decoding, temperature 0):
`{"route", "sql", "antwort_aus", "suchtext", "werk", "regierungsbezirk", "datum_von", "datum_bis",
"komponente", "schadensart"}` (die letzten beiden: Self-Query-Filter für die Suche, wenn das Thema sie nennt).
- `route = "sql"`: `sql` wird mit **sqlglot** geprüft: genau eine Anweisung, nur `SELECT`, nur die Sicht
  `meldungen`, keine Funktionen mit Seiteneffekten; Ausführung read-only mit Zeitlimit 10 s; jede SQL wird
  geloggt. `antwort_aus = "tabelle"` → Ergebnis ist die Antwort; `"text"` → zweiter LLM-Aufruf beantwortet die
  Frage aus den (≤ 5) gefundenen Meldungstexten, zitiert ihre `meldung_id`.
- `route = "search"`: Hybrid-Suche (dense + Volltext, RRF k = 60) mit den Filtern, **k = 100** Treffer →
  `cited_ids`.
- Abgelehnte oder fehlerhafte SQL → Antwort null (zählt als falsch), kein zweiter Versuch.

## 4. Bewertung (programmatisch)
- **lookup** (100; Ursache an Equipment/Werk/Datum, Maßnahme zu einer Meldungsnummer, Stillstand einer Anlage an
  einem Tag): Text exakt (case-folded, NFC) bzw. Zahl wie bei Aggregation; Zitat = `cited_ids` vs. Gold-Zeile.
- **filtered_semantic** (100): Mengen-F1 von `cited_ids` gegen die Gold-Menge.
- **aggregation** (100): Tabelle gegen Gold: gleiche Schlüssel (erste Spalte, NFC/case-folded) und jeder Zahlenwert
  mit relativem Fehler ≤ 5 % → 1 (Antwort mit einem Wert, z. B. „welches Werk“: exakt); zusätzlich mittlerer
  relativer Fehler berichtet. (`schadensart` ist geschätzt, Summen darüber sind Näherungen.)
- **Router-Genauigkeit**: aggregation/lookup → `sql`, filtered_semantic → `search`.
- **Gesamt** = Mittel der drei Typ-Werte; Zitier-Präzision/-Recall über lookup + filtered_semantic.

## 5. Verifikation (`zj verify`, Regel 4)
Bestanden, wenn alle 300 Fragen in Reihenfolge beantwortet sind (Felder vorhanden, `route` gültig) und **keine
SQL außerhalb der Regeln ausgeführt** wurde (Log `sql_rejected` vs. `sql_executed`). Die Qualität wird berichtet;
das Gewinner-Gate (Gesamt ≥ 0,9 × bestes Gesamt) wendet der Bericht an.

## English summary
Raw cp1252 CSV (1 M rows) → Track A columns + doc_text → bge-m3 vectors → damage type for all rows (LLM labels
a 2 000-row sample, a linear classifier on the embeddings labels the rest) → store with vector + full-text
index and a read-only SQL view → 300 questions, each routed by one constrained LLM call to SQL (sqlglot-checked
SELECT on the view, 10 s timeout) or filtered hybrid search (k = 100, self-query filters for component/damage type). Scored programmatically: lookup exact
cause, filtered-semantic set-F1 of cited IDs, aggregation tables within 5 % relative error; router accuracy,
citation P/R, time-to-RAG (s, Wh) and J/question.
