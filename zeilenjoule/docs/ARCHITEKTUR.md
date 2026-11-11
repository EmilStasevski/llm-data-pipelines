# ZeilenJoule: Vorschlag Produktionsarchitektur (Gate 2)

> **Status: Vorschlag, wartet auf Bestätigung (Gate 2).** Grundlage: `reports/WINNER.md` und `reports/REPORT.de.md`
> (generiert aus `results/zj.duckdb`). Zahlen unten sind Mediane aus 3 gemessenen Läufen (Session `444b1e05`,
> 2026-11-11, 1 000 000 Zeilen) bzw. aus den in WINNER.md genannten Track-Sessions. Entscheidung: ADR 0021.

## 1. Ergebnis der Gewinnerregel (PLAN §11.1)
- **Schnellste End-to-End-Pipeline, die alle Gates besteht: `e03_postgres_only`** – 1 740 s Wall-Zeit
  (Roh-CSV → Index → 300 Antworten), 247,9 kJ brutto, Gesamtqualität 0,754 (beste: 0,757 bei e05).
- Sie ist **zugleich die effizienteste** (Energieverhältnis 1,00) → Regel 5 verlangt keine Abwägung.
- Dienste: PostgreSQL, TEI, vLLM (3) gegenüber e02 (1: llama.cpp) → Δ +2, Grenze „> 2“ nicht überschritten.
- `e05_best_of_tracks` (Track-Sieger zusammengesetzt: Rust-Parser, TEI, Qdrant, vLLM) ist **nicht** schneller:
  Qdrant braucht für 1 Mio. Punkte 729 s Indexaufbau gegenüber 505 s bei pgvector. Der Track-C-Sieg von Qdrant
  wurde bei 100 000 Zeilen gemessen – die Zusammensetzung der Einzelsieger ist nicht automatisch der Gesamtsieger.
- `e04_pure_rust` wurde nicht gebaut (Entscheidung 2026-11-10, ADR 0020).

## 2. Wo Zeit und Energie hingehen (e03, Time-to-RAG 1 672 s / 66,4 Wh)
| Stufe | Zeit s | Energie Wh | Anteil Energie |
|---|---|---|---|
| ingest (Rust a16, S1–S3) | 3 | 0,0 | < 0,1 % |
| embed (TEI bge-m3, S4) | 977 | 53,3 | 80 % |
| label (vLLM 2 000 Zeilen + Klassifikator) | 186 | 6,1 | 9 % |
| index (Postgres COPY + HNSW + GIN) | 505 | 7,0 | 11 % |
| answer (300 Fragen, Stufe außerhalb Time-to-RAG) | 65 | 2,3 | 27,2 J/Frage |

**Folgerung:** Der Hebel liegt fast nur in S4. In Produktion wird jede Zeile genau einmal eingebettet
(inkrementell, beim Eintreffen); ein Voll-Neuaufbau ist die Ausnahme. Parsen ist mit Rust vernachlässigbar.

## 3. Zielbild (Phase 9)
```
                ┌──────────────────────────── Go ────────────────────────────┐
 Client ──────► │ zj-gateway  REST/OpenAPI + SSE · API-Key · Rate-Limit ·     │
                │             Router (1 LLM-Aufruf: SQL ↔ Suche) · sqlglot-   │
                │             äquivalente SELECT-Prüfung · OTel · /metrics    │
                │ zj-worker   Ingest-Jobs (River-Queue in Postgres) ·         │
                │             Batching zu TEI/vLLM · Backpressure · Retries   │
                └───────┬──────────────┬──────────────┬───────────────────────┘
                        │              │              │
             zj-parse (Rust a16/a17,   │              │
             als Batch-Binary)         ▼              ▼
                        │        TEI (bge-m3)    vLLM (Qwen3.5-9B AWQ)      [GPU, geteilt]
                        ▼        Einbettungen    Router · Antworten ·
                 PostgreSQL 18 + pgvector        Extraktion (/v1/extract) ·
                 halfvec-HNSW · tsvector-GIN ·   Schadensart-Etiketten
                 Sicht `meldungen` (Rolle reader,
                 statement_timeout) · River-Jobs
 zj-energy-exporter (Go: NVML + RAPL) ──► Prometheus ──► Grafana (J/Anfrage, Zeilen/s, p95, GPU W/°C)
```

