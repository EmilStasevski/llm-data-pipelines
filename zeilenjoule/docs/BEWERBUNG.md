# Bewerbungs-Kit – ZeilenJoule

> Für Werkstudenten-Bewerbungen in der NRW-Industrie (Data Engineering, Datenbanken, KI/RAG, DevOps,
> Energieeffizienz). Alle Zahlen stehen in den **generierten Blöcken** (`task report` aus `results/`), nicht im
> Fließtext.

## 1. Kurzfassung (eine Seite)

**Frage:** Wie viel Zeit und Energie kostet es, deutsche Industrie-Tabellen (Störmeldungen im SAP-PM-Stil) zeilenweise
für Retrieval-Augmented Generation aufzubereiten und abzufragen – und welches Werkzeug ist auf einem einzelnen
Rechner (RTX 5060 Ti 16 GB) am schnellsten, ohne an Qualität zu verlieren?

**Vorgehen:**
- Fünf Tracks, jede Pipeline-Stufe einzeln:
  - A Parsen/Bereinigen
  - B Embeddings
  - C Index/Suche
  - D LLM-Extraktion
  - E Roh-CSV bis Antwort
- 76 Wettbewerber-Konfigurationen (`config/contestants.yaml`): pandas, Polars (CPU/GPU), cuDF, DuckDB, Rust, PyTorch/ONNX/TEI/vLLM/llama.cpp, pgvector,
  Qdrant, LanceDB, Tantivy, lokale LLMs.
- Eigenes Mess-Harness in Go (`zj`):
  - Wall-Zeit sowie GPU-Energie (NVML) und CPU-Energie (RAPL) je Stufe
  - Leerlauf-Abzug, mehrere Wiederholungen, Median
  - Prüfung jedes Laufs gegen eine Referenzimplementierung: schnell, aber falsch heißt disqualifiziert.
- Der Gewinner (PostgreSQL + pgvector + Volltext, ein Router-LLM) läuft als **Go-Produktions-Stack**:
  - Gateway + Worker, OpenAPI
  - Docker Compose, k3s/Helm, NetworkPolicies
  - Prometheus/Grafana mit **Joule je Frage**
  - lokale CI (Lint, Tests, Schwachstellen-Scan, SBOM)
- Der Stack ist selbst gemessen, einmal als Pipeline und einmal unter offener Last mit SLOs.

**Kennzahlen:**

<!-- zj:facts:begin -->

Generiert von `task report` aus `results/` (Median der neuesten Sitzung, nur geprüfte Läufe); Wall-Zeit und Energie brutto (GPU + CPU) einer Messung.

- **Track A (Parsen + Bereinigen, Skala L):** schnellster `a17_rs_handrolled` 0,9 s, langsamster `a04_pandas_apply` 82,5 s (Faktor 90); Energie 56 J gegen 2 307 J (Faktor 41).
- **Track B (Embeddings, Skala M):** schnellster `b06_tei` 109 s, langsamster `b01_st_fp32` 459 s (Faktor 4,2); Energie 19 632 J gegen 87 936 J (Faktor 4,5).
- **Track C (Vektorsuche, Skala M):** schnellster `c00_exact_gpu` 4,5 s, langsamster `c03_pgvector` 124 s (Faktor 28); Energie 228 J gegen 3 278 J (Faktor 14).
- **Track E (Roh-CSV → RAG, Skala L):** schnellster `e03_postgres_only` 1 740 s, langsamster `e01_classic_python` 5 330 s (Faktor 3,1); Energie 247 895 J gegen 957 825 J (Faktor 3,9).
- **Track D (LLM-Extraktion, 2 000 Meldungen):** bestes macro-F1 `d02_llamacpp_qwen35_9b_q4_3shot` 0,971 bei 36,2 J/Zeile; Kaskade `d11_cascade` 0,966 bei 17,5 J/Zeile (52 % weniger Energie).
- **Produktions-Stack (Go, Phase 9):** `e06_stack_go` 1 724 s gegen Skript `e03_postgres_only` 1 740 s (Wall -0,9 %, Energie -1,2 %).
- **Lastkurve `e07_stack_load` (Skala L):** Suche stabil bis 320 req/s (p95 99 ms); Fragen stabil bis 2 req/s (p95 4 183 ms) – SLO-Tabelle im Bericht.

<!-- zj:facts:end -->

**Wo nachlesen:**
- Ergebnisse: [`README.de.md`](../README.de.md), [`reports/REPORT.de.md`](../reports/REPORT.de.md), Gewinneranalyse in [`reports/WINNER.md`](../reports/WINNER.md)
- Methodik: [`docs/METHODIK.md`](METHODIK.md)
- Entscheidungen: [`docs/adr/`](adr/) (Architecture Decision Records)

## 2. Fünf Gesprächspunkte

