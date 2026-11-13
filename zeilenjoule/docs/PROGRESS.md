# PROGRESS — ZeilenJoule Sitzungslog / session log

## 2026-11-06: Session 1, Phase 0 (environment audit + compatibility probe)

**Done**
- Read `CLAUDE.md` and `docs/PLAN.md` in full.
- Environment audit → `docs/ENVIRONMENT.md` (raw outputs in `docs/env/`).
  Ubuntu 24.04.5, kernel 6.17 OEM, i5-14400F (6P+4E, 16 T), 31 GiB RAM, 232 GB free, RTX 5060 Ti (cc 12.0), driver 580.178.04 (CUDA 13.0), no host CUDA toolkit, PCIe 4.0 x8.
- Compatibility probe → `docs/COMPAT.md` (probe scripts in `docs/env/probes/`, outputs `docs/env/probe_*.out`).
- User checklist → `docs/CHECKLIST_GATE1.md`.
- User-level installs (no sudo): `go-task` 3.54.0 (`~/go/bin/task`), DuckDB CLI 2.0.0 (`~/.local/bin/duckdb`).
- Pulled probe images (~30 GB total, each < 20 GB): cuda 13.0.2 base/devel, pgvector 0.8.7-pg18, qdrant 1.20.0-gpu-nvidia, TEI 120-1.9.4, llama.cpp server-cuda, vLLM v0.33.0.
- Downloaded into `models/`: `intfloat/multilingual-e5-small` (MIT), `Qwen/Qwen3-0.6B` safetensors + `Qwen3-0.6B-Q8_0.gguf` (Apache 2.0). Probe-only; model cards follow in Phase 3/5.
- llama.cpp source checkout at `~/.cache/zeilenjoule/llama.cpp` (commit 6c01da6), built in a `nvidia/cuda:13.0.2-devel` container for sm_120. Needed the `libcuda.so.1` stub symlink; `llama-bench` runs on CUDA.

