# ZeilenJoule

> *„Wie viele Joule kostet eine Zeile?"*

ZeilenJoule misst **zeilenweise RAG auf deutschen (NRW-)Industriedaten**: Störmeldungen, Q-Meldungen und Prüfprotokolle.
Jede Pipeline-Stufe (Einlesen, Normalisieren, Embedden, Indexieren, Suchen, LLM-Extraktion, Antworten) wird mit vielen Werkzeugen umgesetzt, von naivem pandas über Polars, GPU-Dataframes und reines Rust bis zu Datenbanken und lokalen LLMs.
Gemessen werden **Laufzeit und Energie (Joule)** auf einem Rechner mit NVIDIA RTX 5060 Ti 16 GB.
Der schnellste Stack, der alle Qualitätsprüfungen besteht, bekommt anschließend ein Go-basiertes Produktions-Setup.

## Status
**Phasen 0–6 abgeschlossen**: Track A (Dataframes), B (Embeddings) und C (Speicherung/Index/Suche: dense, BM25, hybrid) sind gemessen.
Ergebnisse: [`reports/REPORT.de.md`](reports/REPORT.de.md) (English: [`reports/REPORT.md`](reports/REPORT.md)). Alle Zahlen dort stammen ausschließlich aus `results/` (Harness-Ausgabe) und werden von `task report` erzeugt. Ein Lauf zählt nur, wenn er die Prüfung gegen die Referenzimplementierung besteht.

| Phase | Inhalt | Status |
|---|---|---|
| 0 | Umgebungsprüfung, Kompatibilitätsmatrix | ✅ `docs/ENVIRONMENT.md`, `docs/COMPAT.md` |
| 1 | Gerüst: Taskfile, uv/cargo/Go-Projekte, Lint/Test | ✅ |
| 2 | Mess-Harness `zj` (Zeit + GPU/CPU-Energie), Kalibrierung | ✅ `docs/CALIBRATION.md`, `docs/METHODIK.md` |
| 3 | Synthetische NRW-Daten mit Gold-Labels, Fragen, reale Daten | ✅ `data/cards/` (Abruf realer Daten: `task data:fetch`) |
| 4 | Track A: Dataframe- und Zeilenverarbeitung (Python-Schleife, pandas, Polars inkl. GPU, cuDF, DuckDB, Rust) | ✅ gemessen |
| 5 | Track B: Embeddings (bge-m3: PyTorch, ONNX Runtime, TEI, vLLM, llama.cpp, Rust) | ✅ gemessen |
| 6 | Track C: Speicherung, Index, Suche (FAISS, cuVS, pgvector, DuckDB, SQLite, LanceDB, Qdrant, Tantivy; Konkurrenz 1/16, Hybrid-RRF) | ✅ gemessen |
| 7 | Track D: LLM-Extraktion | als Nächstes |
| 8 | Track E: End-to-End-RAG, Gewinner | offen |
| 9–10 | Produktion (Go/DevOps), Portfolio | offen |

## Schnellstart
```bash
task setup      # alle Abhängigkeiten aus den Lockfiles installieren, zj bauen
task doctor     # Umgebung prüfen (GPU, RAPL, Docker, Last, Werkzeuge)
task ci         # Lint + Tests für Python, Rust und Go
```
Voraussetzungen: Ubuntu 24.04, NVIDIA-Treiber ≥ 580, Docker (docker-ce) + NVIDIA Container Toolkit, uv, rustup, Go ≥ 1.27, go-task.

## Aufbau
`harness/` (Go: `zj`-CLI) · `libs/` (zjmark-Stufenmarker für Python/Rust/Go) · `contestants/` (Wettbewerber je Sprache/Umgebung) · `config/` · `spec/` · `docs/` (Plan, Methodik, ADRs).
Details: `docs/PLAN.md`, Entscheidungen: `docs/adr/`.

English version: [README.md](README.md)
