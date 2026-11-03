# ruhrbench: project plan (instructions for Claude Code)

Read this file and `CLAUDE.md` (project context) at the start of every session. If they conflict, `CLAUDE.md` describes what is already done and this file describes what to do next.

## 0. Working rules for Claude Code

1. **Small, verifiable steps.** One milestone item at a time. Run `just check` (lint, types, tests) before every commit. Commit messages: imperative, one topic each.
2. **Never invent numbers.** Every number in a README, table or plot must come from a result file under `results/` produced by the harness. READMEs get their tables from a `make report` / `just report` script (marked `<!-- generated -->` blocks), never typed by hand.
3. **Verify before trusting.** Model names, library versions, CLI flags and benchmark claims in this plan were researched in autumn 2026 and may be stale. At the start of each milestone, check the current version on PyPI / crates.io / the project's docs and record what you pinned in `docs/versions.md`.
4. **Ask before:** downloading more than ~1 GB, scraping any website (check its terms and robots.txt first; Bundesanzeiger is off limits for automation), installing system packages, changing GPU power limits, or running anything that needs sudo. Explain what and why in one line, then wait.
5. **Keep the box quiet during measured runs.** Do not run builds or other GPU work while a benchmark executes. The harness must refuse to start if the GPU is busy (other processes in `nvidia-smi`).
6. **Honest negative results are fine.** If Rust is not faster for a stage, report that. The value of this project is the method and the measurements, not a pre-decided winner.
7. **Licensing/hygiene.** MIT for code. Data is never committed (only the empty folder skeleton). Cite sources for datasets in the README. German README (`README.de.md`) at the end of M5.

## 1. Goal

Build a reproducible benchmark of RAG / row-wise table extraction pipelines on German NRW industry documents, from a pandas baseline up to as much Rust as is sensible, on one local machine (Ubuntu, RTX 5060 Ti 16 GB). Everything runs locally.

The project answers three questions, and every milestone should feed at least one:

- **Q1 Quality:** which pipeline extracts the right numbers (field-level F1, retrieval recall@k / MRR / nDCG)?
- **Q2 Speed:** where does the time go per stage, and for which stages does the implementation language (Python vs Rust vs others) actually matter?
- **Q3 Energy:** joules per correct field, tokens per Wh, and cost in EUR at day-ahead prices.

Headline artifact: a **Pareto plot** (F1 vs joules per document, and F1 vs seconds per document) plus a **stage-time breakdown** chart per pipeline.

## 2. Principles of a fair benchmark

- **Same contract at every level.** Every stage implementation takes the same input and produces the same output format, so implementations are swappable (see section 6).
- **Separate three kinds of time** and never mix them:
  1. *kernel time* (in-process, the function only),
  2. *stage time* (including I/O and serialization at the boundary),
  3. *end-to-end time* (whole pipeline for N documents, including process start and model load).
- **Cold vs warm.** Report both. Cold = fresh process, empty page cache where feasible (`sync; echo 3 | sudo tee /proc/sys/vm/drop_caches`, ask first). Warm = after warm-up runs.
- **Statistics.** At least 10 repetitions, report median, p95 and the spread (min/max or MAD). No means without spread. Pin versions, CPU governor, thread counts. Record the hardware and software environment in every result file.
- **Pin threads explicitly** (`taskset`, `RAYON_NUM_THREADS`, `OMP_NUM_THREADS`, `POLARS_MAX_THREADS`) and sweep 1, 4, 8, all as a separate axis. "Rust is faster" is often just "Rust used all cores".
- **Equal effort.** Each implementation gets a reasonable, idiomatic effort, and the tuning applied to each is recorded. Document if one side is a quick port and the other is tuned.
- **Python calling Rust is not Rust.** Tantivy-py, Polars, Qdrant's Python client and PyO3 modules run Rust kernels but pay Python/FFI overhead and conversion costs. Treat "Python wrapper around Rust" and "pure Rust binary" as different rows.
- **Accuracy is a gate.** A faster implementation only counts if its output matches the reference (identical top-k for deterministic stages, or metrics within tolerance for approximate ones). Add parity tests, as in Ruhrwatt.