**Key findings**
- Snap Docker owns the socket → `--gpus all` broken, `--runtime=nvidia` works; `/tmp` mounts empty; use `--user 1000:1000`.
- RAPL is root-only → CPU energy not measurable until checklist C1.
- NVML energy counter works unprivileged; the power reading updates at only ~2 Hz at idle.
- vLLM v0.33.0 works natively (FLASH_ATTN), TEI has an official sm_120 image, and the llama.cpp CUDA image includes sm_120. Several PLAN §3.2 caveats are outdated.
- cudf-polars pins polars 1.44.2 vs. free-resolved polars 2.1.0 → ADR needed.
- Driver 580 = CUDA 13.0 ceiling; vLLM nightlies are now cu134.
- Machine is busy (loadavg 5–8: k3s, another project's JAX benchmark, containers, Chrome).

**Next**
- Wait at **Gate 1** for the user's checklist actions and decisions (Docker A/B, Polars versions, governor, Ollama).
- Then Phase 1: scaffold (Taskfile, uv projects `df`/`gpu_df`/`embed`/`llm-clients`, cargo workspace, Go harness module, `config/*.yaml`, `.env.example`), ADR 0001 (snap Docker/GPU runtime), ADR 0002 (Polars versions).

**Open questions**: see `docs/CHECKLIST_GATE1.md` → "Questions / decisions".

## 2026-11-06: Session 1, Gate 1 decisions

**User decisions:** Docker A (docker-ce), Polars stable only, governor powersave + performance comparison, Ollama via Docker without sudo. Recorded as ADRs 0001–0004.

**Done**
- Backed up snap-only Docker data to `~/docker-snap-migration/`: open-webui volume (stopped ~10 s), local images, inspect dumps. Wrote `README.md` (user sudo steps) and `restore.sh` (no sudo).
- ADRs `docs/adr/0001`–`0004`; checklist updated.

**Pending user actions:** C1 RAPL, C2 persistence mode, C3 stress-ng, C4 step 1 (`snap remove --purge docker` + `nvidia-ctk runtime configure`). C5/C6 only before measured sessions.

**Next:** after the Docker switch, run `restore.sh`, re-verify `--gpus all`, then Phase 1 scaffold.

## 2026-11-06: Docker switch done (ADR 0001 executed)

- User ran step 1. docker-ce 29.9.3 is now the only engine (`/var/lib/docker`); `--gpus all` works.
- `restore.sh` ran: open-webui (healthy, HTTP 200, `webui.db` restored byte-identical in size), anomaly-inspect-mlflow (healthy, HTTP 200), toyapp (running; `/` returns 404, pre-migration behaviour not verified). anomaly-inspect `inference` was already exited before and was left stopped.
- Probe images need re-pulling into docker-ce (by digest) when Phase 1+ needs them.
- Backups in `~/docker-snap-migration/` kept; the user can delete them once satisfied.

**Next:** Phase 1 scaffold.

## 2026-11-06: Phase 1 (scaffold) done

**Done**
- Repo tree per CLAUDE.md. Root uv workspace (zjmark, datagen, reference, eval, analysis; Python 3.12.14 uv-managed) + 4 separate locked contestant envs (`df` polars 2.1.0 / pandas 3.1.1 / duckdb 2.0.0; `gpu_df` cudf 26.10.2 / polars 1.44.2; `embed` torch 2.14.1+cu130 / ORT 1.31.1 / sentence-transformers 6.2.0; `llm-clients`). Every env has `envcheck` (version fingerprint; `--gpu` proves device use).
- zjmark stage-marker libs in Python, Rust and Go, byte-identical against `spec/zjmark_golden.txt` (canonical JSON: sorted keys, no ASCII/HTML escaping). The Go side also has a strict parser + fuzz test (20 s, 2.3M execs, clean).
- Go harness module: `zj version`, `zj doctor [--json] [--gpu-test]` (OS/CPU/RAM/disk/load/governor/RAPL/GPU/Docker/tools + contestants.yaml validation), `internal/config` with strict YAML (unknown keys rejected) and registry validation.
- Smoke contestants `x00_smoke_py`, `x01_smoke_rs` (track X) registered and run per their config.
- `config/{bench,contestants,models}.yaml`, `.env.example`, `.gitignore`, `Taskfile.yml` (setup/doctor/lint/test/ci; later-phase tasks fail loudly), README.de/en, ADR 0005.
- Verified: `task ci` green; **clean-checkout copy: `task setup` + `task ci` both exit 0**; `zj doctor --gpu-test` all OK except loadavg (busy machine).

**Issues found and fixed along the way**
- pyright checked **0 files** for the contestant envs (root `exclude` swallowed explicit paths). Fixed with per-env `pyrightconfig.json`; the real errors that surfaced were fixed.
- go-task `env: PATH` can't override an existing PATH → Go tools are resolved via `go env GOPATH`.
- uv_build needs `module-name` when the package name differs from the project name (llm-clients).

**Next (Phase 2: harness):** `zj run` (process supervision, ZJMARK ingestion, timeout/DNF/OOM), NVML energy counter + 20 Hz power sampling (go-nvml), RAPL sampler with wrap handling, `zj idle`, results JSONL + DuckDB load, `zj verify` skeleton, harness overhead + calibration → `docs/CALIBRATION.md`, `docs/METHODIK.md`.

**Open questions:** none blocking.

## 2026-11-06: Phase 2 (harness), part 1, stopped at usage limit

**Done (all `go test -race ./...` green)**
- `internal/energy`: NVML (go-nvml 0.13.4; energy counter primary, power/temp/VRAM/util), RAPL discovery + wrap handling, 20 Hz sampler, cumulative series with edge interpolation, per-window/stage energy + trapezoid cross-check. Live NVML test passes (opt-in `ZJ_NVML_TEST=1`).
- `internal/procs`: process-tree RSS via `/proc/<pid>/task/*/children`.
- `internal/results`: PLAN §5.5 record (+ diagnostics), null = not measured, derived metrics, fsync'd JSONL store, UUIDs.
- `internal/verify`: fail-closed builtins `x.smoke`, `x.calib`.
- `internal/runner`: process group exec, timeout→dnf, SIGKILL→oom, protocol checks, leftover-kill, load guard, cooldown, seeded interleaved plan, sessions (manifest, builds, versions_cmd, idle baseline every 30 min, governor guard, resume), env fingerprint.
- `internal/exporter`: Prometheus text metrics, unwrapped RAPL.
- CLI: `zj run / idle / verify / exporter` (+ doctor, version). `config/tasks.yaml`; bench.yaml `runs:` + `load_guard.max_wait_s`.
- Checked: `zj run --dry-run` plan OK; `zj verify x01_smoke_rs` → ok, 10000 lines match.

**Left for Phase 2**
1. `golangci-lint` pass over the new packages (not run yet); add `CGO_CFLAGS=-Wno-deprecated-declarations` to the Taskfile `env:` (silences go-nvml header warnings).
2. Taskfile: wire `bench`, `bench:one`, `verify`, `idle`, `exporter` to `bin/zj`.
3. `python -m zjmark.envinfo` `__main__` (x00 `versions_cmd` needs it; `zj run` with x00 fails until then).
4. Calibration contestants: `zjmark.wrap` (x02 stress-ng CPU), `zj_embed.gpuburn` (x03 torch fp16 60 s), x04 sleep 30 (overhead) → `zj calibrate` → generated `docs/CALIBRATION.md`. **Needs a quiet machine (pause k3s, cytopk loop, containers: checklist C5/C6).**
5. `zj_analysis.load`: runs.jsonl → results/zj.duckdb (+ test).
6. `docs/METHODIK.md` (DE + EN summary).
7. `task ci` green + PROGRESS update.

## 2026-11-06: Phase 2 (harness), part 2

**Done (items 1–7 of part 1's list, except running the calibration)**
- golangci-lint clean on all new packages. Real fix found by errcheck: non-exit `Wait` errors are now protocol errors. Process-group kill documented as the reason for not using `CommandContext`. Result/log files are 0600.
- Taskfile: `bench`, `bench:one`, `bench:resume`, `verify`, `idle`, `calibrate`, `results:load`, `exporter`; `CGO_CFLAGS` silences go-nvml header warnings.
- `python -m zjmark.envinfo` (versions_cmd), `zjmark.wrap` (contract wrapper for load generators), `zj_embed.gpuburn` (fp16 matmul load).
- Calibration contestants `x02_stressng_cpu`, `x03_gpuburn_torch`, `x04_sleep30_rs`; task option `no_rows`.
- `internal/calib` + `zj calibrate`: idle ×3, then overhead/CPU/GPU runs, checks (idle CV ≤ 5 %, RAPL plausible vs PL1/PL2, GPU ≥ 80 % of limit, NVML counter vs integral ≤ 5 %, sleep-30 overhead) → `results/calibration/<id>.json` + generated `docs/CALIBRATION.md`.
- `zj_analysis.load` → `results/zj.duckdb` (`runs`, `idle`, view `ok_runs`).
- `docs/METHODIK.md` (DE + EN summary).
- `task ci` green.

**Live smoke session `09dc21db…` (track X, 2 runs, both ok) found a methodology bug**
- The load guard passed at the start of the 60 s idle baseline, but background load began during it (loadavg1 1.94 at the end, `quiet: false`). P_idle (cpu_pkg 15.4 W) was used anyway → E_net of the ~3 W smoke runs was **negative** (−14.6 J).
- Fix: `MeasureQuietIdle`. A baseline is used only if it stayed quiet; non-quiet attempts are stored as data but discarded; after 3 attempts the session aborts (resumable). Applies to sessions and `zj calibrate`. Test added.
- The two smoke records and the non-quiet idle record stay in `results/` as-is (data of record, track X, not ranked). Their E_net values are invalid for the reason above.
- The load guard waited ~15 min at session start (another project's Rust build, loadavg 5.1).

**Left for Phase 2:** run `task calibrate` on a quiet machine (user action: C5/C6), review `docs/CALIBRATION.md`, then Phase 2 is done.

## 2026-11-06: Phase 2 done (calibration)

**Calibration runs** (all data in `results/calibration/`, `results/runs.jsonl`, `results/idle.jsonl`; k3s stopped, 3 idle containers running)
1. `0230bf33…`: 7/8 pass. **FAIL: NVML counter vs integrated power +22.2 %.** Diagnostic `docs/env/probes/probe_nvml_load.py`: under load all power fields read 180.0 W (= limit), the counter only 146.3 W → **ADR 0006: GPU energy = integral of power samples, counter kept as a cross-check.** Also fixed: the trapezoid extrapolated power outside the sampled range (caught by an existing test).
2. `7aadc353…`: GPU checks now pass (burn 177.1 W), but **FAIL: E_net of a single `sleep 30` = 112 J.** Per-domain breakdown: GPU and CPU both drew more during that window (background/desktop burst below the load guard). Fix: overhead check = median of 3 reps. ADR 0006 addendum: the counter error is not a fixed factor (idle: counter +16 %, load: −19…−26 %).
3. `b252097a…`: **all graded checks pass.** Idle CV 0.7 / 0.6 / 1.7 %; CPU 73.7 W (PL1 125 / PL2 150 W); GPU burn 176.7 W of 180 W; overhead median 30.004 s and −3.1 J (−3.1, −3.3, −0.6 J). The counter deviation (−19.0 %) is reported as info. → `docs/CALIBRATION.md` (generated).

**Observations for later phases**
- Idle GPU power differs *between* sessions (4.57 W in calibration 2, 5.43 W in calibration 3) but is stable *within* one (CV ≤ 0.7 %). This confirms per-session idle baselines.
- The **load guard costs ~3.4 min after every CPU-heavy run**: loadavg1 is a lagging 1-min average (9.5 after stress-ng). With Track A's CPU-heavy matrix this adds hours. Proposal for the user: replace/extend the guard with an instantaneous busy-fraction check from `/proc/stat` over ~2 s (PLAN §5.2 names loadavg1, so this needs a decision).
- A wall meter (C11) would settle the NVML counter vs. power-sample question (ADR 0006).

**DoD:** `task ci` green; `task calibrate` reproduces the report; METHODIK/COMPAT/ADR 0006 updated. The 13 track-X runs in `results/` are harness self-tests, not ranked (`ok_runs` = 0).

**Next: Phase 3:** lexicon (`spec/lexicon/*.yaml`), deterministic generator S/M/L/XL with gold labels, question sets, real-data fetchers (thru.de, TED, BA Jobsuche), dataset cards.

## 2026-11-07: Load guard change (ADR 0007, user-approved)

- New `internal/cpustat`: CPU busy share of all CPUs from `/proc/stat`. Guard: ≤ 5 % over 2 s before each run/idle; idle "quiet" = busy ≤ 5 % over the whole idle window; records get `env.cpu_busy_pct_start`, idle records `cpu_busy_pct`; `zj doctor` check `cpu_busy`. loadavg1 kept as information.
- Live check: 15 s stress-ng, then the guard released after 2 s (loadavg1 still 4.11; the old guard waited ~3.5 min in every calibration).
- Tests: parser, NaN fail-safe, guard loop (busy → busy → quiet; permanently busy → ErrBusy), doctor quiet/busy. `task ci` green.
- Calibration was not re-run for this change: guard/quietness only decide *when* to measure, not *how*. The next calibration will exercise it.

## 2026-11-07: Phase 3 (data) done, except the real-data fetch run

**Done**
- **Lexicon** `spec/lexicon/*.yaml` (~440 entries): 28 fictional NRW plants (Regierungsbezirk, NUTS-2, sector) + 45 line types, 26 components (75 surface forms with grammatical gender, compound stems, equipment group), 12 damage types (59 phrases incl. English loanwords), causes, actions, 31 abbreviations, safety triggers, quality lexicon (11 features, defect classes, 6M causes). Validated on load.
- **Generator** `datagen/` (`task data:gen SCALE=…`): Störmeldungen + Qualitätsmeldungen, labels first then text, deterministic and **prefix-stable** (S ⊂ M ⊂ L byte-identical, verified on the files and across two refactors). L = 1M rows in 8.9 s (XL ≈ 1.5 min, generated on demand). Formats: cp1252 CSV (CRLF), UTF-8 CSV, Parquet, typed gold Parquet, xlsx sample.
- **Evaluation sets** (`task data:derived SCALE=M|L`): D2k (2 000 + 300 dev, disjoint, from M); `spec/qrels_c.{M,L}.jsonl` (1 000 queries, 250 per filter class, relevance as a rule `zj_eval.relevance.grade`); `spec/questions_e.{M,L}.jsonl` (100 lookup / 100 filtered semantic / 100 aggregation with gold SQL).
- **Real-data fetchers** `zj_datagen.fetch` (thru.de PRTR, TED v3, BA Jobsuche v6): polite cached client (≤ 1 req/s, generic UA, raw cache), redaction, data minimisation; tested offline with MockTransport. Probe requests made: robots.txt ×3 (all 404), 1 page per API, terms pages, the thru.de zip (11 MB, SQLite, 1 798 NRW facilities).
- **Dataset cards** `data/cards/*.md` (`task data:cards`, versioned via `.gitignore` exception), generated from manifests + measured stats. ADR 0008.
- `task ci` green (84 Python tests + Go + Rust).

**Bugs found and fixed along the way**
- Strict cp1252 export: `"µ".upper()` → Greek capital mu (U+039C) → µ-preserving upper case.
- Q measurements could round onto the tolerance limit (250,0 ± 0,5 → 0,525 → 0,5) → ≥ 1 rounding unit distance + fail-loud check.
- `englisch` noise tag was set on compound rows without English text.
- Q text grammar ("beim Ebenheit"), double periods, text not naming the out-of-tolerance feature, Faker suppliers like "e.V."/bare surnames.
- `.gitignore` inline comment would have silently disabled the `data/cards` exception.
- Several edits silently missed their target after `ruff format` re-wrapped code; now always re-reading before editing.

**Blocked: user decision needed**
- `task data:fetch` (bulk download: TED ~20 requests, BA ~80 requests at 1 req/s) was **denied by the auto-mode permission classifier**. Not worked around. The user can run `task data:fetch` themselves or allow it; afterwards `task data:cards` fills in the real-data cards.

**Next: Phase 4 (Track A):** `spec/track_a.md` + golden vectors (≥ 150 cases) + JSON schema; `reference/track_a.py`; verifier (`builtin` → external DuckDB comparison); first contestants (a05 pandas vectorized, a09 polars eager, a13 duckdb, a17 Rust hand-rolled); thin E2E report.

## 2026-11-07: Decision on real data, Phase 4 start

- **PENDING (user decision):** real-data fetch (`task data:fetch`: thru.de PRTR, TED, BA Jobsuche) is **not run**. The user decides separately. Code + offline tests exist; real-data cards show "not fetched yet". Nothing downstream may depend on real data until then.
- Phase 4 (Track A) uses **only the synthetic S/M/L sets**; XL is generated only if Track A needs it.
- Quiet-box rule unchanged: nothing heavy runs during measured runs.

## 2026-11-07: Phase 4 (Track A), part 1: thin end-to-end ready, measurement blocked

**Done**
- `spec/track_a.md` v1.0 (DE + EN): exact rules for norm/dates/numbers/durations/abbreviations/rules/doc_text; abbreviations per whitespace token (portable without lookbehind).
- `spec/track_a_lexicon.json` (compiled, fails on ambiguities), `spec/track_a.schema.json`, `spec/track_a_cases.jsonl` (**208 hand-written golden vectors**, incl. half-up rounding traps).
- `reference/` (plain Python, Decimal): passes all vectors; cross-check vs generator gold on S: date/downtime/costs/material/region **100 %**, safety rule 99.98 %, component rule 96.1 % (documented rule limits).
- `zj_eval.verify_track_a` (DuckDB, by value, fail-closed) + harness external verifier (`verifier: cmd`, JSON + exit code must agree). References S/M/L built (`task trackA:reference`).
- **8 contestants**, all verified ok on S through the harness: a01 csv loop, a02 iterrows (anti-pattern, S/M), a03 itertuples, a04 apply, a09 Polars eager, a10 Polars lazy, a11 Polars streaming, a13 DuckDB. Golden vectors pass for row-wise, Polars and DuckDB implementations (619 tests).
- `task report` → `reports/REPORT.de.md` + `REPORT.md` (medians, IQR, CV flag, E gross/net, mJ/row, €/1M rows household/industry, g CO₂, ZJ score, non-counted runs listed).
- Safety net: `zj run` / `zj calibrate` refuse to start without NVML + RAPL (`--allow-missing-energy` to override).
- Loader bug fixed: Go `omitempty` fields absent in all lines → missing column → stable schema enforced.

**Blocked: user action needed before the measured session**
- The machine was **rebooted**: the non-persistent checklist items are gone. RAPL `energy_uj` is root-only again (C1), persistence mode is off (C2), k3s is running again (C5). Track A is CPU-bound, so measuring without RAPL would be useless; `zj run` now refuses.
- Proposal: the **persistent** variants this time (udev rule for RAPL, `nvidia-persistenced` for persistence mode).

**Next:** measured session Track A S/M/L (138 runs, ~40 min, quiet box) → first REPORT; then remaining Core contestants (a05 pandas vectorized, a06 pandas+arrow, a07 cudf.pandas, a08 Polars map_elements, a12 Polars GPU, a15 Rust polars, a16 Rust arrow+rayon, a17 Rust hand-rolled).

## 2026-11-07: Quiet-box script for measured sessions

- `scripts/quiet_box.sh`: `prepare` (persistent RAPL udev rule, C1), `measure [zj args]` (stop k3s incl. pods via k3s-killall.sh, Ollama, all running containers, Chrome (SIGTERM) → `zj doctor` → `zj run --track A --scale S,M,L` → **always restore** via trap, also on Ctrl-C/errors; sudo kept alive so restore does not stall), `stop`/`restore`/`status`, `DRY_RUN=1`. Not touched: desktop, governor (ADR 0003), GPU settings.
- Duration estimate for Track A S/M/L (138 runs): ≈ 40 min, plan ≤ 60 min (basis: unmeasured S times scaled, reference at L, per-run overhead, idle baselines).
- State at writing: RAPL still root-only (C1 not applied yet), nvidia-persistenced active.
- Fix: first `prepare` installed the rule with `ACTION=="add"` only, but `udevadm trigger` sends "change" by default → rule never fired (RAPL stayed 0400). Rule now `ACTION=="add|change"`, trigger with `--action=add` + `udevadm settle`; `prepare` replaces a rule whose content differs.

## 2026-11-07: First measured Track A session (`4c021dc7…`)

- `scripts/quiet_box.sh measure` (user): 138/138 runs ok + verified, 34.6 min, services restored automatically. CPU+GPU energy present in all runs; both idle baselines quiet (CPU 1.1 % busy); max CPU busy at run start 1.1 %; 0 read errors; governor powersave throughout.
- `task report` → `reports/REPORT.de.md` / `REPORT.md`. € columns now use significant digits (were 0.0000).
- Observations (from the report, scale L): Polars lazy/streaming fastest and most efficient (≈ 3.55 s, ≈ 0.25 mJ/row), DuckDB 5.6 s / 0.28 mJ/row, Polars eager 11.7 s / 0.41 mJ/row, Python csv loop 30.9 s, pandas itertuples 35.9 s, pandas apply 82.5 s / 2.31 mJ/row. Stage breakdown: Polars eager spends 10.4 s in `transform` vs 2.5 s for lazy `query` (same expressions → lazy optimiser); forced cp1252→UTF-8 transcode costs only 0.27 s. At M the iterrows anti-pattern is 10× slower than streaming.
- Data caveats: S runs and the fast L runs (< 5 s) are `energy_confidence = low` by design; idle GPU baseline drifted 5.55 → 4.53 W between the two baselines (both quiet).
- **XL needed?** For confident energy numbers of the fast contestants (≥ 10 s windows, PLAN §14) yes. Blocker: the reference keeps all rows in memory (L: 3 GB RSS → XL ≈ 30 GB > 31 GB RAM) → needs a streaming (chunked) reference first.

**Next:** streaming reference + XL data/reference; remaining Core contestants (a05, a06, a07 cudf.pandas, a08 map_elements, a12 Polars GPU, a15–a17 Rust); figures in the report.

## 2026-11-07: Real-data fetch (user approved)

- `task data:fetch` (no measured run active; machine not in quiet mode, which isn't needed):
  - thru.de PRTR 2024: 3,020 NRW facilities, 338 places. The ZIP was already cached from the Phase 2 probing → 0 requests.
  - TED: 5,000 of 13,514 available NRW contract notices (Jan to Oct 2026), 20 requests, 154 redactions (e-mail/phone → `[entfernt]`).
  - BA Jobsuche: 786 Werkstudent ads (28 cities, 25 km radius), 53 requests; 143 company names without a legal form set to null.
- Privacy check after the fetch: 0 e-mail addresses and 0 +49 phone numbers left in the TED texts; no contact or coordinate fields in BA.
- Dataset cards regenerated: `data/cards/{thru_prtr_nrw,ted_nrw,ba_werkstudent_nrw}.md`.
- Real data is used only where PLAN plans it (Tracks C/E); Track A stays synthetic.

## 2026-11-07: Track A completed (Core contestants), streaming reference, XL

Done (all not measured yet; correctness verified against the reference with `zj_eval.verify_track_a`):
- **Streaming reference** (50k-row chunks, fails loudly on non-increasing ids): matches the old M reference exactly; XL reference in 7 min / 268 MB RSS. **XL data** generated (10M rows, 1.7 GB cp1252); first 1M rows identical to L.
- **New contestants**, each verified at S/M/L (and with a shuffled input where sorting is implemented):
  - a05 pandas vectorized (pandas 3 defaults), a06 pandas + pyarrow engine/ArrowDtype (ADR 0009)
  - a07 cudf.pandas = unchanged a05 code; GPU/CPU-fallback call counts per run → report marks "⚠CPU fallback"
  - a08 Polars map_elements (anti-pattern, S/M)
  - a12 Polars GPU engine: **fails loudly** (7 unsupported ops incl. NFC normalize) → recorded at S, not hidden
  - a15 Rust polars lazy (streaming engine), a16 Rust arrow-csv + rayon, a17 pure Rust hand-rolled (ADR 0010)
  - Rust: 208/208 golden cases for both parser flavours; clippy pedantic clean
- Golden tests: pandas added (205 cases); 824 Python tests in the df env.
- Verifier: one join, all threads (XL 48 s → 4.5 s). Report: latest session per contestant/scale (sessions never mixed), CPU-fallback label (rule 6).
- XL feasibility (unmeasured): Polars/DuckDB 28–33 s at 12–21 GB; pandas ≈ 6 min at 24 GB, a09 109 s → XL only for contestants with L < 10 s; a07 at XL exhausted host RAM (memory guard stopped it).
- Unmeasured observations (not results): a17 ≈ 0.9 s at L, a16 ≈ 2.8 s, a15 ≈ 4.6 s; a07 33 s vs a05 37 s at L.

**Next measured session (needs the quiet box, user runs it):** 216 runs (a05, a06, a07, a08, a10, a11, a12, a13, a15, a16, a17 at their scales, incl. XL), estimated 45–55 min.

Open questions:
- a12b: hand-split GPU/CPU hybrid for Polars GPU? (extra, not planned yet)
- a08 superlinear at L (3.7 s at M vs 203 s at L, unmeasured): investigate later if relevant.
- Real-data use for Tracks C/E (data fetched, see above).

## 2026-11-07: a23 Polars GPU hybrid + real corpus for Tracks C/E (user: "do it gpu cpu split, do real c/e time measure")

- **a23_polars_gpu_hybrid** (Extra; `a12b` would break the ID convention): explicit 3-stage plan. CPU streaming (normalise + expand) → GPU `raise_on_fail=True` (fields, lower/fold, Materialnr, safety, Werk lookups, doc_text) → CPU (komponente + sort). Four expressions were rewritten into GPU-supported equivalents (ADR 0011; new COMPAT row, including the cudf-polars `extract_groups` crash).
  - Tests: golden cases on the CPU and the GPU engine (gpu_df env: 195 tests), plus a case-class table checked against Python IGNORECASE over all of Unicode.
  - `verify_track_a` passes at S/M/L/XL. Unmeasured: ≈ 6 s at L, ≈ 40 s / 16 GB at XL.
  - `config/contestants.yaml`: S/M/L/XL. `PolarsRules` refactor: `doc_text()` + `OUTPUT_COLUMNS` are reusable, no change in behaviour (df env tests green).
- **Real corpus** (ADR 0012): `task data:real-corpus` → `data/real/corpus/real_nrw.parquet`: 6,791 documents (thru 1,005 latest year, deduplicated / TED 5,000 / BA 786).
  - Queries: `spec/queries_c.real.jsonl`, 1,000 known-item queries. Median filter selectivity: ort 2.1 %, datum 20 %, kombiniert 0.22 %.
  - Dataset card: `data/cards/real_nrw_corpus.md`.
  - Tracks C/E get a `real` scale: timing, latency and J, with recall vs. exact brute force and the known-item hit rate. No gold-label metrics on real data, and real rows never count toward the winner score.
- Measuring C/E on real data needs Track B embeddings first (Phase 5); planned there.

**Next measured session (user, quiet box):** as before, plus a23: 240 runs (a05, a06, a07, a08, a10, a11, a12, a13, a15, a16, a17, a23), ≈ 50–60 min.

- **Full TED fetch** (user OK): `max_notices` 15,000 (the TED page-number cap). 13,514 notices fetched (= all available for 2026-01..10), 36 requests (20 from cache), 478 redactions.
  - Privacy check: 0 e-mail addresses, 0 +49 numbers, 0 duplicate ids.
  - Real corpus is now **15,305 documents**. Median selectivity: ort 1.3 %, datum 9.6 %, kombiniert 0.08 %. Card regenerated.

## 2026-11-07: Phase 5 started: Track B thin slice (b00–b04)

- **Model:** bge-m3 @ `5617a9f6…` (MIT, checked on the model card), 4.3 GB in `models/hub`. `models/hf` has root-owned locks from the Phase 0 TEI container → checklist item.
- **Spec:** `spec/track_b.md`. Inputs come from `task trackB:inputs` (S/M/L + real); the fp32 reference from `task trackB:reference` (M: 461 s, real: 159 s unmeasured).
- **Verifier:** `zj_eval.verify_track_b` (dtype/shape/finite/norm + mean cos ≥ 0.995). The recall part follows with c00.
- **Harness config:** `b.embed` task, scale `real: 15305` in bench.yaml.
- **Contestants** (ADR 0013; all verified, unmeasured times only):
  - b01 st fp32: S ✓.
  - b02 st fp16: S/M/real ✓ (M ≈ 155 s).
  - b03 sorted + token budget: S/M/real ✓ (M ≈ 113 s).
  - b04 ORT CUDA fused fp16: S/M/real ✓ (M ≈ 114 s).
  - b00 ORT CPU int8 mixed precision: S ✓ (≈ 445 s, mean cos 0.9966).
- **Findings (COMPAT):**
  - ORT's CUDA EP silently runs on CPU without `preload_dlls()`.
  - `disable_cpu_ep_fallback` is unusable (shape nodes) → replaced by a shape-only proof + per-run log.
  - Default dynamic int8 fails the gate (0.985).
- **Tests:** embed env (batching, I/O contract, shape-only proof); eval (verifier).

**Track B measured session** (quiet box), estimated from the unmeasured times at 5 reps + warmup: ≈ 2.8 h in total → split into two sessions:
- B1: b02/b03/b04 at S/M/real + b01 at S/real, ≈ 70 min.
- B2: b01 at M + b00 at S, ≈ 95 min.

**Next:** server runtimes b06 TEI (check GeLU-tanh against the cosine gate), b07 vLLM pooling, b08 llama.cpp embedding; then Track C (c00 exact GPU → recall part of the B gate).

## 2026-11-08: Track A measured session 2 (quiet box)

- Session `17a7791b…`: 240 runs, 1 h 03 min, 234 ok / 6 fail. All failures are `a12_polars_gpu` at S, the expected deliberate failure with raise_on_fail. The report lists them under "Runs not counted".
- `task report` regenerated from 391 runs (sessions `4c021dc7…` + `17a7791b…`).
- **Results** (median wall time, from `reports/REPORT.md`):
  - a17 is fastest at every scale, e.g. XL 6.8 s.
  - The CPU Polars variants a10/a11 come next (about 29 s at XL); a15 rs_polars is slower at L and XL than Python Polars.
  - a23 GPU hybrid: XL 35.6 s, behind a10/a11/a13. GPU work does not pay off in Track A (transfers + 2 CPU stages).
  - a07 cudf.pandas (CPU fallback) at L: 28.4 s, slower than a01.
- **Open methodology point:** the GPU idle baseline fluctuates between baselines by about 1 W (4.53 / 5.58 W, P-state). For long CPU-only runs this gives GPU E_net < 0 (a08 M: GPU net about −20 J/run, total net −0.3 J). This is not a measurement error in the run, but the net value is noisy at this order of magnitude. Proposal (needs an ADR, pending): GPU net for runs without GPU use = 0, or a session-median idle baseline.

**Next:** Track B sessions B1/B2 (user, quiet box), then b06–b08 and Track C.

## 2026-11-08: Track B measured session B1 + report Track B

- Sessions `11062937…` (b02/b03/b04 × S/M/real, 54 runs) and `eba85ebe…` (b01 × S/real, 12 runs): **all 66 ok**, all verified (mean cos ≥ 0.999997 for fp16, 1.0 for b01).
- `zj_analysis.report` generalised to multiple tracks (title/unit per track, Track B counts documents, note about the pending recall part). Tests: 2 new ones.
- **Results** (REPORT.md, median): at M, b03 sorted 112.3 s and b04 ORT CUDA 113.7 s are effectively tied; b02 st fp16 146.5 s; b01 fp32 is about 3× slower than fp16 at S/real. Energy at M about 21 kJ (b03/b04).

**Next:** B2 (b01 M + b00 S, about 95 min, user); then b06 TEI / b07 vLLM / b08 llama.cpp, Track C.

## 2026-11-08: Track B measured session B2

- Sessions `88681801…` (b01 × M) and `47964f5d…` (b00 × S): 12/12 ok, verified.
- b01 fp32 at M: 458.8 s, 88 kJ (≈ 4× b03/b04). b00 ORT CPU int8 at S: 398.0 s, 37.7 kJ, mean cos 0.996574 (min 0.991213), ≈ 26× slower and 16× more energy than b04 at S.
- The Track B thin-slice matrix (b00–b04 × S/M/real as configured) is fully measured; `task report` is updated (469 runs).

**Next:** b06 TEI, b07 vLLM pooling, b08 llama.cpp embedding; then Track C (c00 → recall part of the B gate).

## 2026-11-08: ADR 0014 (GPU net share), cleanup

- **ADR 0014** (user OK): in the analysis, CPU-only contestants (`needs_gpu: false`) get **no GPU net share** (GPU idle drift ≈ 1 W made E_net negative). `zj_analysis.load` adds the column `uses_gpu`; the report SQL drops the GPU net share accordingly. E_gross is unchanged, and so is results/. METHODIK de/en updated, 1 new test. Effect: a08 M E_net −0.3 J → 20.9 J.
- `models/hf` chown done by the user. `models/derived/bge-m3/qexp` deleted (9.5 GB, user OK).
- analysis: ruff/pyright/pytest green; `task report` regenerated (469 runs).

**Next:** finish Phase 5 Core: b06 TEI, b07 vLLM (Qwen3-Embedding), b08 llama.cpp, b10 Rust ort.

## 2026-11-08: Phase 5 Core complete: b06 TEI, b07 vLLM, b08 llama.cpp, b10 Rust ort

- **Server contestants** (ADR 0015): `zj_embed.track_b.server` (container lifecycle, health, GPU proof via container VRAM + log marker, ordered HTTP fan-out) + `served.main` (stages load/read/embed/write).
  - b06 TEI 1.9.4: fp16 FlashBert, server-side truncation via `hf512`.
  - b07 vLLM 0.33.0: pooling runner, CLS from the ST config.
  - b08 llama.cpp b12169: GGUF f16, client token ids, `-fa on -ub 1024`.
  - Images pinned by digest.
- **Model preparation** `task trackB:serverprep`: `hf512` (safetensors + 512 limit), `gguf` (converter at the image's commit, protobuf pin fix).
- **b10_rs_ort:** Rust `ort` 2.0.0-rc.15 (load-dynamic → the same libonnxruntime 1.31.1), `tokenizers` 0.22.2, same graph/batching as b04.
- **Unmeasured check** (verify ✓): b06 S 22 s / real 44 s (cos 0.999987); b07 47 / 69 s (0.999999); b08 31 / 79 s (0.99998); b10 13 / 38 s (0.999997).
- COMPAT: 4 new rows (TEI safetensors/length, llama.cpp no truncation + converter pin, ort load-dynamic). Tests: embed +5 (server), Rust b10 +2. `task ci` green.

**Measured session (user, quiet box):** b06/b07/b08/b10 × S/M/real = 72 runs, estimated ≈ 1 h 45 min.
**Next:** Phase 6 Track C (c00 exact search → recall part of the B gate).

## 2026-11-08: Track B session B3 measured (b06/b07/b08/b10)
- **Done:** quiet-box session `5227bccf` (user), b06_tei/b07_vllm_pooling/b08_llamacpp_embed/b10_rs_ort × S/M/real, 72/72 ok, 1 h 46 min. `task report` regenerated (Track B now 9 contestants).
- **Findings (from results/, medians):** M fastest = b06_tei 108.8 s / 19.6 kJ gross (embed stage 96.3 s, but 11 s container start); b10_rs_ort fastest at S (12.9 s) and real (38.1 s). b07 vLLM has the lowest gross energy at real (6.3 kJ) despite 68 s wall (24.5 s load). b08 llama.cpp slowest GPU contestant (M 246.8 s). Server load stage: TEI ≈ 11 s, vLLM ≈ 24.5 s, llama.cpp ≈ 5 s vs. in-process 1–4 s.
- **Next:** Phase 6 Track C (c00 exact search + Track B recall gate).

## 2026-11-08: Phase 6 Track C, part 1 (`c.dense`)
- **Done:**
  - Spec `spec/track_c.md`, ADR 0016 (one run = ingest + index + 1 000 filtered queries; gate recall@10 ≥ 0.95 per filter class; nDCG@10 / hit@10 reported).
  - Inputs `task trackC:inputs SCALE=M|real` (`zj_embed.track_c_prep`): docs + metadata, fp16 reference vectors, normalised filters, query vectors (b01 path). Reference `task trackC:reference` (`zj_reference.track_c` numpy fp32 brute force, `zj_eval.track_c` labels + quality): dense ceiling nDCG@10 M 0.8476, hit@10 real 0.999.
  - Verifier `zj_eval.verify_track_c`; harness copies a verifier's `quality` object into the run record (`verify.RunCmdQuality`, Go test).
  - Track B retrieval gate in `verify_track_b` (M, real; Δ quality ≤ 0.01). Earlier B runs were verified without it (report note).
  - New env `contestants/python/db`. Contestants c00_exact_gpu (embed env), c01_faiss_cpu, c03_pgvector, c05_duckdb_vss, c06_sqlite_vec, c07_lancedb, c08_qdrant_cpu / c08_qdrant_gpu; all pass the gate at M and real in unmeasured runs. ef_search 100 for all HNSW engines (64 left real/none at 0.958–0.963).
  - Report: Track C section + stage/latency/quality table. COMPAT: 6 new rows (Qdrant GPU build ✅, sqlite-vec NULL, LanceDB nprobes, DuckDB threads …).
- **c02_cuvs_cagra** done after the download: the cuvs-cu13 wheel lacks the libcudart dependency → explicit `cuda-toolkit[cudart]==13.5.0` (COMPAT). Passes at M and real (both paths: exact-on-GPU for selective filters, CAGRA + bitset tested with `--brute-max 100`). `task ci` green (logs/ci_trackc.log).
- **Pending:** c11 Tantivy + `c.hybrid` (BM25 + RRF, compound words), concurrency-16 QPS.
- **Measured session (user, quiet box):** c00/c01/c02/c03/c05/c06/c07/c08×2 × M/real = 108 runs, ≈ 60 min (estimate from unmeasured timings).

## 2026-11-08 — Track C `c.dense` gemessen (Session 75d4d73c)
- `scripts/quiet_box.sh measure run` mit 9 Kandidaten × M, real × (1 Warm-up + 5): 108/108 ok, 59 min; `task report` neu erzeugt (649 Läufe).
- Alle bestehen das Recall-Gate (≥ 0,95 je Filterklasse); nDCG/hit liegen an der Obergrenze der exakten Suche (LanceDB M recall 0,983, nDCG trotzdem 0,8476).
- M: c00 exakt GPU 2,98 s / 108 J, c02 CAGRA 3,11 s, c01 FAISS 14,7 s; Server-/DB-Lösungen 25–122 s (pgvector-Indexbau 107 s dominiert).
- real: c00, c01, c02 gleichauf um 2–2,4 s; Qdrant-GPU-Indexbau bei kleinen Segmenten langsamer als CPU (11,4 s vs. 4,6 s).
- Läufe < ~3 s sind „low confidence“ (Energie-Abtastung), im Bericht markiert.
- Nächstes: c11 Tantivy + `c.hybrid` (RRF), Konkurrenz-16-QPS, optional Track-B-Wiederholung mit Retrieval-Gate; dann Phase 7.

## 2026-11-08 — Phase 6 Teil 2: Konkurrenz 16, Tantivy (`c.lexical`), Hybrid (`c.hybrid`) gebaut (ungemessen)
- **Konkurrenz 16:** neue Stufe `query_c16` in allen 9 `c.dense`-Kandidaten (16 Client-Threads, je eigenes Handle),
  Ergebnis `<out>.c16.npy`, Verifier prüft es mit demselben Recall-Gate; `extra.throughput` (QPS c1/c16).
  c02 bekam pro Thread einen eigenen cuVS-Handle + Stream-Sync; CAGRA ist auch einzeln nicht deterministisch (COMPAT).
- **Referenz** `zj_reference.track_c_lex` (Tantivy-Analyzer nachgeschrieben, snowballstemmer 3.1.1, BM25, RRF k 60),
  `zj_eval.track_c_lex` (Eingabe `queries.text.jsonl`, Referenzdateien, Kompositum-Teilmenge: 35 Ein-Wort-Anfragen bei M),
  `zj_eval.verify_track_c_lex` (bm25: gleichstandsfester Recall; hybrid: Dense-Bein-Recall, Fusion nachgerechnet,
  lexikalisches Bein Plausibilität). `task trackC:lexref SCALE=…`.
- Referenzqualität: M nDCG@10 BM25 0,777 / dense 0,848 / hybrid 0,831; Kompositum BM25 1,0 (die Meldungen enthalten
  das Kompositum oft wörtlich); real hit@10 0,997 / 0,999 / 1,000.
- **Neu:** c11_tantivy (Rust, `c.lexical`), c03_pgvector_hybrid (ts_rank, kein BM25), c05_duckdb_hybrid (fts BM25),
  c06_sqlite_hybrid (FTS5 bm25), c07_lancedb_hybrid (nativ, RRFReranker), c08_qdrant_hybrid (nativer BM25-Textindex +
  RrfQuery). Alle bestehen ungemessen bei real und M. ADR 0017, spec/track_c.md, COMPAT, Bericht je Task getrennt.
- Ungemessen bei M alle 15 ok (logs/verify_trackC2_M.log); `task ci` grün (logs/ci_trackc2.log).
- Nächstes: gemessene Session (15 Kandidaten × M, real = 180 Läufe), danach Phase 7 (Track D).

## 2026-11-09 — Track C komplett gemessen (Session 82fdef07: dense + Konkurrenz 16, lexical, hybrid)
- `scripts/quiet_box.sh measure run`, 15 Kandidaten × M, real × (1 Warm-up + 5): 180/180 ok, 2 h 50 min; `task report` (829 Läufe).
- **c.dense (ersetzt Session 75d4d73c):** M Wall c00 4,48 s, c02 6,24 s, c01 16,9 s; QPS c1 → c16 (M): c02 1 548 → 328
  (16 Threads ohne Batching bremsen die GPU), c03 pgvector 98 → 760, c05 DuckDB 144 → 767, c08 Qdrant 244 → 517,
  c06 sqlite-vec 16 → 79. Alle Recall-Gates bestanden (c16 eingeschlossen).
- **c.lexical:** c11_tantivy M 1,49 s / 26,8 J brutto, p50 0,51 ms, BM25-Recall 1,0000 (real 0,9995).
- **c.hybrid:** M Wall: Qdrant 66,6 s < LanceDB 101,8 s < pgvector 125,1 s < DuckDB 134,6 s < SQLite 184,2 s; p50 Qdrant
  6,6 ms, sonst 26–94 ms. nDCG@10 M 0,804 (pgvector, ts_rank) bis 0,832 (LanceDB), **unter Dense allein (0,848)**:
  auf den synthetischen Daten schadet die lexikalische Fusion; auf real hit@10 1,000 vs. dense 0,999 (Decke).
  Kompositum-Teilmenge überall 1,0. Fusion-Übereinstimmung Qdrant 0,972 (M), sonst ≥ 0,9997.
- Nächstes: Phase 7 (Track D, LLM-Extraktion); optional Track-B-Wiederholung mit Retrieval-Gate.

## 2026-11-09 — README-Ergebnisblock + Abbildungen (generiert)
- Neues Modul `zj_analysis.readme`: `task report` schreibt jetzt zusätzlich einen Block zwischen
  `<!-- zj:results:begin/end -->` in README.md/README.de.md (je Track die größte vollständig gelaufene Skala:
  A L, B M, C dense/hybrid/lexical M; Rang, Wall s, × schnellster, Energie J brutto) und SVG-Abbildungen in
  `reports/figures/` (Wall-Zeit log mit Energie-Label; QPS c1 vs. c16). Keine Zahl von Hand (Regel 3).
- Report-Helfer öffentlich benannt (`fmt_num`, `summaries`, `accumulate`, `median_of`); Test für Block +
  Idempotenz. analysis: ruff, pyright, pytest grün.

## 2026-11-09 — Phase 7 Teil 1: Track D (`d.extract`) gebaut, Modelle geladen (ungemessen)
- Spezifikation `spec/track_d.md`, ADR 0018; Schema `spec/schema_stoermeldung.json`; Referenz/Bewertung
  `zj_reference.track_d` (strenge JSON-Gültigkeit, exakt/Token-F1, macro-F1), `zj_eval.track_d prepare`
  (Prompts 0/3-shot mit Katalogen aus spec/lexicon), `zj_eval.verify_track_d` (Gate: Gültigkeit ≥ 99,5 %,
  kein Reasoning). Skalen `D2k` (2 000) und `dev` (300, nur Konkurrenz-Sweep). `task trackD:prep`.
- Client `zj_llm.extract` für Ollama 0.40.15 (Docker, Modelle unter models/ollama), llama.cpp b12169, vLLM 0.33.0;
  gleiche Nachrichten, temperature 0, max_tokens 256, JSON-Schema-Decoding, Reasoning aus; Stufen load/extract/
  write/ttft. 12 Sweep-Kandidaten (3 Engines × N 1/4/16/64, Qwen3.5-9B Q4 bzw. AWQ) in config/contestants.yaml.
- Modelle (`scripts/fetch_models_d.sh`, gepinnte Revisionen, alle Apache-2.0): Qwen3.5-9B Q4/Q8, Qwen3.5-4B Q4,
  Gemma 4 12B Q4/Q8, Gemma 4 E4B, EuroLLM-9B-2512 Q8, Teuken-7B Q8, Ministral 3 14B Q4, gpt-oss-20b MXFP4;
  vLLM: Qwen3.5-9B AWQ/FP8, gpt-oss-20b. Ollama: qwen3.5:9b.
- Ungemessene Proben (dev, N = 16, nur Richtwerte): llama.cpp Qwen 9B ≈ 2,7 Zeilen/s (N = 64 gleich: 2,76,
  CPU-gebundenes Grammar-Sampling?), vLLM AWQ 3,9 (N = 64: 4,6), Ollama 1,4; macro-F1 0,93–0,96, Gültigkeit 1,0.
  Gemma 4 12B übernimmt teils den Klammerzusatz des Katalogs in `komponente` (Prompt präzisiert, bleibt teils).
- Bericht: Abschnitt Track D mit J/Zeile, Tok/s, TTFT, VRAM, macro-F1 und Gewinner-Gate; `task ci` grün.
- Offen (Erlaubnis nötig): `models/vllm/gpt-oss-20b/original` + `metal` (≈ 13 GB Duplikat durch zu weites
  Include-Muster) löschen.
- Nächstes: gemessener Sweep auf `dev` → N je Engine wählen → D2k-Matrix (Modelle × Engines, 0/3-shot, Q4/Q8).
- 2026-11-09: `models/vllm/gpt-oss-20b/original` (13 GB Duplikat) mit Erlaubnis gelöscht; Include-Muster korrigiert.
- Kompatibilitätsproben (100 dev-Zeilen, N = 16, ungemessen): Qwen3.5-4B Q4 0,932; Gemma 4 E4B 0,973 (nach
  `--reasoning-budget 0`); Qwen3.5-9B Q8 0,964; vLLM FP8 0,961 (mit text-only + 0,95 Speicher); gpt-oss-20b llama.cpp
  Gültigkeit 0,97; EuroLLM/Teuken ohne Grammatik (llama.cpp-Sampler-Fehler) ungültig; vLLM gpt-oss und Gemma 12B Q8
  bei N = 16: VRAM zu knapp. Details docs/COMPAT.md.

## 2026-11-09 — Track D Konkurrenz-Sweep gemessen (Session 775e9db7, dev, 12/12 ok, 60 min)
- Erster Versuch (03:10) brach ab: Leerlauf-Baseline 3× nicht ruhig (loadavg 1,16), nichts aufgezeichnet; Wiederaufnahme ok.
- Beste N nach J/Zeile: vLLM AWQ N = 64 (4,67 Zeilen/s, 32,0 J/Zeile, macro-F1 0,938), llama.cpp Q4 N = 16 (2,94 Zeilen/s,
  42,7 J/Zeile, 0,958; N = 64 nicht schneller, 48,6 J), Ollama N = 64 (1,55 Zeilen/s, 62,5 J/Zeile, 0,950; p99 109 s).
  N = 1 kostet 190–233 J/Zeile. Alle 12 Gültigkeit 1,0. Qualität praktisch unabhängig von N.
- D2k-Matrix angelegt (15 Konfigurationen: llama.cpp 11 inkl. 3-shot/Q8/EuroLLM+Teuken ohne Grammatik, vLLM 3, Ollama 1);
  3-shot-Prompts ≤ 1 496 Token passen in 2 048 (ungemessene Probe). Verifier legt completion_tokens_median nun in quality.

## 2026-11-09 — Track D Hauptmatrix gemessen (Session 2501fd1d, D2k, 45 Läufe, 8 h 52 min)
- 35 ok, 9 disqualifiziert (gpt-oss-20b Gültigkeit 0,962 – Reasoning ohne Aus-Schalter; EuroLLM 0,941 und Teuken 0,000 –
  ohne Grammatik), 1 fail (vLLM FP8 Wdh. 1: `max_num_seqs 64 > 37 Mamba-Cache-Blöcke`, Wdh. 2/3 ok → n = 2). `task report`.
- Alle gewerteten Konfigurationen 100 % gültiges JSON und im Gewinner-Gate (bestes macro-F1 0,9712 → Schwelle 0,874).
- Bestes macro-F1: llama.cpp Qwen3.5-9B Q4 3-shot 0,9712 (35,9 J/Zeile, 3,38 Zeilen/s). Geringste Energie:
  Gemma 4 E4B 3-shot 23,0 J/Zeile (0,9615, 4,2 GB VRAM) ≈ vLLM AWQ 3-shot 23,3 J/Zeile (0,9640, schnellste: 6,24 Zeilen/s).
- 3-shot ist besser **und** billiger (kompaktes JSON wie in den Beispielen: 67–68 statt 84 Token/Zeile).
  Q8 bringt gegenüber Q4 nichts (0,949 vs. 0,953, +15 % Energie). Ollama gleiche Gewichtsklasse: 1,91 Zeilen/s,
  43,5 J/Zeile vs. llama.cpp 3,09 / 38,8. Gemma 4 12B: 76 J/Zeile, `komponente` 0,68 (Klammerzusatz).
- Bericht: Zeilen/s mit 2 Nachkommastellen unter 100/s.
- Offen in Phase 7: d10 GLiNER und d11 Kaskade (PLAN §10, Core); dann Phase 8.

## 2026-11-09 — Phase 7 Teil 3: d10 GLiNER + d11 Kaskade gebaut (ungemessen)
- Neue uv-Umgebung `contestants/python/ner` (torch 2.14.1 cu130, transformers 5.22.2, gliner 0.2.30);
  SauerkrautLM-GLiNER (MIT, `models/ner/`). `zj_ner.fields` (Stufe 0 Track-A-Regeln, Stufe 1 GLiNER → Katalognamen),
  `d10_gliner` (ohne LLM), `d11_cascade` (Regeln + Gemma 4 E4B nur für 4 semantische Felder, verkleinertes Schema).
- Referenz: Prompt/Schema je Feldmenge (`SEMANTIC_FIELDS`), neue Variante `prompts.semantic3shot.jsonl` und
  `spec/schema_stoermeldung_semantic.json`; bestehende Prompts bytegleich. ADR 0019, spec/track_d.md §7.
- Ungemessen (zj verify, D2k): d10 20 s, macro-F1 0,775 (GLiNER löst 36 % schadensart / 20 % ursache / 5 % massnahme);
  d11 392 s, 5,2 Zeilen/s, macro-F1 0,966, Gültigkeit 1,0.
- Nächstes: gemessene Session d10 + d11 (D2k, 3 Wdh.), dann Phase 8.
- 2026-11-10: quiet_box: zj-*-Container werden nicht mehr als Dienst gesichert (Restore scheiterte an einem gelöschten zj-d01-ollama und blockierte jeden Start); Restore startet Container einzeln.

## 2026-11-10 — Phase 7 abgeschlossen: d10 + d11 gemessen (Session 98fa9eac, D2k, 6/6 ok, 22 min)
- d11 Kaskade (Regeln + Gemma 4 E4B nur für 4 semantische Felder): **17,2 J/Zeile** (vs. 23,0 Gemma E4B 3-shot mit
  vollem Schema, −25 %), 5,25 Zeilen/s, 57 statt 93 Token/Zeile, macro-F1 **0,966** (Gewinner-Gate ✓; bestes LLM
  0,971 Qwen3.5-9B 3-shot bei 35,9 J/Zeile). Damit sparsamste gewertete Konfiguration von Track D.
- d10 GLiNER ohne LLM: 18,9 s, 0,38 J/Zeile (≈ 45× weniger als d11), macro-F1 0,775 → Gewinner-Gate ✗
  (schadensart 0,34). Nicht-LLM-Untergrenze.
- Kandidat für Phase 8 (Extraktion im End-to-End-Stack): d11 Kaskade; Alternative bei Qualitätsvorrang Qwen3.5-9B 3-shot.
- Nächstes: Phase 8 (Track E End-to-End-RAG, WINNER.md, Produktionsarchitektur) → Gate 2.

## 2026-11-10 — Phase 8 Teil 1: Track E (`e.rag`) gebaut, auf M ungemessen geprüft
- spec/track_e.md, ADR 0020; `zj_eval.track_e` (Fragen ohne Gold, zentrale Router-/Antwort-/Label-Prompts + Schemas,
  programmatische Bewertung, Verifier); Task `e.rag`, `task trackE:prep`.
- Neue uv-Umgebung `contestants/python/e2e`: `zj_e2e.sqlsafe` (sqlglot: genau ein SELECT auf `meldungen`, keine
  Datei-/Seiteneffekt-Funktionen, Transpilieren je Dialekt), `zj_e2e.stores` (FAISS+sqlite3, DuckDB vss+fts, Postgres
  pgvector+FTS mit SELECT-only-Rolle + statement_timeout, Qdrant hybrid + DuckDB-SQL), `zj_e2e.pipeline`
  (Track-A/B-Kandidaten per CLI, Schadensart per Destillation, ein Router-Aufruf je Frage mit Self-Query-Filtern).
  Pipelines e01 (pandas/ST fp32/FAISS/Ollama), e02 (Polars/ORT/DuckDB/llama.cpp), e03 (Rust/TEI/Postgres/vLLM),
  e05 (aus results/zj.duckdb: a17 → b06 → c08 Qdrant → vLLM). e04 (reines Rust) noch offen.
- Fehler auf dem Weg (ungemessen gefunden, behoben): Bewerter las Ein-Zeilen-Tabellen nicht („welches Werk“);
  Text-Antwortweg brauchte `meldung_id`; feste k = 50 ohne Komponenten-/Schadensart-Filter (semantic F1 0,28 → 0,76);
  ZJMARK-Zeilen der Unterprozesse kamen verspätet beim Harness an (jetzt herausgefiltert).
- Ungemessen auf M (300 Fragen): e02 Gesamt 0,714, e03 0,747 (553 s), e05 0,744 (417 s); Router 100 %, keine SQL
  abgelehnt/fehlgeschlagen. Restfehler sind echt: Ursache/Maßnahme nicht als Katalogwert (Lookup 0,6–0,8),
  Summen über geschätzte Schadensart (Klassifikator 0,885 auf LLM-Etiketten), 294/100 000 unlesbare Daten.
- Bericht: Abschnitt Track E (Time-to-RAG s/Wh, J/Frage, Typ-Werte, Router, Zitate, Gate); `reports/WINNER.md` +
  `WINNER.en.md` generiert (Schnellster, Effizientester, Pareto-Front, Produktionsregel 5 mit Diensten).
- e01 erster Probelauf: Zeitüberschreitung beim Etikettieren → Ursache: Ollama verteilt Qwen3.5-9B bei 64 × 4 096 Token
  stillschweigend 25 %/75 % auf CPU/GPU. Neu: `gpu_check` (Ollama `ollama ps` muss „100% GPU“ zeigen, sonst Abbruch,
  Regel 6); e01 mit N = 16 → ungemessen auf M 1 195 s, Gesamt 0,669, 100 % GPU. Track-D-Ollama-Läufe (64 × 2 048)
  nachgeprüft: 100 % GPU.
- `task ci` grün (logs/ci_tracke.log). Nächstes: gemessene Session Track E auf L (4 Pipelines × 3 Wdh., ≈ 11 h).
- 2026-11-10: e04_pure_rust vor Gate 2 ausgelassen (Entscheidung des Nutzers), in ADR 0020 und WINNER.md vermerkt.

## 2026-11-11 — Track E: Vorbereitung der gemessenen Session nach Aufräumen
- Session `ba8fd0f7` (2026-11-10): e02 und e01 auf L nach 1 800 s abgebrochen (dnf, globales `timeout_s`), dann
  abgebrochen/Neustart. Harness: neues optionales `timeout_s` je Wettbewerber (Go, Test); Track E 10 800 s.
- Nutzer hat Docker aufgeräumt: Images vLLM, Ollama, Qdrant, pgvector fehlten → mit gepinnten Digests neu geladen
  (vLLM ≈ 35 GB mit Erlaubnis). TEI und llama.cpp unverändert (Digest geprüft). Zwei liegengebliebene Container
  (`zj-b06-tei`, `zj-d02-llamacpp`, nach dem Neustart) entfernt. quiet_box-Restore übersprang die gelöschten Dienste.

## 2026-11-11 — Phase 8 abgeschlossen: Track E gemessen (Session 444b1e05, L, 12/12 ok, 9 h 18 min) → Gate 2
- Wall-Zeit (Median, 1 Mio. Zeilen + 300 Fragen): e03 Postgres 1 740 s / 247,9 kJ, e02 DuckDB 1 864 s / 288,6 kJ,
  e05 best-of-tracks 1 954 s / 261,5 kJ, e01 klassisch 5 330 s / 957,8 kJ. Alle im Gewinner-Gate (Gesamt 0,697–0,757).
- Time-to-RAG: e02 1 646 s (schnellster Index), e03 1 672 s / 66,4 Wh (sparsamster); Einbetten = 80 % der Energie.
  J/Frage: vLLM-Pipelines 26–27 J, llama.cpp 65 J, Ollama 89 J. 2 400 Router-SQL, 0 abgelehnt/fehlgeschlagen/unsicher.
- e05 (Track-Sieger zusammengesetzt) verliert gegen e03: Qdrant-Index bei 1 Mio. Punkten 729 s vs. pgvector 505 s.
- Bericht: Klassifikator-Spalte liest jetzt `result.extra` (Verifier künftig auch aus `ingest.json.label`).
- `reports/WINNER.md` (Regel 5: e03, keine Abwägung nötig), `docs/ARCHITEKTUR.md`, ADR 0021 (vorgeschlagen).
- **Gate 2: warten auf Bestätigung der Produktionsarchitektur vor Phase 9.**
- 2026-11-11: **Gate 2 bestätigt** (Architektur e03, ADR 0021 angenommen) → Phase 9.

## 2026-11-11 — Phase 9 Teil 1: GPU-Teilung geprüft, Go-Modul `services` angelegt
- GPU-Teilung TEI + vLLM (ungemessen): 12,8/16,3 GB, KV 38 550 Token bei vLLM 0,70; LLM 401 → 343 Tok/s während
  gleichzeitigen Einbettens; TEI-Grenze ≈ 512 gleichzeitige Eingaben (HTTP 429). Nachtrag in ADR 0021.
- `services/` (Go-Modul `zeilenjoule/services`, Abhängigkeiten ≥ 2 Wochen alt gepinnt): `internal/sqlguard`
  (pg_query_go, ADR 0022, Tests + Fuzz), `api/openapi.yaml` (ingest, jobs, extract, search, ask mit SSE, healthz,
  readyz) → `internal/api` per oapi-codegen v2.8.1 (`go tool`, strict std-http server).
- Nächstes: Clients (TEI, vLLM), Postgres-Schema + Migrationen, RAG-Kern (Router → SQL/Suche → Antwort),
  Gateway-Handler + Middleware, Worker (River-Jobs: Rust-Parser → Einbetten → COPY → Schadensart), Compose.

## 2026-11-11 — Phase 9 Teil 2: Dienste lauffähig, Compose-Stack, Beobachtung (alles ungemessen)
- Go-Dienste: Clients TEI (geteilte Drossel 4 × 128, Retry bei 429/5xx) und vLLM (JSON-Schema, Denken aus, Semaphore
  32); Schema-Migrationen (`docs` halfvec-HNSW + GIN, Sicht `meldungen`, Rollen `zj_reader`/`zj_ask`, `ingest_jobs`,
  `classifier`) + River-Migrationen; RAG-Kern = Port von `answer_all`/`PostgresStore.search`; Klassifikator-Port
  (Softmax/Adam); Ingest-River-Job (Rust a16 → Parquet via arrow-go → TEI → Schadensart → COPY/Upsert);
  Gateway (strict OpenAPI-Handler, SSE, API-Key, Rate-Limit je Schlüssel, Body-/Zeitlimits, RFC-9457, /metrics).
- Prompts: `task services:prompts` erzeugt `services/internal/prompts/prompts.json` aus den Benchmark-Buildern
  (Benchmark-Prompt byte-gleich geprüft; Produktions-Router nennt PostgreSQL statt DuckDB, ADR 0023).
- Images (distroless/cc, nonroot, Digests): gateway 101 MB, worker 118 MB (inkl. Rust-Parser), exporter 59 MB.
  `deploy/compose` mit Profilen `gpu`/`observability`, Prometheus (Recording-Rules J/Frage, J/Zeile, Alarme),
  Grafana-Dashboard (15 Panels, alle Abfragen gegen den laufenden Stack geprüft).
- Ergebnis Smoke-Test (`task smoke`, S = 10 000 Zeilen, ungemessen): alle Endpunkte ok; Erst-Import 175 s
  (davon 132 s LLM-Etiketten für 2 000 Zeilen, Klassifikator-Holdout 0,89), Folge-Import mit gespeichertem
  Klassifikator 19 s.
- Gefunden + behoben: Docker maskiert `/sys/devices/virtual/powercap` → `zj exporter --powercap` + Mount außerhalb
  /sys; CPU-Energie nur Domäne `cpu_pkg` (core/uncore sind enthalten); TEI-Metriken auf dem API-Port; Re-Import
  überschrieb LLM-Etiketten → Upsert behält sie bei gleichem Text (Integrationstest); govulncheck fand 9 erreichbare
  Schwachstellen (x/net, brotli, compress, kin-openapi) → auf behobene Versionen (alle ≥ 2 Wochen alt).
- Tests: Unit (sqlguard, classifier, httpapi), Integration gegen pgvector-Container (`task test:services:int`:
  Migration idempotent, Upsert, Rollen-Grenzen, statement_timeout, Suche, Router-Pfade). `task ci` grün.
- ADR 0023, `docs/RUNBOOK.md`. Nächstes: Lasttest (300 Track-E-Fragen gegen den Stack, Latenz/J je Frage, gemessen
  über quiet_box), Qualitätsprüfung des PostgreSQL-Routers auf L, Helm/k3s, CI-Scans (hadolint, trivy, syft,
  kubeconform, gitleaks), `docs/CI.md`.

## 2026-11-13 — Phase 9 Teil 3: k3s/Helm, lokale CI/CD mit Scannern, e06 als Track-E-Teilnehmer (ungemessen)
- **Helm-Chart** `deploy/helm/zeilenjoule` (ADR 0024): Postgres-StatefulSet (+ Init-Skript und Monitoring-Dateien
  per Symlink aus Compose, keine Kopien), Gateway/Worker (Migration als Init-Container), TEI/vLLM mit
  `RuntimeClass nvidia` und je einem `nvidia.com/gpu` (Time-Slicing ×2, `deploy/k3s/nvidia-device-plugin.yaml`),
  Modelle über statisches hostPath-PV (Pods bleiben PodSecurity „restricted“), NetworkPolicies (Default-Deny,
  9 Policies), Backup-CronJob (`pg_dump -Fc`, 7 Stände), optionales Monitoring (Prometheus/Grafana/Exporter) im
  eigenen Namespace, `helm test`, `values-prod.yaml` (HPA, PDB, Ingress/TLS, RWX). helm lint/kubeconform
  (77 Ressourcen, 1.36.5 strict) und Server-Dry-Run gegen k3s: gültig.
- **Nicht ausgerollt:** Namespaces + Time-Slicing sind clusterweite Änderungen; der Auto-Modus hat sie blockiert →
  Nutzer entscheidet (`task k3s:cluster-setup`). Lokale Registry `127.0.0.1:5000` getestet: k3s zieht ohne
  Konfiguration (kein sudo). Sicherung des ursprünglichen Device-Plugin-DaemonSets im Scratchpad.
- **CI** (`docs/CI.md`): `task ci` = Lint/Tests + hadolint, helm lint + kubeconform, gitleaks (dir), trivy config
  (MEDIUM+), trivy fs auf alle Lockfiles (ersetzt pip-audit/cargo audit); `task ci:release` = + Images, trivy image,
  syft-SBOM (SPDX 2.3 nach `dist/sbom/`). Alle Werkzeuge als digest-gepinnte Container, Versionen ≥ 2 Wochen alt.
  Befunde behoben: numerisches `USER 65532:65532`, seccomp für Exporter/Device-Plugin; trivy fs fand 3 HIGH in
  `services/go.mod` (kin-openapi, x/net; govulncheck: nicht erreichbar) → v0.149.0 / v0.62.0 (≥ 2 Wochen alt).
  Images: 0 HIGH/CRITICAL. Ausnahmen begründet in `.trivyignore.yaml` (Exporter root/hostPath, NVIDIA-Plugin).
  `task images` baut ohne `.env` (`scripts/build_images.sh`, OCI-Labels).
- **e06_stack_go** (ADR 0025): Go-Client `services/cmd/zj-e2e` misst den Compose-Stack als Track-E-Teilnehmer (gleiche
  CSV, gleiche 300 Fragen, gleicher Prüfer; Stufen load/ingest/answer/write; eigenes Compose-Projekt, frische Volumes).
  Dabei gefunden und behoben:
  - Erst-Import ließ HNSW zeilenweise wachsen → bei leerer Tabelle Indizes verwerfen und danach parallel bauen
    (e03-Verfahren; Integrationstest: Definition byte-gleich zur Migration).
  - API meldete unbrauchbare Router-Antworten als `search` (hätte Router-Genauigkeit geschönt) → `route: "none"`.
  - Tabellen verloren die Spaltenreihenfolge (Go-Map) → `columns` + Zeilen als Arrays.
  - Neues Feld `sql_status`.
- **Funktionsprüfung auf M, ungemessen** (nicht über das Harness, Zahlen nur als Plausibilität):
  - e06 läuft vollständig durch (Ingest 100 000 Zeilen über den Bulk-Pfad, 300 Fragen, Stack abgebaut). Ausgaben:
    `logs/p9_e06_func/`.
  - Vergleich mit einem ungemessenen e03-Lauf auf denselben M-Fragen (`logs/p9_e03_M_func/`): Gesamt 0,727 vs. 0,744,
    300/300 gleiche Routen, 184/200 SQL zeichengleich (Rest gleichwertig). Die Differenz liegt bei Aggregationen über
    die geschätzte Spalte `schadensart`: gleiche SQL, andere Summen, weil die 2 000-Zeilen-Stichprobe für die
    LLM-Etiketten in Go (math/rand) anders gezogen wird als in numpy.
  - **Der PostgreSQL-Router ist damit gleichwertig** (offener Punkt aus ADR 0023).
- **Nächstes (Nutzer):**
  1. Gemessene e06-Session auf L:
     `scripts/quiet_box.sh measure run --id e06_stack_go --scale L --reps 3 --warmup 0`.
     Dauer ungemessen geschätzt 1,5–2 h (e03: 1 740 s je Lauf).
  2. Optional `task k3s:cluster-setup` und `task images && task k3s:up`, dann `task k3s:test` und `task k3s:smoke`.
- **Danach:** Bericht um e06 ergänzen (`task report`), Lastkurve (Latenz gegen Parallelität), README-Abschnitt
  Phase 9.

## 2026-11-13 — Phase 9 Teil 4: e06 gemessen, Bericht, Lastkurve

**Erledigt**
- Gemessene Session e06_stack_go (Skala L, 3 Wiederholungen, Nutzer via quiet_box): alle 3 `ok`, Prüfung bestanden,
  Energie-Konfidenz hoch; Gegenprobe des NVML-Zählers −12…−19 % = bekannte Eigenschaft der Karte (METHODIK, ADR 0006).
  Zahlen nur im generierten Bericht (`reports/WINNER.md`, Abschnitt Phase 9; `reports/REPORT*.md`).
- Analyse: Produktions-Stacks (`PRODUCTION_STACKS`) sind kein Gewinnerkandidat mehr (sonst hätte sich der Stack
  selbst zum Gewinner erklärt); neuer WINNER-Abschnitt „Produktions-Stack gegenüber dem Gewinner als Skript“.
  Time-to-RAG für Pipelines ohne Einzelstufen (e06: alles in `ingest`). README-Ergebnisblock enthält jetzt Track E.
- README: Phase-9-Abschnitt (de/en), Status ehrlich „gebaut, Lastkurve offen“.
- Lastkurve (ADR 0026): `services/internal/benchstack` (aus zj-e2e herausgelöst), `services/cmd/zj-load`
  (offene Last, Stufe je Rate, SSE „erstes Ereignis“), Task `e.load` + Prüfer `builtin:e.load` (Harness, Tests),
  Teilnehmer `e07_stack_load`, Auswertung `analysis/…/load_curve.py` (Knie, SLOs, J/Anfrage) im Bericht.
- Funktionstest (ungemessen, M) fand einen echten Fehler: generic plan der lexikalischen Suche (≈ 3× langsamer).
  Korrigiert mit `plan_cache_mode = force_custom_plan` (identische Treffer). Images neu gebaut.

**Als Nächstes**
- Nutzer: `scripts/quiet_box.sh measure run --id e07_stack_load --scale L --reps 3 --warmup 0` (≈ 2¼ h), dann `task report`.
- Danach: Entscheidung zur lexikalischen Stufe auf L (siehe ADR 0026, offen), Phase 10.

**Offene Fragen**
- Suche-SLO auf L: Rangfolge der lexikalischen Stufe ändern (Qualität/Vergleichbarkeit) oder Ziel anpassen?

## 2026-11-13 — Phase 9 Teil 5: Lastkurve gemessen, Phase 9 abgeschlossen
- Nutzer hat `e07_stack_load` auf L gemessen (Sitzung 356d44ab, 3 Läufe, je ≈ 2 890 s): alle `ok` und geprüft
  (20 295 Anfragen je Lauf, 1 M Zeilen ingestiert). `task report` neu erzeugt; Lastkurve + SLO-Tabelle im Bericht.
- Ergebnis: Knie Suche 5 req/s (Sättigung ≈ 11 ok/s), Fragen 2 req/s (≈ 4,5 ok/s). SLO Ingest ✓, Suche ✗,
  erstes Ereignis ✗. Deutung und Optionen als Nachtrag in ADR 0026. README: Phase 9 ✅ mit verfehlten SLOs offen benannt.
- Nächstes: Entscheidung des Nutzers, ob die SLOs nachgebessert werden (lexikalische Rangfolge, schnellerer Router),
  sonst Phase 10 (Portfolio: Abbildungen, Blog-Entwurf, `docs/BEWERBUNG.md`, `task demo`, Demo-Seite).
