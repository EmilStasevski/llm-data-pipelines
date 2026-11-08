# ADR 0016: Track C als ein Lauf `c.dense` (Laden + Index + Anfragen), Recall-Gate je Filterklasse

**Status:** angenommen (2026-11-08)

## Kontext
PLAN §9 nennt `c.load`, `c.index`, `c.query` als getrennte Tasks. Der Harness misst einen Prozess pro Lauf.
Getrennte Läufe würden den Speicher zwischen den Läufen am Leben halten müssen (Server, Dateien) und damit
den „gleichen Maschinenzustand“ (Regel 5) verletzen. Fair-Play verlangt Vergleich bei gleichem Recall.

## Entscheidung
- **Ein Lauf = leerer Speicher → `ingest` → `index` → `query` (1 000 Anfragen, Konkurrenz 1) → `write`.**
  Die Stufen-Marker liefern Zeit und Energie je PLAN-Task; der Speicher wird danach gelöscht.
- **Eingaben vorab berechnet** (`task trackC:inputs`): Dokument-Vektoren = Track-B-Referenz (b01, fp32) als
  fp16 („gleiche Vektoren für alle, halbe Genauigkeit“), Anfrage-Vektoren mit demselben Codepfad. Die
  Einbettung kostet in Track B, nicht hier.
- **Wahrheit = numpy-fp32-Brute-Force** über die fp32-Vektoren (`zj_reference.track_c`), nicht c00: c00 ist
  ein normaler Wettbewerber (torch, GPU) und wird wie alle geprüft.
- **Gate: recall@10 ≥ 0,95 in jeder Filterklasse** (none/werk|ort/datum/kombiniert). Ein Gesamtwert würde
  schwache gefilterte Suche verstecken. fp16-Rundung allein kostet bis 0,012 (real, Klasse none, Beinahe-Gleichstände).
- **nDCG@10 / hit@10 werden berichtet, nicht gegated**; die exakte Suche ist die Obergrenze (M 0,848, real 0,999).
- **Track-B-Retrieval-Gate** nutzt dieselbe Maschinerie: Qualitätsmetrik mit den Kandidaten-Vektoren muss
  innerhalb 0,01 der Referenz liegen. PLAN sagt „recall@10 auf den qrels“; bei tausenden relevanten Zeilen
  je Anfrage ist recall@10 bedeutungslos, daher die Skalenmetrik.
- **Bibliotheken ohne Planer (FAISS, cuVS)** bekommen die Logik, die ein Experte schreibt: kleine gefilterte
  Mengen exakt, größere über den Index mit Filter-Bitmap. Datenbanken (pgvector, Qdrant, DuckDB, LanceDB)
  nutzen ihren eigenen Planer.
- **Parameter:** HNSW M=16, efC=200 (PLAN §9), ef_search 100 überall: mit 64 lagen FAISS, pgvector und DuckDB bei real/none nur bei 0,958–0,963 (ungemessen), zu knapp am Gate; mit 100 ≥ 0,97. LanceDB erreicht das Gate nur, wenn alle
  IVF-Partitionen abgefragt werden (ungemessene Proben: ¼ → 0,72–0,87, ½ → 0,944 bei real); CAGRA nutzt die
  cuVS-Standardwerte (kein M/efC). Qdrant mit GPU-Indexbau ist eine eigene Variante; der Lauf schlägt fehl,
  wenn nicht jeder Segment-Build laut Log auf der GPU lief (Regel 6).

## Konsequenzen
- Skalen `M` und `real` (`S` hat keine Anfragen); `L` für die Top-Kandidaten später.
- Konkurrenz 16 (QPS) und `c.hybrid` (BM25 + RRF) folgen als eigene Erweiterung.
- Bereits gemessene Track-B-Läufe wurden ohne Retrieval-Teil geprüft; der Bericht sagt das.