## 3. Repository layout (target)

```
ruhrbench/
  CLAUDE.md                  project context (exists)
  PLAN.md                    this file
  pyproject.toml, uv.lock    Python 3.13, uv
  Cargo.toml                 workspace
  rust-toolchain.toml        pin stable
  justfile                   one-command entry points
  src/ruhrbench/
    ingest/                  (exists) ixbrl, esef, prices, hf_labeled, pdf_baseline
    bench/                   harness: timer, stats, energy, result schema, runner CLI
    pipelines/               stage contracts + Python implementations (L0, L1, L2)
    eval/                    retrieval metrics, KPI scoring, error taxonomy
    report/                  results -> tables, plots, README blocks
  crates/
    ruhrbench-core/          shared types, stage traits, Parquet/JSON I/O
    ruhrbench-parse/         PDF/XHTML parsing, de-hyphenation, chunking
    ruhrbench-index/         Tantivy BM25, dense index clients
    ruhrbench-embed/         fastembed-rs / ort / candle embedding
    ruhrbench-extract/       row matcher + LLM client (OpenAI-compatible HTTP)
    ruhrbench-cli/           pure-Rust end-to-end binary (rayon + tokio)
    ruhrbench-py/            PyO3 module (maturin) exposing selected kernels
  benches/                   criterion benches for Rust kernels
  configs/                   one YAML per pipeline configuration
  results/                   raw result JSONL + Parquet (gitignored except samples)
  docs/                      versions.md, methodology.md, results.md, devops.md
  deploy/                    docker compose, prometheus, grafana
  tests/                     pytest (offline), parity tests across languages
```

## 4. Tooling and DevOps (show these on the README)

Mirror the discipline already used in Ruhrwatt and anomaly-inspect.

| Area | Tool | Purpose here |
|---|---|---|
| Python env | **uv** + lockfile | reproducible env, `uv sync` builds the Rust module via maturin |
| Python quality | **ruff**, **mypy** (or pyright), **pytest** | lint, format, types, tests |
| Rust quality | **cargo fmt / clippy / test**, **cargo-nextest** | lint and tests |
| Rust perf | **criterion** | micro-benchmarks of kernels (statistics included) |
| CLI timing | **hyperfine** | end-to-end process timing with warm-up and repeats |
| Load tests | **oha** (or k6) | HTTP latency/throughput for L4 serving |
| Profiling | **py-spy**, **cargo-flamegraph** / `perf`, **nsys** (Nsight Systems) | find where time goes |
| Task runner | **just** (or Makefile) | `just fetch`, `just bench`, `just report`, `just check` |
| Hooks | **pre-commit** | ruff, clippy, block data/model files from being committed |
| CI | **GitHub Actions** | Python + Rust + (small) integration tests on every push; cache uv and cargo |
| Dependencies | **Dependabot / Renovate** | keep pins fresh, bump via PR |
| Containers | **Docker + Compose** | Qdrant, vLLM, llama.cpp server, Prometheus, Grafana; multi-stage Rust image, non-root |
| GPU telemetry | **Zeus** (energy), **nvidia-smi / NVML**, optional **DCGM exporter** or an nvidia-smi Prometheus exporter | energy and power time series |
| Dashboards | **Prometheus + Grafana** | power, temperature, clocks, tokens/s during runs (optional, M5) |
| Experiment/result tracking | start with plain JSONL + Parquet in `results/`; add **MLflow** (as in anomaly-inspect) or **DVC** only if it earns its keep | reproducible runs, no hand-edited numbers |
| Data/model versioning | **DVC** (local remote) for gold labels and derived datasets, pointers only in git | |
| Docs | `docs/methodology.md`, auto-generated `docs/results.md` | |

