# ADR 0026: Lastkurve des Produktions-Stacks mit offener Last (e07_stack_load)

**Status:** angenommen (2026-11-13)

## Kontext
PLAN §12.3 verlangt SLOs (Suche p95 < 100 ms bei 20 req/s, TTFT p95 < 1,5 s, Ingest ≥ 0,8 × Gewinner) und das
„Knie“ der Latenzkurve mit J/Anfrage. Regel 3: Zahlen nur aus `results/`. vegeta/k6 würden ein weiteres Werkzeug
neben das Harness stellen und die Energie nur über den Exporter liefern.

## Entscheidung
- **Eigener Go-Lastgenerator `services/cmd/zj-load` als Harness-Teilnehmer `e07_stack_load`** (Task `e.load`,
  Prüfer `builtin:e.load`). Stack-Start und Ingest wie e06 (gemeinsames Paket `internal/benchstack`), danach
  Stufen `/v1/search` 5…160 req/s (je 60 s) und `/v1/ask` 0,5…8 req/s (je 90 s). **Jede Rate ist eine
  ZJMARK-Stufe**: Zeit und GPU/CPU-Energie misst das Harness wie bei allen Teilnehmern; J/Anfrage = Stufenenergie /
  angebotene Anfragen (brutto und minus Leerlauf).
- **Offene Last:** Anfrage i ist um t0 + i/r fällig, Latenz zählt ab Fälligkeit (kein *coordinated omission*).
  Über `--max-in-flight` (2048) hinaus zählt eine Anfrage als „overload“. Zeitüberschreitungen und 5xx sind
  Messergebnis; falsch geformte 2xx-Antworten oder 4xx sind Defekte → Prüfung schlägt fehl (fail-closed).
- **TTFT → „erstes Ereignis“:** `/v1/ask` streamt die Router-Entscheidung als erstes SSE-Ereignis und dann die
  ganze Antwort (die Antwort-LLM-Aufrufe sind JSON-beschränkt, kein Token-Strom). Gemessen wird die Zeit bis zum
  ersten Ereignis; das ist die erste Ausgabe, die ein Client sieht.
- **Knie** = höchste Rate mit ≤ 1 % Fehlern und p95 ≤ 2 × p95 der niedrigsten Rate (Auswertung in
  `analysis/…/load_curve.py`, nicht im Generator).

## Befund beim Funktionstest (ungemessen, Skala M) und Korrektur
- `/v1/search` brauchte ~520 ms schon bei 1 req/s. Ursache: pgx cached Prepared Statements, Postgres wechselt nach
  fünf Ausführungen zum *generic plan*, der für die ODER-tsquery ~500 Treffer schätzt (real ~40 % der Tabelle) und
  einen einfädigen Bitmap-Heap-Scan mit `ts_rank` wählt (382 ms statt 120 ms). Korrektur: `SET LOCAL
  plan_cache_mode = force_custom_plan` in der Such-Transaktion – gleiche SQL, gleiche Ordnung, identische Treffer.
  Danach p50 ~160 ms bei 1 req/s, p95 144 ms bei 20 req/s auf M.
- **Offen (Entscheidung des Nutzers):** Die lexikalische Stufe bewertet *alle* Treffer der ODER-Abfrage; ihre
  CPU-Kosten wachsen linear mit der Tabelle (L ≈ 10 × M). Das 100-ms-Ziel auf L ist damit voraussichtlich nicht
  erreichbar, ohne die Rangfolge zu ändern (z. B. UND-zuerst, RUM-Index, Kandidatenlimit) – das würde die Qualität
  und die Vergleichbarkeit mit e03 berühren. Die gemessene Kurve entscheidet.

## Konsequenzen
- e06 wurde mit dem Gateway *vor* der Plan-Korrektur gemessen; e07 misst das korrigierte Gateway. Die Fragen-Stufe
  von e06 (64 parallele Fragen, Suche nur ein Teil davon) ist davon kaum betroffen; nicht neu gemessen.
- Ein gemessener L-Lauf dauert ≈ 45 min (Stack-Start + Ingest ≈ 28 min + Stufen ≈ 15 min).

## Ergebnis (gemessen, Skala L, 3 Läufe, Sitzung 356d44ab; Zahlen aus `results/`, Tabelle im Bericht)
- Alle 3 Läufe `ok` und geprüft. Knie `/v1/search` bei 5 req/s (p95 563 ms), Sättigung ≈ 11 ok/s; `/v1/ask` stabil
  bis 2 req/s (erstes Ereignis p95 3,3 s), Sättigung ≈ 4,5 ok/s.
- SLOs: Suche ✗ (p95 72 s bei 20 req/s, Rückstau), erstes Ereignis ✗ (p95 ≥ 2,0 s bei jeder Rate – das erste
  Ereignis ist die Router-LLM-Antwort), Ingest ✓ (628 gegen 460 Zeilen/s).
- Deutung: Mit 8 Leser-Verbindungen und ≈ 11 req/s Sättigung auf 16 Kernen kostet eine Suche ≈ 1,4 Kern-s
  (abgeleitet, nicht einzeln gemessen) – die lexikalische Stufe über ≈ 40 % von 1 M Zeilen. Größerer Pool hilft
  nicht; nur eine andere lexikalische Rangfolge. Für die erste-Ereignis-SLO müsste die Router-Entscheidung schneller
  werden (kleineres Router-Modell/Regeln) – ein früheres „Platzhalter“-Ereignis wäre Schönfärberei und ist
  ausgeschlossen. Beides ist eine Entscheidung des Nutzers (Qualität/Vergleichbarkeit mit e03).
