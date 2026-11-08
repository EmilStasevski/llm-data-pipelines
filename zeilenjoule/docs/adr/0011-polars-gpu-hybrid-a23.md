# ADR 0011: Explicit CPU/GPU split for Polars (a23_polars_gpu_hybrid)

- Status: accepted (Phase 4, 2026-11-07). User request: "do it gpu cpu split".
- Context: a12 (a10's plan on `GPUEngine(raise_on_fail=True)`) fails: NFC normalisation, `extract_many`, `list.eval` and four other expressions have no GPU implementation in cudf-polars 26.10 (ADR 0009). The question "what does the GPU buy on Track A if you split the plan by hand?" remains. The working name `a12b` breaks the ID convention (`^[a-ex][0-9]{2}_…`, harness config validation), so the contestant is **a23**.
- Decision: three explicit stages, each a separate `collect()`:
  1. CPU (Polars streaming engine): scan, §1 normalisation of all 11 columns, §6 abbreviation expansion.
  2. GPU (`raise_on_fail=True`, no silent fallback): §2–5 fields, lower/fold, Materialnummer, safety triggers, Werk lookups, doc_text.
  3. CPU: §7 longest Komponente (`extract_many` → explode → group_by) + join + sort.
- GPU-supported rewrites (`GpuRules`, subclass of the shared `PolarsRules`): one `str.extract` per group instead of `extract_groups` (crashes on the GPU), `to_date(cache=False)`, literal `replace_many` instead of a regex `replace_all`, `(?i)` replaced by explicit case classes. The classes reproduce Python's `re.IGNORECASE` (the reference) including `ſ`, `İ` and `ı`; a test checks the table against all of Unicode. `zfill` became `when(len == 1)` because it raises on non-ASCII columns.
- Verification: golden cases on the CPU and the GPU engine (`contestants/python/gpu_df/tests/test_a23.py`, 195 tests), plus `verify_track_a` passing at S/M/L/XL (unmeasured run).
- Consequences:
  - Every stage boundary is a host↔device copy. At L, a23 took about 6 s in an unmeasured run, against 3.55 s measured for a10 (session `4c021dc7…`), so the GPU is not expected to win Track A. The measured run will show whether it saves energy.
  - Engine label `gpu+cpu hybrid (explicit split…)`. This is not a fallback, so the report does not mark it as a CPU fallback (rule 6 is satisfied by `raise_on_fail=True` in the GPU stage).
  - Runs at S/M/L/XL (XL unmeasured: 40 s, 16 GB RSS).
