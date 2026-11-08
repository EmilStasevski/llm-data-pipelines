# CLAUDE.md — ZeilenJoule

> **ZeilenJoule** — *„Wie viele Joule kostet eine Zeile?"* / *"How many joules does a row cost?"*
>
> This project benchmarks **row-wise RAG on German (NRW) industrial data**. Each tool gets the same task, from naive pandas to Polars, GPU dataframes, pure Rust, SQL/vector databases and local LLMs, and is measured for **wall time and energy (joules)** on one machine: Ubuntu + NVIDIA GeForce RTX 5060 Ti 16 GB.
> The fastest stack that passes the quality gates then gets a **full Go-based DevOps / production setup**.
> Secondary goal: a portfolio piece for a **Werkstudent** (working student) position in NRW industry, covering data engineering, databases, RAG, Rust, Go, DevOps and energy awareness.

## Read at the start of every session (in this order)
1. `CLAUDE.md` (this file): rules and conventions
2. `docs/PLAN.md`: the full specification. It is the source of truth for phases, tasks, contestants and methodology.
3. `docs/PROGRESS.md`: the running session log. Create it if it is missing. Append an entry after every work block: date, what was done, what's next, open questions.

If PLAN.md and reality disagree (versions, APIs, flags), **trust reality**. Record the difference in `docs/ENVIRONMENT.md` or `docs/COMPAT.md` and continue.

## Hard rules
1. **No git commits, no pushes, no GitHub.** Never run `git commit`, `git push`, `gh`, and don't create `.github/`. The user will handle version control later. If a `.git` directory exists, leave it alone.
2. **Ask the user before** any of these:
   - any `sudo` command
   - installing or upgrading the NVIDIA driver, CUDA, or the kernel
   - changing GPU clocks, power limits or persistence mode
   - a single download larger than 20 GB
   - deleting anything in `data/`, `models/` or `results/`
   - anything that costs money
   Collect these into **one checklist** for the user. Don't ask one at a time.
3. **Never fabricate numbers.** Every figure in docs and reports must come from `results/`, written by the harness. If something wasn't measured, write „nicht gemessen / not measured".
4. **Correctness before speed.** A run counts only if `zj verify` passes against the reference output. A run that is fast but wrong is **disqualified**. Record the disqualification; don't hide it.
5. **Fairness.** Every contestant gets the same input bytes, the same output spec and the same machine state. Write each contestant the way an expert in that tool would. The only exceptions are the explicitly labelled anti-pattern baselines (`*_iterrows`, `*_map_elements`, …), which exist to show what the naive approach costs.
6. **No silent fallbacks.** GPU engines (Polars GPU, cudf.pandas, ONNX Runtime EPs) must either fail loudly or log their CPU-fallback statistics. Any run that fell back to CPU gets labelled as such.
7. **Measurement hygiene.** Don't build, download, run tests or do any other heavy work while a measured benchmark is running. Start long runs in the background (tmux session `zj` or `nohup … > logs/<name>.log 2>&1 &`), then poll the log instead of blocking.
8. **Licences and data etiquette.**
   - Use only models and data whose licence allows this use. Record the licence in the model or dataset card.
   - External APIs: at most 1 request/s, generic User-Agent `zeilenjoule-research/0.1`, cache raw responses under `data/raw/`.
   - **Never collect personal data.**
9. **Versions are facts, not guesses.** Pin dependencies with lockfiles (`uv.lock`, `Cargo.lock`, `go.sum`, image digests), and the harness records the actual versions with every result.
10. Secrets go only in `.env`, which is gitignored. Keep `.env.example` up to date.

## Machine (verify in Phase 0, don't assume)
- Ubuntu LTS (24.04 or 26.04, detect which). CPU and RAM unknown: detect them.
- GPU: RTX 5060 Ti 16 GB GDDR7 (Blackwell GB206, **compute capability 12.0 / sm_120**, 180 W TGP, 448 GB/s, PCIe 5.0 x8).
- sm_120 needs builds made with CUDA ≥ 12.8. Many prebuilt wheels and images were flaky on sm_120 (vLLM attention backends, AWQ/GPTQ, TEI images), so Phase 0 runs a **compatibility probe** for each engine and writes `docs/COMPAT.md`.
- Plan for about 14.5 GB of usable VRAM, and run only **one measured GPU workload at a time**.

