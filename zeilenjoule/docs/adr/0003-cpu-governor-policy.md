# ADR 0003: CPU governor policy for measurements

- Status: accepted (user, Gate 1, 2026-11-06).
- Context: intel_pstate is active with governor `powersave` and EPP `power` (the Ubuntu default). The governor changes both time and energy, especially on the 6P+4E i5-14400F.
- Decision: the **primary** matrix runs on the default `powersave`/EPP `power` (realistic, untouched machine). A **secondary** comparison runs the top contestants per track (≤ 5) on `performance`.
- Consequences:
  - Switching the governor needs sudo (`cpupower frequency-set -g …`). The user runs it at the start/end of the comparison session. Claude never changes it.
  - The harness records governor + EPP in every run's env fingerprint and refuses to mix governors inside one session.
  - The report shows the energy-vs-time trade-off of `performance` as its own section.
