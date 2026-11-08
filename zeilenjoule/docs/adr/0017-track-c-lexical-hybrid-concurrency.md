# ADR 0017: Track C – Konkurrenz 16, `c.lexical` (BM25) und `c.hybrid` (RRF) mit prüfbaren Teillisten

**Status:** angenommen (2026-11-08)

## Kontext
PLAN §6/§9 verlangt QPS bei Konkurrenz 1 und 16, eine BM25-Basislinie (Tantivy), Hybrid-Suche mit RRF
(k = 60), nDCG auf den qrels und die Kompositum-Teilmenge. Jede Engine hat ihren eigenen Analyzer
(Stemmer, Stoppwörter) und ihr eigenes Ranking (BM25, `ts_rank`, FTS5-`bm25()`); ein Vergleich gegen eine
einzige BM25-Wahrheit würde fremde Analyzer als „falsch“ werten.

## Entscheidung
- **Konkurrenz 16 = zusätzliche Stufe `query_c16` in `c.dense`**: dieselben 1 000 Anfragen aus 16
  Client-Threads, jeder mit eigenem Handle (Verbindung/Cursor/gRPC-Kanal), Ergebnis `<out>.c16.npy`,
  gleiche Prüfung (Recall-Gate). Threads statt Prozesse: so spricht ein Gateway eine Datenbank an. GPU-
  Bibliotheken im Prozess teilen sich das Gerät ohne Batching (ein dynamischer Batcher wäre ein eigener
  Kandidat). Die bisherigen `c.dense`-Messungen werden durch die neue Session ersetzt (Bericht: neueste
  Session je Paar).
- **`c.lexical`** (Tantivy): Referenz = Tantivys Analyzer in Python nachgeschrieben (Snowball-Deutsch,
  `snowballstemmer` 3.1.1 = `frostem` in Tantivy 0.27) + BM25 k1 1,2 / b 0,75. Gate: **gleichstandsfester**
  BM25-Recall@10 ≥ 0,95 je Klasse (Toleranz 2 %: 1-Byte-Längennormen, viele gleiche Scores in kurzen
  Meldungen). Ungemessene Probe: Tantivy 0,9995 (real) / 1,0 (M).
- **`c.hybrid`**: jede Engine mit ihrem dokumentierten Weg (eine SQL-Anweisung mit zwei CTEs bei
  pgvector/DuckDB/SQLite; native Fusion bei LanceDB `RRFReranker` und Qdrant `RrfQuery`), je Bein 100
  Kandidaten. Nach `write` schreibt Stufe `legs` beide Kandidatenlisten; der Verifier rechnet die Fusion
  nach (Übereinstimmung ≥ 0,95, Rang-Ursprung 0/1 unterscheidet sich), prüft das Dense-Bein mit dem
  Recall-Gate und das lexikalische Bein nur mit einer **Plausibilitätsuntergrenze** (≥ 0,5 × Referenz-BM25-
  Qualität). Die Qualität (nDCG/hit) wird berichtet, nicht gegated: sie ist das Ergebnis.
- **Kompositum-Teilmenge** = Anfragen aus genau einem Wort (35 von 1 000 bei M).
- Postgres `ts_rank` ist **kein BM25** (keine IDF): im Kandidatennamen-Kommentar und im Bericht so
  benannt; FTS5 hat keinen deutschen Stemmer (nur englisches `porter`), also keiner.
- Task-Name `c.lexical` statt `c.bm25`: der Harness erlaubt nur Buchstaben im Task-Namen.

## Konsequenzen
- Wandzeit von `c.hybrid` enthält die Stufe `legs` (bei allen gleich); Latenz und QPS kommen aus `query`.
- CAGRA (c02) ist auch einzeln nicht deterministisch (zwei Läufe: 86 % identische Zeilen bei `none`); die
  Konkurrenzstufe ändert daran nichts, der Recall bleibt gleich.
- Tantivy-Hybrid (Rust, eigener Dense-Teil) und ein GPU-Batcher sind mögliche spätere Kandidaten.
