# Methodik / Methodology

*Stand Phase 2 (2026-11-06). Beschreibt, was der Harness `zj` (harness/) tatsächlich tut. Ergänzt PLAN §5.*
*English summary at the end.*

## 1. Was gemessen wird

| Größe | Quelle | Umsetzung |
|---|---|---|
| Wall-Zeit | monotone Uhr des Harness | Prozessstart bis -ende (Fork bis `wait4`), **ohne** Build, Server-Start, Lastwächter, Abkühlung und Verifikation |
| GPU-Energie | NVML `GetPowerUsage`, 20 Hz | **Primärquelle (ADR 0006):** Trapezintegral der Leistungswerte im Fenster (auf den Messbereich begrenzt) |
| GPU-Energiezähler | NVML `nvmlDeviceGetTotalEnergyConsumption` (mJ) | nur **Gegenprobe** (`energy_check.deviation_pct`): Auf dieser Karte zählt er unter Last ~19 % zu wenig (146 W statt 180 W bei Leistungslimit) |
| CPU-Energie | RAPL powercap `energy_uj` (`package-0` → `cpu_pkg`, `core` → `cpu_core`), 20 Hz | Zähler entfaltet (Überlauf bei `max_energy_range_uj`, hier ≈ 262 kJ ≈ 29 min bei 150 W), mehrere Pakete summiert |
| DRAM-Energie | RAPL `dram` | auf diesem i5-14400F **nicht vorhanden** → `null` |
| Steckdose | Smart-Plug (optional) | **nicht gemessen** (kein Messgerät, Checkliste C11) → `null` |
| CPU-Zeit | `wait4` rusage (user/sys, inkl. abgewarteter Kindprozesse) | |
| Peak-RSS | Summe VmRSS des Prozessbaums, 5 Hz über `/proc/<pid>/task/*/children` | Obergrenze (geteilte Seiten mehrfach gezählt); zusätzlich `max_rss_single_mb` aus rusage |
| Peak-VRAM | NVML Gerätespeicher, Peak im Fenster minus Belegung vor dem Start | gerätebezogen, nicht prozessbezogen (Container-PIDs sind nicht zuordenbar) |
| Temperatur, GPU-Auslastung | NVML | Peak bzw. Mittel im Fenster |

**Nicht gemessene Größen sind `null`, nie `0`** (Regel 3). Berichte schreiben dafür „nicht gemessen / not measured".

## 2. Brutto, Netto, Gesamt

- `energy_j` (**E_brutto**) = gemessene Energie im Fenster je Domäne.
- `idle_w` = Leerlaufleistung je Domäne aus `zj idle` (60 s, nichts gestartet). Gemessen zu Sessionbeginn und spätestens alle 30 min, gespeichert in `results/idle.jsonl`.
- Eine Leerlaufmessung zählt nur, wenn die Maschine **während** der Messung ruhig blieb (CPU-Auslastung über das ganze Fenster ≤ 5 %, gespeichert als `cpu_busy_pct`). Unruhige Messungen werden mit `quiet: false` gespeichert, aber nicht verwendet; nach 3 Fehlversuchen bricht die Session ab (fortsetzbar). Grund: In der ersten Smoke-Session (Phase 2) lief während der Leerlaufmessung fremde Last, P_idle war zu hoch und E_netto wurde negativ.
- `energy_net_j` (**E_netto**) = E_brutto − P_idle × t, je Domäne.
- **Reine CPU-Wettbewerber** (`needs_gpu: false`) bekommen im Bericht **keinen GPU-Nettoanteil** (0 statt E_GPU − P_idle,GPU × t): Die GPU-Leerlaufleistung driftet zwischen Baselines um ≈ 1 W (P-State), was bei langen CPU-Läufen negative Nettowerte ergab (ADR 0014). Die Rohdaten in `results/` bleiben unverändert.
- Gesamtenergie für Kennzahlen (`j_per_row`, `rows_per_kj`) = **GPU + CPU-Paket + DRAM** (Kern ist Teil des Pakets und wird nicht addiert).
- Wichtig: Auch reine CPU-Wettbewerber zahlen im Brutto die Leerlaufleistung der GPU. Das ist realer Aufwand; E_netto blendet ihn aus. Berichte zeigen beides.

Energie wird **systemweit** im Zeitfenster gemessen, nicht pro Prozess. Deshalb läuft immer nur ein Wettbewerber, und der Lastwächter wartet vor jedem Lauf auf eine ruhige CPU (≤ 5 % Auslastung über 2 s, ADR 0007).