## Repo layout (create in Phase 1)
```
zeilenjoule/
├── CLAUDE.md  Taskfile.yml  .env.example  README.md  README.de.md
├── config/        bench.yaml  contestants.yaml  models.yaml
├── spec/          track specs, golden test vectors, JSON schemas, lexicon/*.yaml
├── harness/       Go module: `zj` CLI, energy sampler, verify, energy exporter
├── libs/          zjmark stage-marker helpers: python/, rust/ (Go lives in harness/pkg/zjmark)
├── datagen/       Python (uv): synthetic NRW data generator + real-data fetchers
├── reference/     slow, obviously-correct reference implementations (spec of truth)
├── contestants/   python/<env>/  rust/ (cargo workspace)  go/  docker/ (server compose files)
├── eval/          quality metrics (verify, recall, nDCG, F1, QA scoring)
├── analysis/      report + figure generation
├── services/      Phase 9: gateway/ (Go)  worker/ (Go)  core/ (Rust, if it wins)  proto/
├── deploy/        compose/  helm/zeilenjoule/  k3s/  monitoring/
├── data/ models/ logs/            (gitignored)
├── results/       runs.jsonl  zj.duckdb
├── reports/       REPORT.de.md  REPORT.md  figures/
└── docs/          PLAN.md  PROGRESS.md  ENVIRONMENT.md  COMPAT.md  METHODIK.md  adr/
```

## Toolchain
- **Python** 3.12 (3.13 is fine if every wheel exists) via **uv** (`uv run …`); ruff, pyright, pytest. Stacks whose dependencies conflict get **separate uv projects**: `df`, `gpu_df` (RAPIDS), `embed` (torch/onnx), `llm-clients`.
- **Rust** stable via rustup; `cargo clippy -- -D warnings`, `cargo test`, criterion for micro-benchmarks.
- **Go**, latest stable (≥ 1.26); `golangci-lint`, `govulncheck`, `go test -race`.
- **Taskfile** (go-task) as the single entry point. Docker + docker compose + NVIDIA Container Toolkit. Run database and inference servers in Docker with `--network host` during latency benchmarks to avoid docker-proxy overhead.

## Everyday commands (implement in Phase 1–2)
```
task doctor                       # environment + GPU + RAPL + versions check
task data:gen SCALE=S|M|L|XL      # synthetic data (seeded, deterministic)
task data:fetch                   # real NRW data (thru.de, TED, BA Jobsuche), cached
task bench TRACK=A SCALE=M        # run a track's matrix via the harness (resumable)
task bench:one ID=a09_polars_eager SCALE=M REPS=5
task verify ID=…                  # correctness check vs reference
task report                       # regenerate reports/ from results/zj.duckdb
task up / task down               # Phase 9 stack (docker compose)
task ci                           # local CI: lint + tests + builds + scans (no GitHub)
```

## Conventions
- Code, identifiers and code comments: **English**. Data, prompts and lexicons: **German**. User-facing docs: German first (`*.de.md`), English second.
- Contestant IDs: `<track letter><2 digits>_<tool>_<variant>`, e.g. `a09_polars_eager`, `b06_tei_bgem3`, `c03_pgvector_hnsw`, `d02_llamacpp_qwen35_9b_q4`.
- Every contestant follows the **CLI contract** (PLAN §5.3) and emits `ZJMARK` stage markers (PLAN §5.4).
- Regexes in shared specs must be **RE2-compatible** (no lookaround, no backreferences). Don't put `\b` next to umlauts; use the explicit boundary class from PLAN §7.2.
- Units are SI. Energy goes in joules in data and in J/Wh in reports. Decimal commas appear only in German prose, never in data files.
- Work in small steps. After each step, get the tests green, then update `docs/PROGRESS.md`.
- Every non-obvious decision gets an ADR (`docs/adr/NNNN-title.md`, 10–30 lines).

## Phase gates (otherwise work autonomously through the phases)
- **Gate 1**, end of Phase 0: show `docs/ENVIRONMENT.md`, `docs/COMPAT.md` and the sudo/permission checklist, then wait for the user.
- **Gate 2**, end of Phase 8: show the winner analysis (`reports/WINNER.md`) and the proposed production architecture, then wait for confirmation before Phase 9.

## Definition of Done (each phase)
- Code builds, linters are clean, tests pass (`task ci` green for the touched parts).
- Results are reproducible with a single `task …` command from a clean checkout plus the data.
- Docs are updated (PROGRESS.md, plus the relevant spec/ADR/README section).
- Nothing in reports is hand-typed: tables and figures are regenerated by `task report`.