CI rules: CI must run **offline** with tiny fixtures (a few KB of XHTML/PDF, a 20-passage retrieval set, a mock LLM server returning canned JSON). Tests that need the GPU, big models or the network skip themselves with a clear marker. CI also runs `cargo clippy -D warnings`, `ruff`, `mypy`, and builds the Docker image.

## 5. Harness specification (build first, M2)

One runner for every level, so numbers are comparable.

```
ruhrbench run --config configs/<pipeline>.yaml --reps 10 --warmup 2 --docs N --out results/
```

Each result record (JSONL, one line per repetition per stage) contains:

- `run_id`, `git_commit`, `config_hash`, `pipeline`, `stage`, `impl`, `rep`, `cold` (bool)
- `n_docs`, `n_pages`, `n_chunks`, `n_tokens_in`, `n_tokens_out`
- `wall_s`, `cpu_s` (user+sys), `peak_rss_mb`, `peak_vram_mb`
- `threads`, `batch_size`, `concurrency`
- `gpu_energy_j` (Zeus counter delta), `gpu_idle_w`, `gpu_energy_adjusted_j`, `cpu_energy_j` (RAPL if available, else null), `wall_plug_energy_j` (optional smart plug)
- `power_limit_w`, `gpu_clock_mhz_median`, `gpu_temp_max_c`, `throttled` (bool)
- `metrics`: quality metrics for the stage (recall@k, MRR, nDCG, field F1, error counts)
- `env`: CPU model, governor, kernel, driver, CUDA, Python and Rust versions, library versions

Rules the harness enforces:

- Warm-up runs are discarded but stored.
- Refuses to run if the GPU shows foreign processes or if GPU temperature is above a threshold at start (let it cool).
- Persistence mode on; power limit set explicitly and logged (ask before changing it).
- Energy: GPU via Zeus hardware counters (fallback NVML polling at 100 ms). Report both gross and idle-adjusted energy. CPU via RAPL if readable (root/capability needed, ask), because CPU-only levels (L0, Rust kernels) look free otherwise. If possible, cross-check one run with a wall smart plug.
- LLM runs also record TTFT, inter-token latency, tokens/s (prefill and decode separately), and request-level p50/p95/p99 under concurrency.

Report generator: `ruhrbench report` reads `results/`, writes `docs/results.md`, the Pareto plot, the stage-time stacked bar chart, the scaling curves, and README blocks.

## 6. Speed-comparison matrix (the core of Q2)

Define a **stage contract** for each stage (input and output as Parquet/JSONL with a fixed schema, plus a Python Protocol and a Rust trait). Implementations are then compared stage by stage and end to end.

| Stage | Implementations to compare |
|---|---|
| **Parse** (PDF to page text, tables) | PyMuPDF (Python), PyMuPDF `find_tables`, Docling, pdfium-render (Rust), lopdf / pdf-extract (Rust), XHTML parsing with lxml vs a Rust HTML parser (scraper / quick-xml), small document VLM (e.g. PaddleOCR-VL, GLM-OCR; check current options) for scanned or hard layouts |
| **Normalize + chunk** | pure Python, Polars (Python), Rust kernel via PyO3, pure Rust; strategies: per page, fixed tokens, table-as-chunk, row-as-chunk |
| **Lexical retrieval** | TF-IDF/BM25 in pandas or rank_bm25 (L0), scikit-learn, Tantivy via tantivy-py, Tantivy in pure Rust, DuckDB FTS; German analyzer variants (stemming, umlaut folding, compound handling) |
| **Embedding** | sentence-transformers (PyTorch), Hugging Face TEI (Rust server), fastembed-rs (ONNX), ort, candle; models: bge-m3 baseline, a current small multilingual model (check MTEB and a German retrieval test), fp16 vs int8; batch size sweep |
| **Vector search** | numpy brute force, FAISS (flat and HNSW), hnswlib, Qdrant (Python client vs Rust client), LanceDB, usearch; scale 1k to 1M chunks (synthetic extension of real chunks) |
| **Fusion + rerank** | RRF in Python vs Rust; cross-encoder / reranker model (GPU) vs none |
| **Extraction** | rule-based row matcher (regex in Python vs Rust `regex`), LLM via llama.cpp, vLLM, mistral.rs (same model and quantization where possible), constrained JSON decoding on vs off |
| **Orchestration** | Python asyncio + httpx vs Rust tokio + reqwest, sweeping concurrency 1..64 to see where Python overhead shows |
| **Serialization / glue** | JSON vs Parquet vs Arrow IPC between stages; in-process (PyO3) vs separate processes |

