# ADR 0025: Den Produktions-Stack wie einen Teilnehmer messen (e06_stack_go)

**Status:** angenommen (2026-11-13)

## Kontext
PLAN §12.3 verlangt Lasttests mit J/Anfrage; ADR 0023 verlangt, die ungemessene PostgreSQL-Router-Variante mit den
300 Track-E-Fragen nachzuprüfen. Regel 3: Zahlen nur aus `results/`, also aus dem Harness.

## Entscheidung
- **e06_stack_go ist ein normaler Track-E-Teilnehmer** (`e.rag`, Skala L, gleiche CSV-Bytes, gleiche 300 Fragen,
  gleicher Prüfer `zj_eval.track_e`). Der Go-Client `services/cmd/zj-e2e` startet den Compose-Stack (Profil `gpu`,
  eigenes Projekt `zeilenjoule-bench`, frische Volumes, Zufalls-Secrets), lädt die CSV über `POST /v1/ingest`, stellt
  die Fragen über `POST /v1/ask` (64 parallele Clients wie e03) und baut den Stack wieder ab. Stufen: `load`,
  `ingest`, `answer`, `write`. Das Harness misst Zeit und Energie der ganzen Maschine wie bei e01–e05.
- **Ratenbegrenzung im Messlauf aus** (`ZJ_RATE_LIMIT/BURST=1000`): sie ist Richtlinie pro Client, kein Teil der
  Pipeline; e01–e05 haben keine.
- **Erst-Import ohne Suchindizes:** Ist `docs` leer, verwirft der Worker HNSW und GIN vor dem Laden und baut sie
  danach einmal parallel (wie e03: COPY, dann CREATE INDEX), statt den HNSW-Graphen zeilenweise wachsen zu lassen.
  Die DDL steht einmal in Go (`db.SearchIndexes`); ein Integrationstest prüft, dass das Ergebnis byte-gleich zur
  Migration ist. Bricht ein Import ab, baut ein `defer` bzw. der nächste Worker-Start die Indizes wieder auf.
- **API-Korrekturen, die der Messlauf aufgedeckt hat:**
  - Unbrauchbare Router-Antworten hießen `route: "search"` – das hätte die Router-Genauigkeit geschönt.
    Jetzt `route: "none"`.
  - Tabellen kamen als JSON-Objekte, deren Spaltenreihenfolge Go alphabetisch sortiert. Jetzt `columns` plus Zeilen
    als Arrays.
  - Neues Feld `sql_status` (executed / rejected / failed), damit Clients und der Messlauf Schicht 1 und 2
    unterscheiden können.

## Konsequenzen
- e06 vergleicht „gleiche Pipeline, als Dienst betrieben“ mit e03 „gleiche Pipeline als Skript“: Unterschiede
  stammen aus HTTP, Upsert/Staging, River und dem gleichzeitigen Laden von TEI und vLLM (GPU-Teilung 0,70).
- Der Stack-Start (`load`) wird als eigene Stufe gemessen; Bauen der Images liegt im `build`-Schritt außerhalb.
- Lastkurven (Latenz gegen Parallelität) sind ein späterer, eigener Lauf; e06 misst den Track-E-Arbeitspunkt.
