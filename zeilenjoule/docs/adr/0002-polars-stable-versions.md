# ADR 0002: Polars — latest stable releases only

- Status: accepted (user, Gate 1, 2026-11-06: "polars just stable version").
- Context: the latest stable Polars is **2.1.0**. The latest stable RAPIDS GPU engine (`cudf-polars-cu13 26.10.2`) requires **polars 1.44.2**. A version-delta contestant was proposed and rejected.
- Decision:
  - Only stable releases (no RC/nightly), pinned exactly in the uv lockfiles.
  - CPU Polars contestants (a08–a11, Python) use **polars 2.1.0** in the `df` env.
  - The GPU contestant a12 uses **polars 1.44.2** in the separate `gpu_df` env, because that's what stable cudf-polars supports. There's no extra delta contestant.
  - The Rust `polars` crate (a15) uses its own latest stable release.
- Consequences:
  - The a12-vs-a09/a10 comparison mixes two Polars major versions. The report states this explicitly next to every such comparison, and each run records the actual versions (rule 9).
  - When a stable cudf-polars supporting Polars 2.x is released, a12 moves to it and is re-run. Old a12 results stay, labelled with their version.
