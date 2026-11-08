# ADR 0005: Repo tooling layout (uv workspace + separate envs, cargo, Go module)

- Status: accepted (Phase 1, 2026-11-06).
- Context: contestant stacks have conflicting dependencies (Polars 2.1 vs. 1.44 for cudf-polars; torch cu130 index; RAPIDS), but shared code (zjmark, datagen, reference, eval, analysis) should be one environment.
- Decision:
  - **Python:** one root **uv workspace** (`pyproject.toml`, `uv.lock`) for the shared packages + dev tools (ruff, pyright, pytest). Contestant envs `contestants/python/{df,gpu_df,embed,llm-clients}` are **separate uv projects** with their own `uv.lock`, excluded from the workspace and depending on `zjmark` via a path source. Interpreter: uv-managed CPython 3.12.14 (not the system or miniforge Python).
  - **Pyright:** strict everywhere. Each env has its own `pyrightconfig.json` pointing at its `.venv` (passing env paths to the root config checked 0 files because of the root `exclude`, which was found and fixed in Phase 1). Only `gpu_df` and `embed`, which wrap stub-less native libs (cudf, onnxruntime), downgrade the unknown-type rules to warnings.
  - **Rust:** toolchain pinned in `rust-toolchain.toml` (1.99.1). `libs/rust/zjmark` is a standalone crate; contestants live in the `contestants/rust` workspace (release profile: fat LTO, 1 codegen unit, panic=abort). clippy pedantic + `-D warnings`.
  - **Go:** module `zeilenjoule/harness` (no GitHub path, rule 1), Go 1.27.3, golangci-lint v2 + govulncheck + `-race`. YAML via `go.yaml.in/yaml/v3` with `KnownFields(true)`.
  - **Track X** = harness self-test contestants (`x00_smoke_py`, `x01_smoke_rs`). They follow the CLI contract but never enter the rankings. `contestants.yaml` gains an optional `build` command, run before any measured window.
  - **Stage-marker format** is canonical JSON (sorted keys, no ASCII/HTML escaping), byte-identical in all three languages, enforced by the shared golden file `spec/zjmark_golden.txt`.
- Consequences: `task setup` installs everything from lockfiles; `task ci` lints and tests all three languages. Go-installed tools are resolved via `go env GOPATH`, because go-task's `env:` cannot override PATH.
