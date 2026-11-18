# ADR 0028: Winner-Kit – Installation mit/ohne Docker und beliebige lokale Modelle

**Status:** angenommen (2026-11-18)

## Kontext
Der Nutzer will den Gewinner (`e03_postgres_only` als Go-Stack, ADR 0021/0027) als eigenständige Installation:
- mit Docker und ohne Docker,
- ohne Docker auf einem Server ohne sudo,
- mit einem beliebigen lokalen Modell, nicht nur vLLM + TEI.

Der Stack war auf drei Stellen festgelegt:
- TEI-API (`/embed`),
- 1 024-dimensionale Vektoren (`halfvec(1024)`),
- vLLM-Feld `chat_template_kwargs` zum Abschalten des „Thinking“.

## Entscheidung
**1. Ordner `winner/`** mit einem Skript `kit`:
- `setup native|docker` mit Presets: `vllm-tei` (gemessen), `ollama`, `llamacpp`, `external`
- `start`, `stop`, `status`, `ingest`, `ask`, `reset-db`, `pack`
- Konfiguration in `winner/winner.env`; Geheimnisse werden beim Setup erzeugt.

**2. Native ohne sudo:** alles unter `winner/.run`.
- PostgreSQL 18.7 + pgvector 0.8.6 von conda-forge über micromamba, eine statische Binärdatei.
- Go und Rust: vorhandene Toolchains, sonst Downloads im Nutzerverzeichnis. Alle Downloads sind gepinnt und per
  sha256 geprüft.
- `initdb` mit dem eingebauten Locale-Provider C.UTF-8. `lower()` und Umlaute funktionieren dann ohne OS-Locales.
  Der deutsche Volltext ergibt dieselben Lexeme wie das gemessene pgvector-Image (geprüft).
- Unix-Sockets sind aus; nur TCP auf 127.0.0.1. Socket-Pfade sind auf 107 Bytes begrenzt, und lange Pfade im
  Home-Verzeichnis sind im Test daran gescheitert.

**3. Beliebige Modelle**, mit neuen, optionalen Schlüsseln (ohne sie bleibt das Verhalten gleich):
- `ZJ_EMBED_API=openai`: `/v1/embeddings`, mit L2-Normierung im Client.
- `ZJ_EMBED_DIM`: Die Migration ersetzt `halfvec(1024)` beim ersten Anlegen. `db.CheckDim` bricht Gateway und
  Worker mit einer klaren Meldung ab, wenn die Datenbank für eine andere Größe angelegt wurde.
- Präfixe für e5-artige Modelle.
- `ZJ_LLM_EXTRA_JSON`: serverabhängige Anfragefelder.
  - Die Voreinstellung ist das gemessene `chat_template_kwargs`.
  - Ollama ignoriert dieses Feld; ein Denkmodell verbraucht dann sein ganzes Token-Budget, bevor das JSON kommt
    (geprüft).
  - `{"reasoning_effort":"none"}` wirkt bei Ollama, würde aber von vLLM abgelehnt. Deshalb ist es Konfiguration
    und nicht Code.
- Bereitschaftsprüfung: `/health`, sonst `/v1/models`, weil Ollama kein `/health` hat.
- Optional ein Bearer-Token.

**4. Kleine Tabellen in der Common-Terms-Regel (Korrektur zu ADR 0027):** Selten heißt jetzt
Dokumentfrequenz ≤ max(1 % der Zeilen, 50 Zeilen).
- Bei 100 Zeilen galt sonst jedes Wort aus zwei Zeilen als häufig. Im Beispiel fiel „Brand“ aus der
  lexikalischen Stufe, und die dichte Stufe allein rankte „Bruch“ davor.
- Ab 5 000 Zeilen ändert sich nichts; die gemessenen L-Läufe (1 Mio. Zeilen) sind nicht betroffen.

**5. Beispiel `winner/example/`:**
- 100 Zeilen, die ersten Datensätze von S
- 9 Fragen mit Sollwerten, berechnet von `derive_expected.py` aus den Gold-Daten
- `run.sh` prüft jede Antwort.

## Prüfung (ungemessen, Entwicklung)
Alle drei Wege liefen auf diesem Rechner einmal komplett durch:
- Docker `vllm-tei` (gemessene Modelle)
- nativ `ollama` (qwen3:8b + bge-m3), auch aus dem gepackten Bündel an einem fremden Pfad
- nativ `llamacpp` (llama-server über das Docker-Image als Test-Shim)

Ergebnis des Beispiels: 8/9, 8/9 und 7/9. Die Fehler sind Modellfehler, und jeder Weg verfehlt eine andere Frage:
- `sum()` über boolean
- falscher Schadensart-Filter
- `ORDER BY … DESC` ohne `NULLS LAST`

Die Prompts bleiben unverändert, denn sie bestimmen die gemessene Qualität.

## Konsequenzen
- Mit anderen Modellen ist der Stack nicht gemessen. Zahlen gelten nur für `e06`/`e07` (vLLM + TEI).
- llama.cpp ohne Docker braucht ein selbst gebautes `llama-server`, denn für Linux gibt es keine offiziellen
  CUDA-Builds.
- `task lint:sh` (shellcheck, gepinntes Image) ist Teil von `task ci`.