Measure each stage in isolation (kernel and stage time) and then end to end. Expectations to test, not assume:

- Parsing, tokenization, chunking, BM25 indexing and serialization are CPU-bound, so a language difference is plausible there.
- Embedding and LLM decoding are GPU-bound (memory-bandwidth-bound for decode), so language should barely matter. If the data confirms that, say so clearly, because it is the key insight of the project.
- Orchestration overhead only matters under high concurrency or with many small requests.

For each stage produce: time vs input size, time vs threads, peak memory, and a parity check against the reference implementation.

## 7. Milestones

Work through these in order. Each has acceptance criteria; do not start the next until they are met (or explicitly deferred).

### M1. Gold data you can trust (finish ingestion)
- Compute per-KPI coverage across the 40 Austrian reports (revenue, net income, total assets, equity, cash, operating income). Hand-check 3 values against the published reports (scale, sign, year). Write findings to `docs/gold_quality.md`.
- Inspect the GermanQuAD parquet schema and write it into `docs/data.md`.
- Test `fetch-prices` schema against the real file.
- **NRW gold:** several NRW groups publish ESEF XHTML on their investor-relations pages (RWE's reporting page lists an XHTML annual report). Ask the user before downloading; check each site's terms. Run `ixbrl.py` on them. Extend the gold from 6 KPIs to **all tagged rows of the primary statements** (income statement, balance sheet, cash flow): label, concept, current year, prior year, unit, scale. Keep the matching PDF of each report as the test input, so XHTML is gold and PDF is the hard input.
- Acceptance: gold table in Parquet with schema documented; at least 5 NRW reports with both XHTML gold and PDF; tests green.

### M2. Harness, baselines, CI
- Build `bench/` (section 5), the result schema, `report` skeleton, `justfile`, pre-commit, GitHub Actions (offline fixtures only).
- Check what inference server exists: run `nvidia-smi`, `which ollama llama-server`, `docker ps`, `pip list | grep -i vllm`. Report back. Do not install anything big before telling the user.
- L0 baselines: TF-IDF/BM25 retrieval on GermanQuAD (recall@k, MRR); regex KPI extraction on the gold set.
- Acceptance: `just bench-l0` produces JSONL and a generated results table; CI green; methodology doc written.

### M3. Row-wise extraction pipelines (quality, Python first)
- Table/row chunking (row-as-chunk with title and column headers).
- Pipelines A to D: (A) regex only; (B) BM25 + row matcher, no LLM; (C) BM25/dense hybrid + small LLM JSON; (D) hybrid + rerank + larger LLM. Include the two oracle baselines: gold page into the LLM (pure extraction error) and whole statement section in long context (no retrieval).
- Validators without labels: accounting identities (balance sheet balances, subtotals add up, signs, scale consistency). Use them to build a **cascade**: cheap path first, bigger model only for rows that fail validation.
- Error taxonomy per field: wrong scale (x1000), wrong sign, wrong year column, wrong row, missing, hallucinated.
- Acceptance: F1 per KPI and per pipeline on ESEF gold and NRW gold; error-type chart.

### M4. Speed ladder (Rust and friends)
- Implement the Rust crates in this order, each with criterion benches, parity tests against the Python reference, and PyO3 bindings where useful: (1) chunker + normalizer, (2) PDF/XHTML parse, (3) Tantivy BM25, (4) row matcher with `regex`, (5) embedding via fastembed-rs/ort, (6) pure-Rust CLI that wires everything with rayon and tokio.
- Run the full matrix of section 6, with thread and size sweeps (1k, 10k, 100k, 1M chunks for retrieval stages).
- Add GPU serving comparison: llama.cpp vs vLLM vs mistral.rs for the same extraction workload; sweep concurrency 1..32, quantization (GGUF Q4/Q5/Q8 vs NVFP4 where supported, check current vLLM Blackwell support), max tokens, thinking mode on/off, vLLM n-gram speculative decoding on/off (extraction copies text, a good case for prompt lookup), prefix caching on/off.
- Acceptance: stage-time breakdown chart per pipeline, scaling plots, speedup table with spread, parity tests green. A short written "where Rust matters and where it does not" section with evidence.

### M5. Energy, cost, polish
- Energy sweeps: GPU power limit (e.g. default 180 W vs the card's allowed minimum; check `nvidia-smi -q -d POWER` first and ask before changing), batch size, quantization, model size, MoE vs dense. Report joules per correct field, tokens per Wh, idle-adjusted and gross.
- Cost: EUR per 10,000 documents at the day-ahead prices from `prices.py`, and the saving from running the batch in the cheapest hours.
- Final Pareto plots; README (English) with result highlights first (3 to 5 generated lines, one plot), architecture diagram, how to reproduce, limitations. `README.de.md`. CI badge. Optional Prometheus/Grafana dashboard of a benchmark run (screenshot in README).
- Acceptance: a stranger can run `just setup && just bench-all && just report` and regenerate every number.

## 8. Quality metrics and gold

- Retrieval: recall@k (k = 1, 5, 10), MRR, nDCG@10 on GermanQuAD, and on a small self-built set for table rows (query = KPI label, relevant = gold row).
- Extraction: field-level exact match with numeric tolerance after normalizing scale and sign; F1 per KPI; macro-average over KPIs; report the error taxonomy.
- Always report quality next to cost (time, joules). A result without both is incomplete.

## 9. Hypotheses to write down before running anything

Create `docs/hypotheses.md` with predictions and commit it **before** the M4 and M5 runs. Suggested:

1. A BM25 + row-matcher pipeline reaches within a few F1 points of the LLM pipelines on statement rows at a tiny fraction of the energy.
2. For GPU-bound stages (embedding, LLM decode) the language of the client does not change throughput measurably; for parsing, chunking and BM25 indexing a Rust implementation is clearly faster than pure Python.
3. Lowering the GPU power limit costs little decode speed (memory-bandwidth-bound) and improves tokens per watt, but costs more during compute-bound prefill/embedding.
4. Output tokens dominate energy; cutting output length (short keys, schema-constrained output, no thinking) saves more than any other single change.
5. Prior-year vs current-year column confusion is the most common LLM error on financial statements.

At the end, record which were confirmed, refuted or inconclusive. Refuted hypotheses make the write-up more credible, not less.

## 10. Things not to do

- Do not scale to 1M documents; 1M applies to retrieval microbenchmarks on synthetic chunks only.
- Do not benchmark Ollama as a serving contender (it wraps llama.cpp with its own defaults); use llama.cpp directly.
- Do not tune one pipeline harder than the others without recording it.
- Do not ship numbers from a run that was throttled, shared the GPU, or had a different power limit than the rest of the series.
- Do not scrape sites whose terms forbid it; prefer official downloads, ESEF files and open datasets.

## 11. First session checklist

1. Read `CLAUDE.md`, run `uv run pytest`, confirm 9 tests pass.
2. Run `nvidia-smi`, `which ollama llama-server`, `docker ps`, `cargo --version`, `just --version`, `hyperfine --version`; report what is missing.
3. Create `docs/versions.md`, `docs/hypotheses.md` (section 9), and the empty directory skeleton from section 3 (commit only skeleton and config).
4. Start M1: per-KPI coverage report over the 40 ESEF reports, then hand-check list for the user.
5. Write the next-steps list back into `CLAUDE.md` when done, so the next session starts from the current state.