## 3. Stufen (ZJMARK)

Wettbewerber schreiben `ZJMARK {"ev":"start","stage":"…"}`/`…"end"…` auf stderr (Format: `spec/zjmark.md`, byte-identisch in Python/Rust/Go).
Der Harness stempelt jede Zeile **beim Empfang** mit seiner monotonen Sampler-Uhr, dadurch gibt es keine Uhrenprobleme zwischen Sprachen.
Stufenenergie = Zählerdifferenz zwischen den Zeitstempeln (linear interpoliert). Stufen dürfen verschachtelt sein, aber sich nicht überlappen. Fehlende oder überlappende Marker machen den Lauf ungültig (`fail`, Protokollfehler).

## 4. Ablauf eines Laufs

1. Lastwächter (ADR 0007): warten, bis die CPU-Auslastung aller Kerne aus `/proc/stat` über `window_s` = 2 s ≤ `max_busy_pct` = 5 % ist (höchstens `max_wait_s`, sonst Session-Abbruch, fortsetzbar). `loadavg1` wird nur noch informativ gespeichert: Als gleitender 1-Minuten-Mittelwert hinkte er CPU-lastigen Läufen ~3,5 min hinterher.
2. Abkühlung: warten, bis GPU < `cooldown.gpu_temp_c` oder `max_wait_s` vergangen ist; Starttemperatur wird gespeichert.
3. Sampler starten, `settle_s` (0,5 s) warten, Prozess in **eigener Prozessgruppe** starten.
4. Ende: regulär, Timeout (`timeout_s`, ganze Gruppe wird mit SIGKILL beendet → `dnf`) oder Benutzerabbruch (kein Datensatz → wird beim Fortsetzen wiederholt).
5. Übrig gebliebene Prozesse der Gruppe werden beendet und im Datensatz vermerkt.
6. `settle_s` nachlaufen lassen, Sampler stoppen, integrieren.
7. Außerhalb des Messfensters: Status bestimmen, **verifizieren**, Datensatz an `results/runs.jsonl` anhängen (fsync).

## 5. Status

| Status | Bedeutung |
|---|---|
| `ok` | Exit 0, Protokoll korrekt, **Verifikation bestanden** |
| `disqualified` | lief, aber Ausgabe falsch (Regel 4); wird gespeichert, nicht versteckt |
| `fail` | Exit ≠ 0, Startfehler, Signal, Protokollfehler (Marker, kein/mehrfaches ZJRESULT) |
| `dnf` | Timeout |
| `oom` | SIGKILL, den der Harness nicht gesendet hat (in der Regel der OOM-Killer); Peak-RSS bleibt erhalten |

Die Verifikation ist **fail-closed**: Eine Aufgabe ohne funktionierenden Prüfer kann kein `ok` erzeugen.
In Rankings zählen nur `ok`-Läufe, keine Aufwärmläufe und nicht Track X (View `ok_runs` in `results/zj.duckdb`).

## 6. Wiederholungen und Reihenfolge

- Standard: 1 Aufwärmlauf + 5 Messläufe (`bench.yaml`), Auswertung über **Median und IQR**; Variationskoeffizient > 5 % wird markiert.
- **Verschränkt (round-robin)**: Jede Runde ist eine neue, mit `seed` reproduzierbare Permutation der Wettbewerber. So verteilt sich thermische Drift gleichmäßig. Skalen laufen aufsteigend.
- Sessions sind fortsetzbar (`zj run --session <id>`): bereits gespeicherte Läufe der Session werden übersprungen.
- Ändert sich der CPU-Governor während einer Session, bricht sie ab (ADR 0003).

## 7. Vertrauen in Energiewerte

`energy_confidence = low`, wenn das Fenster < 5 s ist (Telemetrie von Consumer-Karten ist gemittelt und niederfrequent; der Leistungswert ändert sich nur etwa 2-mal pro Sekunde) oder wenn Lesefehler auftraten. Die Abweichung Zähler ↔ Leistungsintegral ist eine bekannte systematische Eigenschaft dieser Karte (ADR 0006) und wird je Lauf gespeichert, aber nicht als Kriterium verwendet. **Systematische Unsicherheit:** Sollte doch der Zähler stimmen, wäre die GPU-Energie unter Last um ~23 % zu hoch; ohne Steckdosenmessung (C11) bleibt das offen. Skalen werden so gewählt, dass Läufe ≥ 10 s dauern.