| # | Typische Anforderung | Was ich im Projekt gemacht habe | Beleg |
|---|---|---|---|
| 1 | **Python, SQL, ETL** | Messy-CSV-Import (cp1252, deutsche Zahlen-/Datumsformate, Freitext-Normalisierung) in pandas, Polars, DuckDB und Rust; Referenzimplementierung als Wahrheit; Anti-Muster bewusst mitgemessen | Track A im Bericht, `reference/`, `contestants/` |
| 2 | **Datenbanken** | pgvector HNSW + deutscher Volltext + RRF in *einer* PostgreSQL; Vergleich mit Qdrant, LanceDB, DuckDB, SQLite; Router-SQL nur über eine SELECT-only-Rolle mit Statement-Timeout; Ursachensuche per Abfrageplan | Track C/E, ADR 0021, ADR 0026/0027 |
| 3 | **Docker, Kubernetes, CI/CD** | Compose-Profile, Helm-Chart (k3s) mit NetworkPolicies und PodSecurity „restricted“, gepinnte Image-Digests, lokale CI mit golangci-lint, govulncheck, Trivy, SBOM, kubeconform | `deploy/`, `docs/CI.md`, `docs/RUNBOOK.md` |
| 4 | **Monitoring & Energieeffizienz** | Energie-Exporter (NVML + RAPL) → Prometheus → Grafana „Joule je Frage“; Lastkurve mit offener Last (kein *coordinated omission*) und J/Anfrage; jede Zahl reproduzierbar aus `results/` | `harness/`, `deploy/monitoring/`, ADR 0026 |
| 5 | **KI / LLM / RAG** | Lokale LLMs auf 16 GB (Ollama, llama.cpp, vLLM), JSON-Schema-beschränkte Ausgabe, Regel→GLiNER→LLM-Kaskade, Router SQL ↔ Suche, Qualitäts-Gates vor Geschwindigkeit; Go-Services, Rust-Parser | Track D/E, `services/` |

Ehrliche Grenzen:
- Die Daten sind synthetisch, mit Gold-Labels. Reale offene Daten (thru.de, TED, BA) dienen nur für Robustheit und Demo.
- Gemessen wurde auf einer einzigen Maschine.
- Die SLO-Geschichte ist im Bericht nachvollziehbar, inklusive der ersten, verfehlten Messung (ADR 0026/0027).

## 3. Welche Fähigkeiten verlangen NRW-Werkstudenten-Anzeigen?

<!-- zj:skills:begin -->

Datensatz: 786 Werkstudenten-Anzeigen der BA-Jobsuche in NRW (28 Städte, Umkreis 25 km), abgerufen mit ≤ 1 Anfrage/s; ausgewertet werden nur Titel und Berufsbezeichnungen (der Listen-Endpunkt liefert keinen Anzeigentext). Eine Anzeige kann in mehrere Gruppen fallen. Generiert von `task report` (`analysis/…/skills.py`).

| Gruppe (Schlüsselwörter in Titel/Beruf) | Anzeigen | Anteil |
|---|---|---|
| Ingenieurwesen & Technik (Maschinenbau, Elektro, Produktion) | 129 | 16,4 % |
| Marketing, Vertrieb, Kommunikation | 126 | 16,0 % |
| Aushilfe Gastronomie / Handel / Logistik | 97 | 12,3 % |
| Softwareentwicklung (Entwicklung, Informatik, Cloud, DevOps) | 91 | 11,6 % |
| Personal / HR | 57 | 7,3 % |
| Controlling, Finanzen, Einkauf | 55 | 7,0 % |
| IT-Betrieb & Support (Systeme, Netzwerk, Administration) | 38 | 4,8 % |
| Daten & Analytics (Data, BI, Reporting, Statistik) | 20 | 2,5 % |
| Energie, Umwelt, Nachhaltigkeit | 19 | 2,4 % |
| KI / Machine Learning | 16 | 2,0 % |

- Mindestens eine der Gruppen Daten, Software, IT-Betrieb oder KI: **134** Anzeigen (17,0 %).
- Keiner Gruppe zugeordnet: 245 (31,2 %).

<!-- zj:skills:end -->

Lesart:
- Die Gruppen Daten, Software, IT-Betrieb und KI passen zu den Gesprächspunkten 1–5.
- Für Ingenieur- und Technikstellen in der Industrie ist die Energie-Perspektive (Joule je Zeile, je Anfrage) das
  Bindeglied.
- Grenze der Auswertung: nur Titel und Berufsbezeichnungen, Schlüsselwortgruppen statt Anzeigentext; ein grober
  Indikator, keine Arbeitsmarktstudie.

## 4. Demo (≈ 5 Minuten)

Voraussetzung: `.env` aus `.env.example`, Modelle unter `models/` (siehe `docs/RUNBOOK.md`).

1. `task demo`:
   - startet den Stack (Postgres, TEI, vLLM, Gateway, Worker, Prometheus, Grafana)
   - lädt die Stichprobe S
   - stellt drei deutsche Fragen und zeigt die Energie je Frage
2. Auf die Ausgabe achten:
   - Die **Route** (SQL oder Suche) kommt nach Sekundenbruchteilen, weil das Router-LLM gestreamt wird.
   - Danach kommen der **Plan** (geprüftes SQL bzw. Suchtext) und die **Antwort mit zitierten Zeilen**.
3. Die Demo-Seite `http://127.0.0.1:8080/demo` im Browser öffnen:
   - API-Schlüssel aus `.env` eintragen und eine eigene Frage stellen
   - die Kacheln „J je Frage“ und „Leistung“ werden aus Prometheus gelesen
4. Grafana `http://127.0.0.1:3000`, Dashboard „ZeilenJoule“: Joule je Frage, Latenz je Endpunkt, vLLM-Warteschlange.
5. Zum Schluss den Bericht zeigen: [`reports/REPORT.de.md`](../reports/REPORT.de.md), Abschnitt „Abbildungen“ (Pareto-Fronten, Lastkurve).

Aufräumen: `task down`.
