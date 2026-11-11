# ADR 0020: Track E – Pipelines aus gemessenen Bausteinen, Schadensart per Destillation, ein Router-Aufruf

**Status:** angenommen (2026-11-10)

## Kontext
PLAN §11 verlangt Time-to-RAG für 1 Mio. Zeilen, 300 Fragen mit Router (SQL ↔ Vektor) und fünf Pipelines
(e1 klassisch Python, e2 eingebettet, e3 nur Postgres, e4 reines Rust, e5 Bestes je Track). 50 der 100
Aggregationsfragen filtern nach `schadensart`, die keine Regel liefert (Track D); LLM-Extraktion aller 1 Mio.
Zeilen bräuchte mit der sparsamsten Konfiguration (d11, 5,25 Zeilen/s) ≈ 53 h und ≈ 4,8 kWh.

## Entscheidung
- **Bausteine statt Neubau:** Jede Pipeline ruft für S1–S3 einen Track-A-Kandidaten und für S4 einen
  Track-B-Kandidaten über deren CLI auf (gleicher Vertrag, gleiche Prüfung); S5 nutzt die Ladewege der
  Track-C-Kandidaten. So misst Track E die Zusammensetzung, nicht neuen Code.
- **Schadensart per Destillation:** das LLM der Pipeline etikettiert 2 000 zufällige Zeilen (Schema nur
  `schadensart`), ein Softmax-Klassifikator auf den ohnehin berechneten bge-m3-Vektoren etikettiert den Rest
  (Probe mit Gold-Etiketten: 0,951 Genauigkeit bei 2 000 Trainingszeilen, < 1 s). Kosten in Stufe `label`.
  Aggregationen über `schadensart` sind damit Näherungen → Toleranz 5 % relativ, Fehler wird berichtet.
- **Ein LLM für alle Pipelines:** Qwen3.5-9B (bestes macro-F1 in Track D) in der jeweiligen Engine
  (Ollama `qwen3.5:9b`, llama.cpp Q4_K_M, vLLM AWQ) – die Pipelines unterscheiden sich im Stack, nicht im Modell.
- **Ein Router-Aufruf** mit JSON-Schema statt Agenten-Schleife; SQL nur `SELECT` auf die Sicht `meldungen`
  (sqlglot), read-only, 10 s. Router- und Antwort-Prompts zentral erzeugt, drei handgeschriebene Beispiele,
  die nicht aus der Fragenmenge stammen.
- **Zusammensetzung:**
  | Pipeline | S1–S3 | S4 | S5 / SQL | LLM |
  |---|---|---|---|---|
  | e01_classic_python | a05 pandas vektorisiert | b01 sentence-transformers fp32 | FAISS (flach, CPU) + sqlite3 | Ollama |
  | e02_modern_embedded | a10 Polars lazy | b04 ONNX Runtime CUDA | DuckDB vss + fts + SQL | llama.cpp |
  | e03_postgres_only | a16 Rust arrow/rayon | b06 TEI | Postgres pgvector + FTS + SQL | vLLM |
  | e05_best_of_tracks | Track-A-Sieger | Track-B-Sieger | Track-C-Hybrid-Sieger + DuckDB für SQL | Track-D-Sieger-Engine |
  e5 wird beim Start aus `results/zj.duckdb` bestimmt (schnellste gewertete Konfiguration je Track) und im
  Ergebnis vermerkt. **e04_pure_rust wird vor Gate 2 nicht gebaut** (Entscheidung des Nutzers, 2026-11-10): LanceDB-Rust +
  SQL + Router in Rust wären der größte Neubau, die Rust-Bausteine (a16/a17) stecken bereits in e03/e05. Kann nach
  Gate 2 nachgeholt werden; WINNER.md weist darauf hin.

## Konsequenzen
- Ein Lauf auf L dauert 30–90 min (S4 dominiert); 3 Wiederholungen je Pipeline.
- Router-Fehler (falsche Route, ungültige SQL) zählen als falsche Antwort, nicht als Ausfall.
- Ergebnisse sind an Qwen3.5-9B gebunden; ein kleineres Modell könnte den Router schlechter bedienen.
- **Ollama in e01 mit N = 16 statt 64:** Router-Prompt + Beispiele brauchen 4 096 Token Kontext je Platz; bei
  64 Plätzen verteilt Ollama Qwen3.5-9B stillschweigend 25 %/75 % auf CPU/GPU (`ollama ps`, ungemessener Probelauf
  lief in Zeitüberschreitungen). Der Client prüft jetzt nach dem Laden `ollama ps` = „100% GPU“ und bricht sonst ab
  (Regel 6). Die gemessenen Track-D-Läufe (64 × 2 048) liefen nachweislich zu 100 % auf der GPU.
- **Zeitlimit je Kandidat:** der Harness kennt jetzt `timeout_s` je Wettbewerber (Vorrang vor `bench.yaml`); die
  Track-E-Pipelines bekommen 10 800 s, alle anderen behalten 1 800 s. Ein erster gemessener Versuch (Session
  `ba8fd0f7`, 2026-11-10) brach e02 und e01 nach 1 800 s ab (Status dnf, im Bericht unter „nicht gewertet“).
