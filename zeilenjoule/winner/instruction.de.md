# ZeilenJoule Winner-Kit – Anleitung

*English: [instruction.md](instruction.md)*

Dieser Ordner installiert den **Gewinner des Benchmarks** als lauffähigen Dienst. Gewinner ist die schnellste und
sparsamste Pipeline auf L mit 1 Mio. Zeilen: `e03_postgres_only`, gemessen als Go-Stack `e06_stack_go`.

- **Datenbank:** eine PostgreSQL mit pgvector (HNSW) und deutschem Volltext, verbunden über RRF.
  - Kein separater Vektor-Store.
- **Dienste:**
  - Ein Go-Gateway mit HTTP-API, API-Schlüsseln und Demo-Seite.
  - Ein Go-Worker für den Ingest: Rust-CSV-Parser, Embeddings und Schadensart-Klassifikator.
- **Router-LLM:** Es entscheidet je Frage zwischen geprüftem, lesendem SQL und hybrider Suche.

Der Stack läuft **mit Docker** oder **ohne Docker und ohne sudo**, und zwar mit **beliebigen lokalen Modellen**.

```
        CSV ──► Worker ──► Rust-Parser ──► Embeddings ──► PostgreSQL (pgvector + deutscher Volltext)
                                                               ▲
  Frage ──► Gateway ──► Router-LLM ──► SQL (nur-Lese-Rolle) ───┤
                                   └─► hybride Suche ──────────┘
```

Alle Einzelheiten (Tabellen, Rezepte, Fehlerbehebung) stehen in der englischen Fassung
[instruction.md](instruction.md). Die Abschnittsnummern sind dort dieselben.

## 1. Schnellstart

**Ohne Docker, mit Ollama** (GPU oder CPU):
```bash
cd winner
./kit setup native --preset ollama   # PostgreSQL + pgvector, baut Go/Rust (~3 min), kein sudo
./kit start                          # startet Ollama, falls es nicht läuft, und lädt qwen3:8b + bge-m3
./example/run.sh                     # lädt 100 Zeilen, stellt 9 Fragen und prüft die Antworten
```

**Mit Docker und den gemessenen Modellen** (vLLM + TEI; NVIDIA-GPU mit 16 GB):
```bash
cd winner
./kit setup docker --preset vllm-tei
./kit start                          # der erste Start lädt ≈ 9 GB Modelle
./example/run.sh
```

Danach `http://127.0.0.1:8080/demo` im Browser öffnen. Der API-Schlüssel steht als `ZJ_API_KEYS` in
`winner/winner.env`.

## 2. Voraussetzungen

- **Nativ:**
  - Linux x86_64 und ein normaler Benutzer, **kein sudo**
  - `curl`, `tar`, `sha256sum`, dazu `python3` für die Client-Befehle
  - Go und Rust werden genutzt, wenn vorhanden, sonst in `winner/.run` geladen.
  - PostgreSQL 18.7 und pgvector 0.8.6 kommen von conda-forge über micromamba.
  - Alle Downloads sind versioniert und per sha256 geprüft.
- **Docker:** Docker Engine mit compose v2 und Mitgliedschaft in der Gruppe `docker`.
  - Für `vllm-tei` zusätzlich eine NVIDIA-GPU mit ≥ 16 GB und das NVIDIA Container Toolkit.

## 3. Benutzung

```bash
./kit ingest meine_meldungen.csv            # hochladen und auf den Job warten (gleiche meldung_id = Aktualisierung)
./kit ask "Wie viele Meldungen gab es 2025 im Werk Krefeld?"
./kit search "Lager läuft heiß"             # nur hybride Suche (Top 10)
./kit status                                # Prozesse + /readyz (database, llm, tei)
```

**Eingabe:** SAP-PM-artige CSV mit `;` als Trennzeichen, in cp1252 oder UTF-8, mit diesen Spalten:
`meldung_id;werk;anlage;equipment_nr;meldungsdatum;schicht;prioritaet;meldungstext;stillstand;kosten;status`.
Beispiel: `example/stoermeldungen_100.cp1252.csv`.

**API:**
- Die Spezifikation liefert `GET /v1/openapi.json`; Einzelheiten stehen in `docs/RUNBOOK.md` §3.
- Mit `Accept: text/event-stream` kommt die Antwort als Strom `route` → `plan` → `answer` → `done`.

