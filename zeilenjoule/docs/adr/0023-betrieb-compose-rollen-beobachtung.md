# ADR 0023: Betrieb des Produktions-Stacks – Compose-Profile, Rollen, Images, Beobachtung, Tests

**Status:** angenommen (2026-11-11)

## Kontext
ADR 0021 legt den Stack fest (Postgres + pgvector, TEI, vLLM, Go-Gateway/-Worker, Rust-Parser). Offen war, wie er
gebaut, gestartet, abgesichert, beobachtet und getestet wird, ohne die gemessene Konfiguration zu verändern.

## Entscheidung
- **Gleiche Bytes wie gemessen:** Prompts kommen aus denselben Python-Buildern (`task services:prompts`, eingebettet
  per `go:embed`); einzige Abweichung: der Router schreibt PostgreSQL- statt DuckDB-SQL (kein Transpiler in Go, ADR 0022).
  Modelle, Images (Digest), vLLM-/TEI-Flags und Index-Parameter (HNSW m 16 / ef_construction 200, ef_search 100,
  RRF k 60, Tiefe 100) entsprechen e03 bzw. dem GPU-Teilungs-Nachtrag.
- **docker compose mit Profilen:** ohne Profil Postgres + Migration + Gateway + Worker; `gpu` startet TEI und vLLM
  (vLLM wartet auf TEI, damit sein KV-Cache aus dem Rest-Speicher bemessen wird); `observability` Prometheus,
  Grafana, Energie-Exporter. Nur 127.0.0.1 ist veröffentlicht. Ein CPU-Profil entfällt: ohne GPU gibt es keinen
  gemessenen Ersatz für vLLM; Modellserver lassen sich per `ZJ_TEI_URL`/`ZJ_LLM_URL` extern angeben.
- **Drei Datenbank-Rollen:** Eigentümer (Migration, Ingest, Suche), `zj_reader` (NOLOGIN, nur `SELECT` auf die Sicht),
  `zj_ask` (Login, Mitglied von `zj_reader`, Standard read-only + 10 s). Das Gateway setzt beides zusätzlich je
  Transaktion. Die Prüfung mit `pg_query_go` ist Schicht 1, die Rolle Schicht 2 (Integrationstest belegt beides).
- **Ingest als River-Job:** Upload-Zeile und Job in **einer** Transaktion; Upsert über `meldung_id` macht
  Wiederholungen idempotent; ein Worker-Slot (GPU ist der Engpass); Einbetten der nächsten Charge überlappt mit dem
  Schreiben der aktuellen. Schadensart: gespeicherter Klassifikator, sonst LLM-Etiketten + neuer Klassifikator
  (gleiche Prozedur wie ADR 0020, in Go); Herkunft je Zeile in `schadensart_quelle`.
- **Images:** mehrstufig, Basis-Images per Digest, Laufzeit `distroless/cc` (cgo für pg_query_go), Nutzer nonroot,
  read-only Root-FS, keine Capabilities. Ausnahme Energie-Exporter: läuft als root (RAPL `energy_uj` ist nur für root
  lesbar), aber read-only und ohne Capabilities.
- **Beobachtung:** Dienste exportieren Latenz, Token, Routen, SQL-Ausgänge, Ingest-Zeilen; Joule je Frage/Zeile
  entstehen als Recording-Rule aus dem Exporter (`zj gpu/cpu energy`) geteilt durch die Zähler – Brutto-Energie der
  Maschine, nicht pro Anfrage zugeordnet (bei Parallelität eine Näherung, im Dashboard so benannt).
- **Tests:** Integrationstest gegen einen Wegwerf-Container (`task test:services:int`) statt testcontainers-go
  (weniger Abhängigkeiten, gleiche Aussage); TEI/vLLM sind dort Attrappen. OpenTelemetry-Tracing wird zurückgestellt,
  bis Lasttests zeigen, dass Metriken + strukturierte Logs nicht reichen.

## Konsequenzen
- Prompt-Änderung (PostgreSQL) ist ungemessen: die Qualität des Produktions-Routers wird mit den 300 Track-E-Fragen
  gegen den Stack nachgeprüft (Phase-9-Lasttest), bevor Zahlen in Berichte gehen.
- HNSW wächst inkrementell: ein Erst-Import von 1 Mio. Zeilen ist langsamer als der Massenaufbau im Benchmark.
