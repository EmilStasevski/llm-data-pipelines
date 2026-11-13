# ADR 0027: Common-Terms-Volltext und gestreamter Router (SLO-Korrektur nach e07)

**Status:** angenommen (2026-11-13) · ersetzt den offenen Punkt „lexikalische Rangfolge“ aus ADR 0026

## Kontext
Die erste gemessene Lastkurve (`e07_stack_load`, L, ADR 0026) hat zwei SLOs verfehlt (Zahlen im Bericht).

**Suche:**
- `doc_text` enthält Feldbezeichnungen (`Werk`, `Anlage`, `Meldung`, `Datum` …), die in jeder Zeile stehen. Eine
  Frage wie „Welche *Meldungen* …“ ergibt deshalb mit der ODER-tsquery praktisch die ganze Tabelle als Kandidaten.
- `ts_rank` lief dann über ≈ 1 Mio. Zeilen, je Anfrage hunderte Millisekunden CPU. Die Kosten wachsen linear mit
  der Tabelle.

**Erstes Ereignis:** Das SSE-Ereignis `route` wurde erst gesendet, wenn das Router-LLM sein *ganzes* JSON erzeugt
hatte, samt SQL bzw. Suchtext und Filtern.

Der Nutzer hat entschieden, beide SLOs zu korrigieren und neu zu messen.

## Entscheidung
**1. Common-Terms-Abfrage für die lexikalische Stufe** (nach dem Vorbild von Lucenes/Elasticsearchs
`cutoff_frequency`):
- *Treffer-Bedingung:* nur die **seltenen** Lexeme der Frage, die in den Daten vorkommen, verknüpft mit ODER.
  Selten heißt Dokumentfrequenz ≤ 1 % der Zeilen.
- Gibt es kein seltenes Lexem, hat die lexikalische Stufe **keine Kandidaten** und die dichte Stufe antwortet allein.
  `ts_rank` kennt keine IDF; eine Rangfolge nur über Allerweltswörter trägt kaum Signal und kostet bis zu einem Scan
  der ganzen Tabelle.
- *Rangfolge:* unverändert `ts_rank` über die ganze ODER-Abfrage. Eine Zeile mit seltenen Treffern gewinnt also
  weiter durch die häufigen Wörter.
- Die Kandidaten sind auf ≈ 1 % der Zeilen je seltenem Lexem begrenzt.
- Verworfene Varianten (Entwicklungsprüfung):
  - Rückfall auf „alle Lexeme mit UND“: Fragen nur aus häufigen Wörtern trafen weiter Zehntausende Zeilen und
    waren die langsamsten.
  - Unbekannte Wörter als „selten“ zählen: Das ergab zufällig leere Kandidatenmengen.

Die Dokumentfrequenzen liegen in der Materialized View `lex_df` (Migration 0002):
- `ts_stat` über `docs` plus exakte Zeilenzahl (deterministisch, keine ANALYZE-Stichprobe)
- `REFRESH … CONCURRENTLY` nach jedem Ingest-Job (Metrik `zj_ingest_stage_seconds{stage="lex_df"}`)
- schema-qualifiziert, weil REFRESH seit PostgreSQL 17 mit eingeschränktem `search_path` läuft

Die Grenze 1 % ist ein fester, begründeter Wert (Größenordnung der ES-Voreinstellungen) und nicht auf die Fragen
optimiert: Werks- und Städtenamen (≈ 2,5 % je Wert) gelten damit als häufig, Equipment-Nummern, Daten und Fachwörter
als selten.

**2. Router-Aufruf gestreamt:**
- `llm.Client.JSONStream` nutzt dieselbe Anfrage mit `stream: true`, also dieselben Tokens, Temperatur 0 und
  dasselbe Schema.
- `route` ist die erste Eigenschaft des Router-Schemas. Sobald das generierte Präfix `{"route": "sql"|"search"`
  enthält, geht das Ereignis `route` hinaus.
- Danach folgen `plan` (SQL bzw. Suchtext, neu), `answer` und `done`.
- Ein Platzhalter-Ereignis vor der Entscheidung wäre Schönfärberei und ist ausgeschlossen. Gemessen wird weiter die
  Zeit bis zum ersten Ereignis, und das ist jetzt die echte Routing-Entscheidung.
- JSON-Antworten (`/v1/ask` ohne SSE, e06) laufen unverändert ohne Streaming.

## Prüfung (Entwicklung, ungemessen; Logs in `logs/p9_fix/`)
- **Qualität:** alle 300 Track-E-Fragen gegen einen L-Entwicklungs-Stack, alter und neuer Gateway. Gesamtqualität und
  Teilwerte (filtered_semantic, Router) unverändert im Rahmen der Lauf-zu-Lauf-Schwankung.
- **Latenz:** Suche bei 20 req/s und erstes Ereignis bei 0,5 req/s deutlich unter den SLO-Grenzen.
- Der Integrationstest prüft die Treffer-Bedingung (seltene, vorkommende Lexeme mit ODER, sonst keine Kandidaten).
- Ein erster Entwurf band die Grenze als `bigint`; der Integrationstest hat das gefunden (Cast `::float8`).

Maßgeblich sind die Neumessungen `e06_stack_go` (Qualität, Zeit/Energie gegen `e03`) und `e07_stack_load`
(Lastkurve), beide vom Nutzer mit `scripts/quiet_box.sh` gemessen.

## Konsequenzen
- **Rangfolge:** Die lexikalische Stufe weicht vom gemessenen Skript `e03_postgres_only` ab (andere Kandidatenmenge).
  e06 vergleicht deshalb jetzt einen leicht anderen Algorithmus mit e03; der Abschnitt in WINNER.md benennt die
  Abweichung.
- **Lastkurve:** Suchstufen bis 640 req/s (vorher 160), weil das Knie sonst nicht mehr im Messbereich läge.
- **API:**
  - neues SSE-Ereignis `plan`; `route` enthält nur noch die Route
  - Antworten der Route `search` tragen `cited_rows` (Demo-Seite)
- **Neu:** `GET /v1/energy` (Prometheus-Recording-Rule, sonst „nicht gemessen“) und die Demo-Seite `GET /demo`.

## Nachtrag: Neumessung (2026-11-13)
- `e06_stack_go` (Sitzung 842ffdfa) und `e07_stack_load` (Sitzung 535a4a88), L, je 3 Läufe, alle `ok` und geprüft.
- **Alle drei SLOs erfüllt.** Zahlen in der SLO-Tabelle des Berichts (`reports/REPORT.de.md`, Abschnitt Lastkurve).
- Das Such-Knie liegt jetzt innerhalb des erweiterten Messbereichs (unter 640 req/s); die Frage-Kapazität bleibt
  durch das LLM begrenzt (Knie unverändert).
- Qualität und Zeit/Energie von `e06` gegen `e03`: Abschnitt Phase 9 in `reports/WINNER.md`; Qualitäts-Gate ✓.
