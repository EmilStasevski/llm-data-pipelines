# Wie viel Energie kostet eine Zeile? pandas vs. Polars vs. Rust vs. GPU auf einer RTX 5060 Ti

*Entwurf – die Zahlen stehen im generierten Block „Kennzahlen“ und in den verlinkten Berichten; nichts davon ist von
Hand eingetippt.*

In der Industrie liegen die interessanten Daten selten in PDFs. Sie stecken in Tabellen: SAP-PM-Störmeldungen,
Q-Meldungen, Prüfprotokolle, exportiert als CSV in cp1252, mit Dezimalkomma, deutschen Datumsformaten und einem
Freitextfeld, das jemand um drei Uhr nachts an der Anlage getippt hat. Wer darauf „mit KI“ Fragen beantworten will,
braucht zeilenweise RAG: jede Meldung ist ein Dokument.

Ich wollte wissen, was das kostet. Nicht nur in Sekunden, sondern in **Joule**. Und ich wollte wissen, ob das teure
Werkzeug sich lohnt oder ob eine einzige PostgreSQL reicht.

## Der Aufbau

- **Ein Rechner:**
  - Ubuntu
  - RTX 5060 Ti mit 16 GB
  - GPU-Energie über NVML, CPU-Energie über RAPL
- **Ein Harness:**
  - Das Harness (`zj`, in Go) startet jeden Wettbewerber gleich.
  - Es misst jede Stufe über Marker im Programm, zieht die Leerlaufleistung ab und wiederholt die Läufe.
  - Es prüft jedes Ergebnis gegen eine langsame, offensichtlich richtige Referenz. **Ein schneller, aber falscher
    Lauf zählt nicht.**
- **Synthetische, aber realistische Daten:**
  - NRW-Werke, Anlagen, Equipment-Nummern, Schadensbilder
  - echte Tippfehler-Muster, Gold-Labels für Extraktion und Fragen
- **Fünf Tracks**, jede Stufe einzeln:
  - A Parsen/Bereinigen
  - B Embeddings
  - C Suche
  - D LLM-Extraktion
  - E die ganze Kette von der Roh-CSV bis zur Antwort auf 300 Fragen

## Kennzahlen

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

## Was ich daraus mitnehme

**1. Der größte Hebel ist das Werkzeug, nicht die GPU.**
Track A zeigt die Spanne von pandas bis zu Polars und handgeschriebenem Rust:
- Bis zur Skala L (1 Mio. Zeilen) hat keine GPU-Variante (cudf.pandas, Polars GPU) die besten CPU-Werkzeuge geschlagen,
  weder in Zeit noch in Energie. Die Bereinigung ist zu viel Zeichenkettenarbeit und zu wenig Rechnen.
- Überraschend: „vektorisiertes“ pandas war nicht schneller als eine schlichte Python-Schleife mit dem `csv`-Modul.
  Der Gewinn kommt von Polars' Ausdrücken und von Rust, nicht vom Wegräumen der Schleife allein.

Die Pareto-Front steht im Bericht.

**2. Beim Embedding entscheidet der Server, nicht das Modell.**
Gleiches Modell (bge-m3), sehr unterschiedliche Durchsätze: der Inferenz-Server TEI schlägt das PyTorch-Skript
(sentence-transformers, fp32) deutlich – in Joule je Dokument ebenso.

**3. Die einfachste Architektur hat gewonnen.**
Eine PostgreSQL mit pgvector (HNSW), deutschem Volltext und RRF-Fusion, dazu ein lokales Router-LLM, das zwischen
geprüftem SQL und hybrider Suche wählt. Kein separater Vektor-Store, keine Synchronisation. Als Go-Dienst betrieben kostet
das praktisch nichts zusätzlich (Kennzahlen: Produktions-Stack gegen Skript).

**4. Kaskaden sparen Energie, ohne Qualität zu verschenken.**
Regeln und ein kleines NER-Modell (GLiNER) lösen den Großteil der Extraktion; nur der Rest geht an das LLM. Die Kaskade
liegt beim macro-F1 knapp unter dem besten LLM-Setup und braucht deutlich weniger Joule je Zeile.

**5. Lasttests finden Fehler, die Benchmarks nicht sehen.**
Der erste Lasttest des Produktions-Stacks hat zwei SLOs verfehlt. Ursachen:
- Die Suche war langsam, weil eine harmlose Frage („Welche *Meldungen* …“) Wörter enthält, die in *jeder* Zeile
  stehen. Die Volltext-Stufe hat dann die ganze Tabelle bewertet.
- Das erste Ereignis kam erst, wenn das Router-LLM sein komplettes JSON fertig hatte.

Die Korrekturen:
- **Common-Terms-Abfrage** (Treffer nur über seltene Wörter, Rangfolge weiter über alle).
- **Router gestreamt** (die Route steht im ersten JSON-Feld).

Die Neumessung erfüllt alle drei SLOs, und die Suche hält jetzt ein Vielfaches der Last (Lastkurve in den
Kennzahlen). Beide Schritte sind in ADR 0026/0027 dokumentiert, samt der verfehlten ersten Messung. Ehrliche Zahlen schließen die
schlechten mit ein.

## Selbst nachmessen

```
task data:gen SCALE=M
task bench TRACK=A SCALE=M
task report
```

Für den Produktions-Stack: `task demo`, dann `http://127.0.0.1:8080/demo`.

Code, Methodik und alle Entscheidungen: Repository *ZeilenJoule* (`README.de.md`, `docs/METHODIK.md`, `docs/adr/`).