## 4. Entscheidungen im Vorschlag (bitte bestätigen oder ändern)
1. **Datenhaltung = PostgreSQL + pgvector** für Vektoren, Volltext, SQL-Sicht **und** Job-Queue (River). Ein
   Datenbanksystem statt Qdrant + DuckDB; read-only-Rolle mit `statement_timeout` ist im Benchmark erprobt
   (2 400 Router-SQL in den 12 gemessenen Läufen, 0 abgelehnt, 0 fehlgeschlagen, 0 unsicher).
2. **Ein LLM für alles: Qwen3.5-9B AWQ auf vLLM** (Router, Antworten, Zeilen-Extraktion, Etiketten). In Track D
   erreicht diese Engine/Modell-Kombination 3-shot macro-F1 0,964 bei 6,24 Zeilen/s. Die sparsamere Kaskade d11
   (Gemma 4 E4B, 17,2 J/Zeile) bräuchte einen **zweiten** LLM-Server auf derselben 16-GB-GPU → nicht vorgeschlagen.
3. **Einbettung = TEI (bge-m3)**, schnellster und sparsamster Track-B-Kandidat; dauerhaft laufend, damit Suchanfragen
   online eingebettet werden (im Benchmark wurden die 300 Fragen gebündelt mit eingebettet, ADR 0020).
4. **Parser in Rust als Batch-Binary** (a16/a17, vom Go-Worker aufgerufen) statt gRPC-Dienst: Parsen kostet
   < 0,1 % der Energie; ein eigener Dienst brächte Betriebsaufwand ohne messbaren Gewinn. Das gRPC/cgo-Experiment
   aus PLAN §12.1 entfällt damit (oder wird als optionales ADR-Experiment nachgeholt).
5. **GPU-Teilung TEI + vLLM:** vLLM belegt im Benchmark 0,88 des Speichers; mit TEI daneben muss vLLM auf ≈ 0,75
   und `max_num_seqs` sinken. Das ist **nicht gemessen** und wird in Phase 9 zuerst geprüft (k3s: NVIDIA-Device-
   Plugin mit Time-Slicing; docker compose: beide Container auf einer GPU).

## 5. Offene Risiken
- **Qualität 0,75:** Nachschlagen 0,85, Suche 0,82, Aggregation 0,59. Aggregationen über die geschätzte Schadensart
  (Klassifikator stimmt zu 0,885 mit LLM-Etiketten überein) bleiben Näherungen – in der API als „geschätzt“ kennzeichnen.
- **Latenz:** die Benchmark-Latenzen (p50 ≈ 9,9 s) sind unter Bündellast mit 64 gleichzeitigen Fragen gemessen;
  Einzelanfrage-Latenz und TTFT unter Produktionslast sind nicht gemessen → Lasttests (vegeta/k6) in Phase 9.
- **Synthetische Daten:** Regeln und Katalog passen gut zu den erzeugten Meldungen; auf echten Daten sinkt der
  Regelanteil vermutlich.

## English summary
The fastest end-to-end pipeline that passes every gate is **e03_postgres_only** (1,740 s, 247.9 kJ for 1 M rows
and 300 questions, overall quality 0.754); it is also the most efficient, so rule 5 requires no trade-off. The
composed "best of tracks" pipeline (Qdrant) is slower at 1 M rows (index 729 s vs. 505 s). Embedding is 80 % of
the energy-to-RAG, so production embeds each row once, incrementally. Proposal: Go gateway + worker, Rust parser as
a batch binary, PostgreSQL + pgvector for vectors, full text, the read-only SQL view and the River job queue, TEI
for embeddings, one vLLM Qwen3.5-9B AWQ server for routing, answers and extraction, Prometheus/Grafana with the
NVML/RAPL energy exporter. GPU sharing between TEI and vLLM is not measured yet and is the first Phase 9 check.
