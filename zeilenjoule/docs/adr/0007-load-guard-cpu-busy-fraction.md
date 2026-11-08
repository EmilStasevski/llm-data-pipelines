# ADR 0007: Load guard = CPU busy fraction from /proc/stat, not loadavg1

- Status: accepted (user, 2026-11-07). Deviates from PLAN §5.2 ("waits if loadavg1 > 1.0").
- Context: loadavg1 is an exponentially decaying 1-minute average. In all three calibrations the guard waited **~3.4–3.6 min** after the 60 s stress-ng run (loadavg1 9.5–10.8 when the GPU run was due), although the CPU was idle again within a second. With Track A's CPU-heavy matrix (many contestants × 6 runs × several scales) this adds hours of pure waiting. loadavg1 also counts tasks in uninterruptible I/O sleep, not CPU use.
- Decision:
  - Before each run (and each idle baseline) the harness measures the **busy share of all CPUs over `window_s` = 2 s** from `/proc/stat` (busy = user+nice+system+irq+softirq+steal; idle and iowait are not busy; guest is already contained in user). It waits until busy ≤ **`max_busy_pct` = 5 %** (≈ 0.8 of 16 threads; loadavg1 1.0 ≈ 6.25 %). Unreadable /proc/stat or a window without ticks counts as not quiet (fail-safe).
  - An idle baseline is "quiet" if the busy share over its **entire** window is ≤ 5 % (instead of loadavg1 at its end), stored as `cpu_busy_pct` in `results/idle.jsonl`.
  - Each run records `env.cpu_busy_pct_start` (the releasing guard measurement); `loadavg1_start` stays as information.
  - `zj doctor` shows `cpu_busy` over 1 s (loadavg1 for information).
- Evidence: right after `stress-ng --cpu 16 --timeout 15s`, loadavg1 was 4.11. The new guard released after one 2 s window (busy 4.4 %); `zj doctor` at the same time as the old guard would have blocked: 1.0 % busy at loadavg1 1.18.
- Consequences: the 5 % threshold is a judgement call. It must be revisited if idle baselines become unstable (calibration CV currently ≤ 1.7 %). Light background activity under 5 % (e.g. desktop) is still possible; the per-run idle baseline and the median over reps absorb it.
