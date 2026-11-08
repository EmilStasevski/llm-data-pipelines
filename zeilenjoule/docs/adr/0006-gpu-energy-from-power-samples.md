# ADR 0006: GPU energy from integrated power samples, not the NVML energy counter

- Status: accepted (Phase 2, 2026-11-06). Deviates from PLAN §5.1 ("counter primary").
- Context: the first `zj calibrate` (session `0230bf33…`, `results/calibration/0230bf33-0186-4913-ace3-c241032e1f40.json`) failed one check: the NVML energy counter and the integrated power samples differed by +22.2 % over the GPU burn. Diagnostic `docs/env/probes/probe_nvml_load.py` (output `docs/env/probe_nvml_load.out`, 20 s fp16 GEMM, steady middle 50 %):
  - `nvmlDeviceGetPowerUsage` 180.0 W, field `POWER_INSTANT` 180.1 W, field `POWER_AVERAGE` 180.0 W
  - energy counter rate 146.3 W (≈ 81 % of the power readings)
  - enforced power limit 180 W
  - At idle both agreed within 2 % (Phase 0: 6.02 W counter vs 6.12 W sampled).
- Reasoning: three independent power fields agree with each other and sit exactly at the enforced limit, the value the driver's power limiter regulates against. A saturating GEMM on a 180 W card is expected to be power-limited. Only the counter disagrees, and only under load. A wall meter (checklist C11) would settle it definitively; none is available.
- Decision:
  - GPU energy (`energy_j.gpu`, stage `gpu_j`, idle `power_w.gpu`) = **trapezoid integral of the 20 Hz `GetPowerUsage` samples**, clamped to the sampled range. Baseline and runs use the same source, so E_net stays consistent.
  - The counter is still recorded on every run (`energy_check.gpu_counter_j`, `deviation_pct` = counter vs. primary).
  - `energy_confidence` no longer uses the deviation: it is a known systematic and would mark every GPU-heavy run "low".
  - Calibration: the counter comparison becomes an **info** line. A new graded check requires the steady burn power to be within 80–105 % of the enforced limit.
- Consequences:
  - The power readings update at only ~1.7–2 Hz (driver-averaged), so GPU energy of stages shorter than a few seconds is coarse (already flagged low confidence below 5 s).
  - If the power readings are the wrong source instead, GPU energy is overstated by ~23 % under load. Reports must state this uncertainty until a wall-meter comparison exists.
  - Revisit on a driver change (fingerprinted per run) or when a wall meter is available.
- Addendum (same day, calibrations 1 + 2, `results/runs.jsonl`): the counter error is **not a simple scale factor**. During the idle-like `sleep 30` run of calibration 1 the counter advanced at 6.0 W while the power samples integrated to 5.2 W (counter +16 %); under the burn the counter was 19–26 % *below* the samples. Correcting the counter by a fixed factor is therefore not an option.