## 4. Beliebige lokale Modelle

Alle Modell-Einstellungen stehen in `winner/winner.env`. Presets (`--preset`) füllen sie für:
- `vllm-tei` (gemessen, nur Docker)
- `ollama`
- `llamacpp` (nur nativ)
- `external` (jeder OpenAI-kompatible Server)

**Chat-Modell (`ZJ_LLM_*`):**
- Der Server braucht OpenAI `/v1/chat/completions` mit `response_format: json_schema`.
- **Das „Thinking“ abschalten**, über `ZJ_LLM_EXTRA_JSON`. Sonst verbrauchen Denkmodelle ihr Token-Budget, bevor das
  JSON kommt, und die Antwort bleibt leer.
  - vLLM, llama.cpp: `'{"chat_template_kwargs":{"enable_thinking":false}}'`
  - Ollama: `'{"reasoning_effort":"none"}'`
  - Modelle ohne Thinking: `'{}'`

**Embedding-Modell (`ZJ_EMBED_*`):**
- `ZJ_EMBED_API=openai` (`/v1/embeddings`) oder `tei`.
- `ZJ_EMBED_DIM` muss der Vektorgröße des Modells entsprechen. Sie wird beim Anlegen der Datenbank festgelegt.
  **Nach einem Modellwechsel:** `./kit reset-db`, dann neu ingestieren.
- Präfixe, z. B. für e5: `ZJ_EMBED_QUERY_PREFIX='query: '`, `ZJ_EMBED_DOC_PREFIX='passage: '`.

Die Rezepte für ein anderes Ollama-Modell, llama.cpp ohne sudo und vLLM ohne Docker stehen in
[instruction.md §4](instruction.md#4-using-any-local-model).

## 5. Server ohne sudo

```bash
./winner/kit pack                                    # → winner/dist/zeilenjoule-winner.tar.gz
scp winner/dist/zeilenjoule-winner.tar.gz server:
# auf dem Server
tar xzf zeilenjoule-winner.tar.gz && cd zeilenjoule-winner/winner
./kit setup native --preset ollama --with-ollama     # Ollama im Benutzerverzeichnis, falls keines vorhanden ist
./kit start
```

- **Ablage:** Alles liegt unter `winner/.run`; mit `ZJ_HOME` lässt sich der Ort ändern.
- **Ports:**
  - PostgreSQL lauscht nur auf `127.0.0.1:55432`.
  - Das Gateway lauscht auf `ZJ_BIND:ZJ_GATEWAY_PORT`.
- **Zugriff von außen:** einen TLS-Reverse-Proxy davorsetzen oder einen SSH-Tunnel nutzen.
- **Start nach dem Booten ohne sudo:** ein Eintrag in der Benutzer-crontab:
  `@reboot cd /pfad/zeilenjoule-winner/winner && ./kit start >> .run/logs/boot.log 2>&1`

## 6.–9. Docker-Details, Betrieb, Beispiel, Fehlerbehebung

Siehe [instruction.md](instruction.md), dort in den Abschnitten 6–9:
- TEI-Image je GPU
- lokale Modellordner
- Backup und Deinstallation
- Fehlertabelle

Das Beispiel in `example/` besteht aus:
- 100 synthetischen Störmeldungen
- 9 deutschen Fragen mit Sollwerten aus den Gold-Daten (`derive_expected.py`)
- `run.sh`, das jede Antwort prüft und bei Fehlern das erzeugte SQL zeigt.

## 10. Grenzen

- **Gemessen:** Nur vLLM + TEI ist gemessen (`reports/`). Andere Modelle funktionieren, sind aber **nicht
  gemessen**.
- **Eingabe:** Der Stack ist auf Störmeldungen mit den Spalten oben zugeschnitten. Werks-Katalog,
  Schadensart-Klassen und Prompts sind deutsch. Andere Tabellen erfordern Änderungen an Parser, Schema und Prompts.
- **Getestet:**
  - nativ mit Ollama und mit llama.cpp
  - Docker mit vLLM + TEI
  - Docker mit Ollama bzw. `external` ist gleich aufgebaut, lief aber nicht komplett durch.
- **Nicht enthalten:** Monitoring (Joule je Frage) und Kubernetes. Dafür ist der volle Stack unter `deploy/` da
  (`docs/RUNBOOK.md`).

Begründungen: ADR 0021, 0027, 0028.