## 8. Umgebungs-Fingerabdruck

Jeder Datensatz enthält Treiber, CUDA, Governor + EPP, GPU-Daten (Leistungsgrenze, Persistence, Takte), Host (OS, Kernel, CPU, SMT, Turbo, RAPL-Zonen), SHA-256 der Konfigurationsdateien und die Versionen des Wettbewerbers (`versions_cmd`), dazu einen SHA-256 über alles Stabile.

## 9. Kalibrierung

`zj calibrate` (≈ 7 min, ruhige Maschine nötig) prüft: Leerlauf 3 × 60 s (Stabilität, CV ≤ 5 %), CPU-Last (stress-ng, RAPL plausibel gegen PL1/PL2), GPU-Last (fp16-Matmul 60 s, Leistung 80–105 % der Grenze; NVML-Zähler vs. Integral nur informativ) und Harness-Overhead (`sleep 30`: Zeit + E_netto ≈ 0). Ergebnis: `docs/CALIBRATION.md` (generiert).

## 10. Bekannte Grenzen

- Energie ist systemweit. Hintergrundlast verfälscht, der Lastwächter mildert das nur.
- RAPL ist ein Modell-/Telemetriewert, keine Shunt-Messung.
- NVML-Leistung auf GeForce ist gemittelt; kurze Spitzen fehlen im Leistungsverlauf, der Energiezähler ist davon nicht betroffen.
- Peak-RSS als Summe überschätzt bei geteiltem Speicher (z. B. fork-basierte Worker).
- VRAM ist gerätebezogen: Desktop-Prozesse auf derselben GPU (Xorg, Chrome) verschieben die Grundlast, nicht aber das Delta.
- Ohne Steckdosenmessgerät fehlen Netzteilverluste, Lüfter, SSD und Mainboard.

---

## English summary

- **Load guard (ADR 0007):** before each run, CPU busy share of all CPUs over 2 s from `/proc/stat` must be ≤ 5 %; loadavg1 is recorded for information only (it lagged ~3.5 min behind CPU-heavy runs).
- **Time:** monotonic, process start to exit, excluding builds, server start-up, load guard, cooldown and verification.
- **GPU energy:** the trapezoid integral of 20 Hz NVML power samples is the primary source (ADR 0006). The NVML energy counter is recorded as a cross-check only: on this card it reads ~19 % low under load (146 W vs. 180 W at the power limit). If the counter were right instead, GPU energy under load would be overstated by ~23 %; this stays open without a wall meter.
- **CPU energy:** RAPL `energy_uj` (package, core), unwrapped and summed across packages. DRAM and wall power are not available here and recorded as `null`, never 0.
- **Gross vs. net:** E_net = E_gross − P_idle × t per domain, with P_idle from 60 s idle baselines at session start and every 30 min. An idle baseline is used only if the machine stayed quiet *during* it (CPU busy share over the whole window ≤ 5 %, ADR 0007). Others are stored with `quiet: false` but discarded; after 3 failed attempts the session aborts (resumable). The total for J/row is GPU + CPU package + DRAM. CPU-only contestants still pay for the idle GPU in E_gross. In E_net they get **no GPU share** (`needs_gpu: false` → 0): the idle GPU drifts by ≈ 1 W between baselines (P-state), which made E_net negative on long CPU-only runs (ADR 0014). Raw results stay unchanged.
- **Stages:** ZJMARK lines on stderr are timestamped on receipt by the harness. Stage energy is the counter delta between those timestamps.
- **Statuses:** `ok` (verified) / `disqualified` (wrong output, kept and shown) / `fail` / `dnf` (timeout, whole process group killed) / `oom` (SIGKILL not sent by the harness). Verification is fail-closed. Rankings use `ok_runs` only: no warm-ups, no track X.
- **Order:** 1 warm-up + 5 reps; median + IQR; CV > 5 % flagged. Seeded round-robin interleaving, sessions resumable, governor changes abort the session.
- **Confidence:** `low` for windows < 5 s or read errors. The counter deviation is recorded per run but is not a criterion (known systematic).
- **Fingerprint:** driver, CUDA, governor/EPP, GPU, host, config hashes, contestant versions, plus a SHA-256 over the stable parts.
- **Calibration:** `zj calibrate` → generated `docs/CALIBRATION.md`.
