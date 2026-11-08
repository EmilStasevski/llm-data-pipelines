# ADR 0014: Kein GPU-Nettoanteil für reine CPU-Wettbewerber / no GPU net energy for CPU-only runs

**Status:** angenommen (2026-11-08, vom Nutzer bestätigt)

## Kontext
E_netto = E_brutto − P_idle × t je Domäne (METHODIK §2). Die GPU-Leerlaufleistung schwankt zwischen
zwei ruhigen Baselines derselben Session um ≈ 1 W (Session `17a7791b…`: 5,58 W bzw. 4,53 W; P-State der
untätigen GPU). Bei langen reinen CPU-Läufen ergibt das einen negativen GPU-Nettoanteil (a08 M: ≈ −20 J je
Lauf, Gesamt-Netto −0,3 J). Das ist Rauschen der Baseline, keine Energie, die der Wettbewerber spart.

## Entscheidung
- In der Auswertung (nicht in `results/runs.jsonl`, die Rohdaten bleiben unverändert) bekommt jeder Lauf
  `uses_gpu` = `needs_gpu` des Wettbewerbers aus `config/contestants.yaml` (unbekannt → TRUE, d. h. der
  gemessene Wert bleibt).
- Für `uses_gpu = FALSE` ist der GPU-Anteil von E_netto **0**. E_brutto enthält die GPU weiterhin (die
  GPU läuft im Leerlauf mit und kostet real Strom).
- GPU-Wettbewerber behalten ihren gemessenen GPU-Nettoanteil.

## Alternativen
- Session-Median der GPU-Baseline: dämpft, beseitigt aber nicht das Vorzeichenproblem bei Drift innerhalb
  eines Laufs. Verworfen.
- VRAM-Spitze je Lauf als Kriterium: geräteweit gemessen, durch Fremdprozesse verrauscht (a04: 39 MB). Verworfen.

## Folgen
- CPU-only E_netto = CPU-Paket (+ DRAM) netto. Rangfolgen nach Brutto bleiben gleich.
- Später: der Harness sollte `needs_gpu` mit jedem Lauf speichern, damit die Zuordnung nicht von der
  aktuellen Konfiguration abhängt.
