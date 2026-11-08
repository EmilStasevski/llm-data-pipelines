# ZeilenJoule — Project Plan & Specification

*Version 0.1 · written 2026-11-06 · for the local Claude Code agent. Read together with `CLAUDE.md`.*

> **One-line pitch (DE):** *Wie schnell und mit wie viel Energie wird aus einem 1-Mio-Zeilen-Industrieexport eine fragbare Wissensbasis? pandas vs. Polars vs. Rust vs. GPU vs. Datenbanken vs. lokale LLMs, gemessen in Sekunden **und** Joule auf einer RTX 5060 Ti.*
>
> **One-line pitch (EN):** *How fast, and at what energy cost, does a 1M-row industrial export become a queryable knowledge base? Every layer of a row-wise RAG pipeline is benchmarked in seconds **and** joules on one consumer GPU box. The winning stack then gets a production-grade Go/DevOps setup.*

---

## 0. TL;DR for the agent

1. **Phase 0:** audit the machine and write the compatibility matrix. **Stop at Gate 1.**
2. **Phases 1–3:** scaffold the repo, build the **Go measurement harness** (time + GPU/CPU energy), build the **deterministic German NRW data generator** with gold labels, and fetch a few real open datasets.
3. **Phases 4–8:** run five benchmark tracks.
   - **A** dataframe/row processing
   - **B** embeddings
   - **C** databases/vector search
   - **D** LLM row-wise extraction
   - **E** end-to-end RAG

   Every run is verified against a reference before it counts. Then pick the winners. **Stop at Gate 2.**
4. **Phase 9:** wrap the winning stack in a Go production service: API gateway, workers, observability with **energy metrics**, Docker/compose, k3s + Helm, local CI, load tests.
5. **Phase 10:** portfolio polish. German + English README, report and a blog-post draft, aimed at Werkstudent applications in NRW industry.

---

## 1. Goals and research questions

**Primary question:** For **row-wise RAG** over German industrial tabular data, which tool or stack is **fastest** at each pipeline stage and end-to-end on a single RTX 5060 Ti 16 GB box, and what does it cost in **energy**?

Sub-questions (each becomes a section of the final report):
- RQ1: How large is the gap between naive pandas (`iterrows`) and vectorized pandas, Polars (eager/lazy/streaming/GPU), DuckDB, and hand-written Rust for parsing and cleaning messy German exports? Measure it in rows/s **and** J/row.
- RQ2: Does GPU acceleration (cudf.pandas, Polars GPU, cuVS) pay off **in energy**, not just time, at 10k / 100k / 1M / 10M rows? Where is the break-even?
- RQ3: Which embedding runtime (PyTorch, ONNX Runtime, TEI, vLLM, llama.cpp, Rust candle/ort) gives the most rows/s and rows/J for German text on sm_120?
- RQ4: Which database gives the best latency, recall and energy for **filtered** vector search plus German lexical search? Candidates: PostgreSQL+pgvector, DuckDB vss, SQLite sqlite-vec, LanceDB, Qdrant, cuVS, Tantivy, and others.
- RQ5: Which local LLM that fits in 16 GB, on which engine, extracts structured fields from German maintenance texts with the best **F1 per joule**? How much energy does a **cascade** (regex → GLiNER → LLM) save?
- RQ6: End-to-end. What is the *Time-to-RAG* and *Energy-to-RAG* for a fresh 1M-row export, and what does one answered question cost in time and joules?
- RQ7 (ops): What does the fastest stack cost to operate (moving parts, memory, idle watts)? Is the simplest stack (e.g. Postgres-only) "fast enough"?

**Non-goals (for now):** training or fine-tuning models, multi-GPU, cloud deployment, GitHub/CI on GitHub, UI polish beyond a small demo page.

---

## 2. What "row-wise RAG" means here

The retrieval unit is a **row (record)**, not a chunk of a long document. Industrial data mostly lives in tables: SAP-PM fault notifications (*Störmeldungen*), quality notifications (*Q-Meldungen*/8D), inspection logs (*Prüfprotokolle*), tenders, job ads. Each row has structured columns plus one or more German free-text columns.

Pipeline stages, each one measured separately:

| # | Stage | Example |
|---|---|---|
| S1 | **Ingest & decode** | cp1252/UTF-8 CSV with `;`, decimal comma, mixed date formats, mojibake |
| S2 | **Normalize & rule-extract** (row-wise) | Parse `1.234,56 €`, `ca. 2 Std.`, `12.3.26`. Expand `i.O./n.i.O.` and similar. Regex/dictionary extraction of equipment no., material no., safety flag, component |
| S3 | **Row → document serialization** | `Werk: Duisburg \| Anlage: Walzstraße 2 \| Datum: 2026-03-12 \| Meldung: …` |
| S4 | **Embed** | dense (and optionally sparse) vectors per row |
| S5 | **Store & index** | vector index + metadata columns + German full-text index |
| S6 | **Retrieve** | metadata pre-filter (plant, date range, priority) + dense + BM25 hybrid, optional rerank |
| S7 | **LLM row-wise extraction** | free text → JSON fields (component, failure mode, cause, action, downtime, safety-relevant) |
| S8 | **Answer** | question → router (SQL for aggregations vs. vector for semantic search) → answer citing row IDs |

---

## 3. Hardware and software landscape (checked 2026-11-06, re-verify in Phase 0)

### 3.1 GPU facts
- NVIDIA GeForce RTX 5060 Ti 16 GB: Blackwell **GB206**, 36 SMs, **4,608 CUDA cores**, 144 5th-gen Tensor cores, 16 GB GDDR7 on a 128-bit bus at 28 Gbps = **448 GB/s**, **180 W TGP**, 32 MB L2, PCIe 5.0 x8.
- **Compute capability 12.0 (sm_120).** Needs CUDA ≥ 12.8 and driver ≥ 570. PyTorch supports Blackwell from 2.7 (cu128 builds). On Ubuntu 26.04 the recommended driver is the open-kernel 595 branch, and NVIDIA's repo ships CUDA 13.x.
- Memory bandwidth (448 GB/s) is the decode-speed ceiling for LLMs. Rough tokens/s ceiling = 448 / (model GB). Expect about 40–70 tok/s single-stream for 6–10 GB quantized models, and much higher aggregate throughput with batching.

### 3.2 Known sm_120 caveats (as of 2026, verify!)
| Component | Status / workaround to test |
|---|---|
| **vLLM** | Prebuilt wheels and images have repeatedly failed on SM120. A 5060 Ti field report says FlashAttention is unsupported: use `--attention-backend TRITON_ATTN` (or FlashInfer). AWQ needs `--quantization awq_marlin`; GPTQ and bitsandbytes were reported broken. NVFP4 support for SM120 arrived in vLLM ≥ 0.12. |
| **llama.cpp** | Build from source with `-DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=120` and CUDA ≥ 12.8. Prebuilt binaries can silently fall back to CPU. |
| **HF TEI** | Standard images rejected compute cap 120. Build with `Dockerfile-cuda-blackwell --build-arg CUDA_COMPUTE_CAP=120`. |
| **RAPIDS (cuDF, cuVS, Polars GPU)** | Blackwell supported since 25.02. Polars GPU engine is Open Beta and **falls back to CPU for unsupported ops**, so use `pl.GPUEngine(raise_on_fail=True)`. |
| **Qdrant GPU indexing** | Since 1.13, via **Vulkan** (vendor-agnostic). It speeds up index *build*; queries run on CPU. Use the NVIDIA GPU image variant. |
| **ONNX Runtime / TensorRT** | Check that the installed onnxruntime-gpu wheel lists `CUDAExecutionProvider` / `TensorrtExecutionProvider` and actually runs on sm_120. |
| **Rust candle / mistral.rs** | Compile with `CUDA_COMPUTE_CAP=120`; verify kernels run. |

### 3.3 Current versions to expect (record actuals in ENVIRONMENT.md)
| Thing | Expected (Nov 2026) | Note |
|---|---|---|
| Ubuntu | 24.04 LTS or 26.04 LTS | 26.04 has CUDA in the archive |
| CUDA | 13.x (≥ 12.8 required) | |
| Python | 3.12 / 3.13 | |
| pandas | 2.x / 3.x | test the pyarrow string backend |
| Polars (Py) | 1.44.x stable, **2.0** (RC since Sept 2026, may be final) | 2.0 has breaking changes; pin exactly |
| Polars (Rust crate) | 0.55.x | separate version line |
| DuckDB | **2.0.0 LTS** (released 2026-10-21); 1.5 supported until Mar 2027 | Go/Rust clients may lag a few days; pin the same engine version across clients if possible |
| PostgreSQL / pgvector | 18 / 0.8.x (0.8.3 in PGDG) | `halfvec`, HNSW, `hnsw.iterative_scan` |
| Go | 1.27 (Aug 2026) | generic methods |
| Qdrant | 1.19+ | GPU indexing via Vulkan |

### 3.4 Electricity and CO₂ constants (config values, not hard-coded)
- Household price **37.3 ct/kWh** (BDEW average for new contracts, data as of 10/2026).
- Small/medium **industry** price **16.7 ct/kWh** (BDEW, new contracts 2026). Report both: the second is what NRW industry pays.
- Grid intensity **344 g CO₂/kWh** (UBA estimate for German electricity consumption, 2025).
- Put these in `config/bench.yaml`, cite the sources in the report, and make them easy to update.

---

## 4. Phase plan

| Phase | Name | Main outputs | Gate |
|---|---|---|---|
| 0 | Environment audit | `docs/ENVIRONMENT.md`, `docs/COMPAT.md`, sudo checklist | **Gate 1** |
| 1 | Scaffold | repo tree, Taskfile, uv/cargo/go projects, `.env.example`, lint/test setup | |
| 2 | Harness | `zj` Go CLI (run/verify/idle/doctor/exporter), zjmark libs, calibration report | |
| 3 | Data | lexicon, generator (S/M/L/XL), gold labels, question sets, real-data fetchers, dataset cards | |
| 4 | Track A | dataframe/row-processing contestants + results | |
| 5 | Track B | embedding contestants + results | |
| 6 | Track C | DB/vector/lexical contestants + results | |
| 7 | Track D | LLM extraction + cascade + results | |
| 8 | Track E + winner | E2E pipelines, `reports/WINNER.md`, proposed architecture | **Gate 2** |
| 9 | Production / DevOps | Go gateway + worker, energy exporter, compose, k3s+Helm, monitoring, load tests, local CI | |
| 10 | Portfolio | README.de/en, REPORT.de/en, blog draft, demo, "Bewerbungs-Kit" | |

Build the tracks **thin end-to-end first**: 2–3 contestants per track at scale S, all tracks wired into the report. Then widen the contestant matrix. That way a usable report exists early.

### Phase 0: Environment audit (detailed)
Run and record in `docs/ENVIRONMENT.md` (raw outputs in `docs/env/`):
- `lsb_release -a`, `uname -r`, `lscpu`, `free -h`, `df -h` (≥ 200 GB free recommended), `nproc`.
- `nvidia-smi`, `nvidia-smi --query-gpu=name,compute_cap,driver_version,power.limit,power.default_limit,pstate,temperature.gpu --format=csv`, `nvcc --version` (if present), `cat /proc/driver/nvidia/version`.
- Docker: `docker info`, NVIDIA Container Toolkit (`docker run --rm --gpus all <current nvidia/cuda base tag> nvidia-smi`).
- RAPL: list `/sys/class/powercap/intel-rapl*/name` (AMD uses the same zone names), and check whether `energy_uj` is readable without root.
- CPU governor (`/sys/devices/system/cpu/cpu*/cpufreq/scaling_governor`), SMT, turbo/boost state.
- Toolchains: python/uv, rustc/cargo, go, gcc/clang, cmake, task. Install **user-level** tools only (uv, rustup, go tarball into `~/.local`, go-task) without sudo; anything system-level goes on the checklist.

**Compatibility probe** → `docs/COMPAT.md`, a matrix with status *works / works with workaround / broken* plus the exact command used:
torch matmul on GPU · onnxruntime-gpu providers · cudf import + op · `polars[gpu]` collect with `raise_on_fail=True` · llama.cpp sm_120 build + tiny GGUF · Ollama + tiny model (check `ollama ps` shows 100% GPU) · vLLM container with a ≤1B model (note the attention backend) · TEI Blackwell build + small embedding model · Qdrant GPU image (Vulkan device visible in logs) · Postgres 18 + pgvector container · DuckDB vss/fts extensions load.

**User checklist (Gate 1).** Prepare it; don't execute it:
- RAPL readability: `sudo chmod o+r /sys/class/powercap/intel-rapl:*/energy_uj /sys/class/powercap/intel-rapl:*:*/energy_uj` (non-persistent), or a udev rule for persistence. Note: world-readable RAPL is a minor power side-channel risk, acceptable on a personal dev box.
- `sudo nvidia-smi -pm 1` (persistence mode, so idle-power and init time stay stable).
- Optional: `sudo cpupower frequency-set -g performance` during benchmark sessions (record it either way).
- Docker group membership, NVIDIA Container Toolkit (`nvidia-ctk runtime configure --runtime=docker`), if missing.
- Optional: passwordless `sudo` only for `/usr/bin/tee /proc/sys/vm/drop_caches` (cold-cache runs).
- Optional hardware: a smart plug with a local API (Shelly Plus/Gen3: `GET /rpc/Switch.GetStatus?id=0` → `apower`, `aenergy`; or Tasmota: `/cm?cmnd=Status%208`) as **wall-power ground truth**. Plug the PC only, not the monitor.

---

## 5. Measurement methodology (also write it up as `docs/METHODIK.md`)

### 5.1 Energy sources
| Source | What | How | Caveats |
|---|---|---|---|
| **GPU** | board energy | NVML `nvmlDeviceGetTotalEnergyConsumption` (mJ counter, delta per run) + `nvmlDeviceGetPowerUsage` samples at 20 Hz | Consumer-card power readings are averaged/low-rate, so energy for windows < 5 s is *low confidence*. Cross-check the counter delta against the integrated samples. |
| **CPU** | package (+core, +dram on Intel if present) | RAPL `energy_uj` via powercap, polled at 20 Hz, wrap-around via `max_energy_range_uj` | Model/telemetry-based, not a shunt measurement. AMD exposes package/core but usually no DRAM. |
| **System (optional)** | wall AC power | smart plug HTTP API at 1 Hz | Includes PSU losses, fans, SSD, idle GPU. This is the ground truth for "what the meter sees". |
| **Process** | CPU time, peak RSS | `wait4` rusage (Go `ProcessState.SysUsage()`), `/proc` tree polling; cgroup v2 `memory.peak` for containers | |
| **GPU memory** | peak VRAM | NVML memory info + per-process list sampled at 20 Hz | |

Energy is **system-level within the run window**, not per-process. Contestants therefore run **in isolation**, and the report shows both:
- `E_gross` = measured energy in the window (GPU + CPU [+ DRAM]) [+ wall, if available]
- `E_net` = `E_gross − P_idle × t`, where `P_idle` per domain is measured with `zj idle` (60 s, nothing running) at session start and every 30 min.

Insight to highlight in the report: CPU-only contestants still pay for an idle GPU in `E_gross` (often 10–20 W). That's real-world cost, and `E_net` hides it.

### 5.2 Protocol
- Every measured run: **1 warm-up + 5 measured repetitions** (`config/bench.yaml`: `reps`). Report the **median** and IQR, and flag a coefficient of variation > 5 %.
- **Interleave** contestants round-robin (A1 B1 C1 A2 B2 C2 …) instead of running all repetitions back-to-back. This avoids thermal-drift bias. Randomize the order with a recorded seed.
- **Cool-down** between runs until GPU temp < 50 °C (configurable) or 60 s pass. Record temperatures.
- Warm cache is the default. A cold-cache variant (`drop_caches`) runs only for the ingest stages, and only if sudo was granted.
- Per-run **timeout** (default 30 min). A timeout is recorded as `DNF`. Anti-pattern baselines run only up to the scale where they finish. **Never extrapolate** silently.
- An OOM is a result too: record `OOM` with peak RSS/VRAM.
- No other heavy processes during runs. The harness checks system load before each run and waits if `loadavg1 > 1.0`.
- Record the environment fingerprint with each run: driver, CUDA, governor, GPU clocks/power limit, library versions and image digests.

### 5.3 Contestant CLI contract
Each contestant is a command registered in `config/contestants.yaml`:
```yaml
- id: a09_polars_eager
  track: A
  task: a.full                 # a.read | a.full | a.udf | b.embed | c.load | c.index | c.query | d.extract | e.ask …
  cmd: ["uv", "run", "--project", "contestants/python/df", "python", "-m", "zj_df.a09_polars_eager"]
  args: ["--input", "{input}", "--output", "{output}", "--threads", "{threads}"]
  needs_gpu: false
  servers: []                  # e.g. ["postgres18"] → started/healthchecked by harness BEFORE the measured window
  scales: [S, M, L, XL]
  anti_pattern: false
```
Rules:
- Exit 0 on success. Write the output to `{output}`. Print **one final line** on stdout: `ZJRESULT {"rows": N, "notes": "...", "extra": {...}}`.
- Server startup and model loading are measured as a **separate stage** (`load`). The report shows both "cold incl. load" and "steady state".
- Threads: default = all cores. Record the thread count. A thread sweep is optional for the top contestants.

### 5.4 Stage markers (`zjmark`)
- Contestants write `ZJMARK {"stage":"parse","ev":"start"}` / `…"ev":"end"…` lines to **stderr** and flush immediately. Python stderr is line-buffered; in Rust use `eprintln!`; in Go use `fmt.Fprintln(os.Stderr, …)`.
- The harness timestamps each marker **on receipt** (monotonic clock) and integrates energy samples per stage. Result: a per-stage time/energy breakdown with no cross-language clock problems.
- Helper libraries: `libs/python/zjmark` (context manager `with stage("embed"):`), `libs/rust/zjmark` (RAII guard), `harness/pkg/zjmark` (Go).

### 5.5 Result record (`results/runs.jsonl` → loaded into `results/zj.duckdb`)
```json
{
  "run_id": "uuid", "session_id": "uuid", "ts": "RFC3339",
  "contestant": "a09_polars_eager", "track": "A", "task": "a.full",
  "scale": "M", "rows": 100000, "rep": 3, "warmup": false, "status": "ok|dnf|oom|fail|disqualified",
  "verify": {"passed": true, "detail": "..."},
  "wall_s": 1.234, "cpu_user_s": 3.1, "cpu_sys_s": 0.2, "max_rss_mb": 812, "vram_peak_mb": 0,
  "energy_j": {"gpu": 21.0, "cpu_pkg": 55.2, "dram": null, "wall": null},
  "idle_w": {"gpu": 14.1, "cpu_pkg": 9.8, "wall": null},
  "energy_net_j": {"gpu": 3.6, "cpu_pkg": 43.1},
  "energy_confidence": "high|low",
  "stages": [{"name": "parse", "t_s": 0.61, "gpu_j": 9.0, "cpu_j": 30.1}],
  "quality": {"f1": null, "recall_at_10": null, "ndcg_at_10": null},
  "throughput": {"rows_per_s": 81037, "rows_per_kj": 1311000},
  "env": {"fingerprint": "sha256:…", "driver": "…", "cuda": "…", "governor": "…", "versions": {"polars": "…"}},
  "config": {"threads": 16, "batch_size": null, "model": null}
}
```

### 5.6 Derived metrics and conversions
- `rows/s`, `J/row` (also mJ/row, µJ/row), `rows/kJ`.
- `€ per 1M rows` = `J/row × 1e6 / 3.6e6 × price_per_kWh`, at both household and industry price.
- `g CO₂ per 1M rows` = `J/row × 1e6 / 3.6e6 × 344`.
- For query benchmarks: p50/p95/p99 latency, QPS at concurrency 1 and 16, **J/query**.

### 5.7 Calibration (end of Phase 2, `docs/CALIBRATION.md`)
- Idle baseline 3 × 60 s; report its stability.
- CPU load: `stress-ng --cpu $(nproc) --timeout 60s`. Check the RAPL package power is plausible against the CPU's spec.
- GPU load: a 60 s torch fp16 matmul loop. Power should approach about 180 W. Check that the NVML counter delta agrees with the integrated power samples within ±5 %.
- If a smart plug is present: compare wall energy against (GPU + CPU) and report the "rest of system + PSU loss" share.
- Measure harness overhead: run `sleep 30` as a contestant.

---

## 6. Data (Phase 3)

### 6.1 Synthetic NRW industrial data (primary; it has gold labels)
Generator in `datagen/` (Python, Faker `de_DE`, seeded, deterministic, vectorized enough to make 10M rows in minutes). **Labels → text**: the generator first samples structured labels, then renders German text from them, so every row carries ground truth.

**Lexicon** (`spec/lexicon/*.yaml`, written by the agent, ~300–600 entries total):
- `werke_nrw.yaml`: plants by city → Regierungsbezirk → NUTS-2:
  - **Düsseldorf (DEA1):** Düsseldorf, Duisburg, Essen, Krefeld, Oberhausen, Mülheim an der Ruhr, Wuppertal, Solingen, Neuss, Dormagen, Wesel
  - **Köln (DEA2):** Köln, Leverkusen, Aachen, Bonn
  - **Münster (DEA3):** Münster, Gelsenkirchen, Bottrop, Marl
  - **Detmold (DEA4):** Bielefeld, Paderborn, Gütersloh
  - **Arnsberg (DEA5):** Dortmund, Bochum, Hagen, Hamm, Lünen, Siegen

  Each plant gets a sector flavour: Stahl, Chemie, Energie, Logistik, Automotive-Zulieferer, Lebensmittel, Maschinenbau.
- `komponenten.yaml`: Kreiselpumpe, Gleitringdichtung, Wälzlager, Getriebe, Frequenzumrichter (FU), Druckluftleitung, Hydraulikzylinder, Förderband, Walze, Wärmetauscher, Absperrschieber, Regelventil, Drucksensor, PT100-Temperaturfühler, SPS-Baugruppe, Schütz, Motorschutzschalter, Brückenkran, Rührwerk, Extruder, Abfüllanlage, Kühlwasserpumpe … with synonyms and compound variants.
- `schadensarten.yaml` (enum): Leckage, Verschleiß, Überhitzung, Schwingung/Vibration, Kurzschluss, Bruch, Korrosion, Verstopfung, Kavitation, Kommunikationsfehler (Profinet/Profibus), Fehlalarm, Sonstiges.
- `ursachen.yaml`, `massnahmen.yaml`: Materialermüdung, fehlende Schmierung, Fremdkörper, Fehlbedienung, Spannungsspitze, Alterung … / Dichtung getauscht, Lager ersetzt, nachgeschmiert, Filter gereinigt, Parameter angepasst, SPS neu gestartet, Fremdfirma beauftragt …
- `abkuerzungen.yaml`: i.O., n.i.O., ggf., lt., z.B., u.a., bzgl., ca., inkl., Std., Min., IH (Instandhaltung), MA, AV, QS, WE, FA, BDE, SPS, FU, HD/ND, Pos., Fa., Nr. → expansions.
- `sicherheit.yaml`: trigger terms for safety-relevant events (Verletzung, Beinahe-Unfall, Brand, Rauchentwicklung, Gefahrstoffaustritt, Absturzgefahr …).

**Tables:**
1. `stoermeldungen` (SAP-PM-like fault notifications). This is the **main table**.
   - Visible columns: `meldung_id, werk, anlage, equipment_nr ("P-4711", "M 0815-A"), meldungsdatum (mixed: "12.03.2026", "12.3.26", "2026-03-12"), schicht (Früh/Spät/Nacht), prioritaet (1–4), meldungstext (free text 1–4 sentences), stillstand ("1,5 h", "90 min", "ca. 2 Std.", "1-2h", empty), kosten ("1.234,56 €", "850 EUR", empty), status`.
   - Hidden gold columns (separate file `*_gold.parquet`): `komponente, schadensart, ursache, massnahme, sicherheitsrelevant, stillstand_minuten, kosten_eur, ersatzteil_materialnr, datum_iso`.
2. `qualitaetsmeldungen` (8D/complaints): supplier, material no., batch (*Charge*), characteristic, measurement "12,7 mm", tolerance, verdict "i.O./n.i.O.", free text. Gold: defect class, root-cause category, measure, deviation in mm.
3. *(Extra)* `pruefprotokolle`: inspection records with measured values and units (bar, °C, mm, kN, kWh).

**Noise injection** (rates configurable, recorded in the dataset card): typos (keyboard-neighbour, ~2 %), umlaut transliteration (ä→ae) in ~10 % of rows, **mojibake** (`Ã¼` for `ü`) in ~3 %, abbreviations, mixed case, missing punctuation, English loanwords ("Downtime", "Leak"), number/date format mixing, empty fields, duplicate whitespace, and **German compounds vs. split forms** ("Druckluftleitungsleckage" vs. "Leckage an der Druckluftleitung"). The last one matters for testing BM25 against dense retrieval.

**Variety with little handwork:** template grammar (~60–120 sentence templates per table) × lexicon sampling × noise. *Optional:* an LLM paraphrase pass with the best local model over a 5k-row seed subset, stored as an extra template pool. Labels stay known because the paraphrase prompt must keep the given facts. Validate a sample with a regex/NER check and drop paraphrases that lose facts.

**Scales:** S = 10k, M = 100k, L = 1M, XL = 10M rows. The LLM tracks use a fixed 2,000-row sample `D2k` (+ 300-row dev set).

**Formats per scale:** `*.cp1252.csv` (`;`, decimal comma, the "realistic SAP export"), `*.utf8.csv`, canonical `*.parquet` (zstd), plus one small `*.xlsx` sample for the demo.

**Question set** `spec/questions_e.jsonl` (generated from labels, ~300 questions, German), three types:
- *lookup:* „Was war die Ursache der Störung an P-4711 in Duisburg am 12.03.2026?" → gold row IDs + answer
- *filtered semantic:* „Leckagen an Druckluftleitungen in Werken im Regierungsbezirk Arnsberg 2025" → gold row-ID set
- *aggregation:* „Wie viele sicherheitsrelevante Störungen gab es je Werk im 2. Quartal 2026?" → gold table (answer via SQL)

**Retrieval qrels** for Track C (`spec/qrels_c.jsonl`): ~1,000 queries with graded relevance derived from label overlap (component + failure mode + plant/date filters).

### 6.2 Real open data (robustness + demo; no gold labels)
| Source | What | Access | Use |
|---|---|---|---|
| **thru.de (UBA PRTR)** | ~5,400 reporting industrial facilities in Germany with activity descriptions, releases and waste; downloadable as **SQLite** or Excel | `https://thru.de/downloads/` → table `betriebe` (e.g. `prtr_beschreibung`) | Filter to NRW. A real German industrial text + numbers table; SQLite doubles as an input format |
| **TED (EU tenders)** | Public procurement notices, many German, filterable by place of performance (NUTS DEA*) | `POST https://api.ted.europa.eu/v3/notices/search` with an expert query; **no API key** for search; page limits apply (use iteration mode for > 15k) | Real German tender texts (CPV codes, buyer, deadline) for the row-wise extraction demo. Build and validate the query in TED Expert Search first |
| **BA Jobsuche** | Job ads of the Bundesagentur für Arbeit | `GET https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v6/jobs` (older docs show v4; probe which version answers) with header `X-API-Key: jobboerse-jobsuche`; community docs at `jobsuche.api.bund.dev` / github.com/bundesAPI/jobsuche-api | Fetch **Werkstudent** ads in NRW (`was=Werkstudent`, NRW cities + `umkreis`), store only job/company fields. Meta-use: *which skills do NRW Werkstudent ads ask for?* That ties the project to its own goal |
| *(Extra)* Open.NRW | NRW open-data portal | check its catalogue API (CKAN/DCAT) | additional tables if useful |

Etiquette: ≤ 1 req/s, caching, respect terms of use, attribute sources in dataset cards, no personal data. The BA API isn't officially documented by the BA, so treat it as best-effort and keep the volume small (≤ a few thousand ads).

### 6.3 Dataset cards
`data/cards/<dataset>.md`: source, licence/terms, generation params/seed, row counts, noise rates, schema, known issues.

---

## 7. Track A: Dataframe and row processing (Phase 4)

### 7.1 Tasks
- `a.read`: decode `stoermeldungen.cp1252.csv` → typed Parquet. Strings stay as-is; just correct dtypes for ids and priorities.
- `a.full`: complete S1–S3 pipeline (spec below) → Parquet with a fixed schema.
- `a.udf`: only the row-wise rule-extraction step, on already-normalized Parquet. This isolates UDF and regex cost.

### 7.2 `a.full` spec (finalize as `spec/track_a.md` + golden vectors **before** writing contestants)
1. Decode cp1252 → UTF-8. Repair mojibake with a **fixed replacement table** (Ã¤→ä, Ã¶→ö, Ã¼→ü, ÃŸ→ß, Ã„→Ä, Ã–→Ö, Ãœ→Ü, â‚¬→€). Don't use ftfy: it isn't reproducible across languages.
2. Unicode **NFC**. Collapse whitespace runs to one space, then trim.
3. **Dates** → `DATE` (null if invalid):
   - `DD.MM.YYYY`, `D.M.YYYY`, `DD.MM.YY` (YY → 2000+YY), ISO `YYYY-MM-DD`
   - reject impossible dates (31.02.)
4. **German numbers** → `FLOAT64`: optional sign, thousands `.` or space/thin-space, decimal `,`, optional `€`/`EUR`. Anything else → null.
5. **Durations** → minutes `INT32`, with an optional `ca.` prefix:
   - `x h` / `x Std.` / `x Stunde(n)` → ×60
   - `x min` / `x Minuten` → ×1
   - ranges `a-b h` → **upper bound**
   - decimal comma allowed
6. `text_norm` = expand abbreviations from `abkuerzungen.yaml`, matching token-bounded and case-insensitively. `text_lower` = Unicode **lower()**, *not* casefold, so ß stays ß. `text_fold` = `text_lower` with ä→ae, ö→oe, ü→ue, ß→ss.
7. Rule extraction:
   - `equipment_nr` via pattern
   - `materialnr` via pattern
   - `sicherheitsrelevant_rule` = any safety trigger term in `text_fold`
   - `komponente_rule` = **longest dictionary match** (Aho-Corasick or equivalent) over `text_fold`; ties go to the earliest position
8. `regierungsbezirk`, `nuts2` via join with `werke_nrw.yaml`.
9. `doc_text` = `"Werk: {werk} | Anlage: {anlage} | Equipment: {equipment_nr} | Datum: {datum_iso} | Priorität: {prioritaet} | Meldung: {text_norm}"`. Empty parts are omitted, with exactly one ` | ` separator.
10. Output sorted by `meldung_id`, Parquet zstd, schema fixed in `spec/track_a.schema.json`.

**Portability rules:**
- Regexes must be RE2-compatible: no lookaround, no backreferences.
- `\b` is ASCII in RE2 but Unicode in Rust/Python, so **don't use `\b` next to umlauts**. Define token boundaries as start/end of string or `[\s.,;:!?()\[\]/"'-]`.
- Golden vectors `spec/track_a_cases.jsonl` (≥ 150 tricky cases) are the shared unit tests for **every** language.

**Verification (`zj verify`):** load the contestant output and the reference output (`reference/track_a.py`) with DuckDB, compare all columns **by value** (floats abs. tol. 1e-9; strings exact), and report the mismatch count and the first 20 diffs. Byte equality isn't required (Parquet writers differ).

### 7.3 Contestants (Core = must, Extra = if time)
| ID | Tool | Variant | Class |
|---|---|---|---|
| a01_py_csv_loop | Python stdlib | `csv` + `re`, per-row loop | Core (pure-Python baseline) |
| a02_pandas_iterrows | pandas | `iterrows()` | Core, **anti-pattern** ("dull" baseline) |
| a03_pandas_itertuples | pandas | `itertuples()` | Core |
| a04_pandas_apply | pandas | `apply(axis=1)` | Core (common in the wild) |
| a05_pandas_vectorized | pandas (NumPy dtypes) | `.str` vectorized + `np.select` | Core |
| a06_pandas_arrow | pandas + pyarrow | `engine="pyarrow"`, `dtype_backend="pyarrow"` | Core |
| a07_cudf_pandas | cudf.pandas | **same code as a05** under `python -m cudf.pandas` | Core GPU (log the fallback profile) |
| a08_polars_map_elements | Polars | Python UDF per row | Core, **anti-pattern** |
| a09_polars_eager | Polars | native expressions | Core |
| a10_polars_lazy | Polars | `scan_csv` → lazy → `collect()` | Core |
| a11_polars_streaming | Polars | streaming engine + `sink_parquet` | Core |
| a12_polars_gpu | Polars + cuDF | `collect(engine=pl.GPUEngine(raise_on_fail=True))` | Core GPU |
| a13_duckdb_py | DuckDB (Python) | `read_csv` + SQL + `COPY … TO parquet` | Core |
| a14_pyarrow_compute | PyArrow | `pyarrow.compute` kernels | Extra |
| a15_rs_polars | Rust `polars` crate | lazy API | Core |
| a16_rs_arrow_rayon | Rust `arrow-rs` | csv reader + `rayon` + `regex` + `aho-corasick` | Core |
| a17_rs_handrolled | **pure Rust, max effort** | `memmap2` + `memchr` chunking on line boundaries + `rayon` + hand-written number/date parsers + `aho-corasick` + `parquet` writer | Core |
| a18_rs_datafusion | Rust DataFusion | SQL + Rust UDFs | Extra |
| a19_go_native | Go | `encoding/csv` + worker pool + RE2 | Extra (useful input for Phase 9) |
| a20_duckdb_cli | DuckDB CLI | a pure `.sql` file (no Python) | Extra |
| a21_sqlite | SQLite | `.import` + SQL (+ app-defined functions) | Extra (DB baseline) |
| a22_chdb | ClickHouse embedded (chDB/clickhouse-local) | SQL | Extra |
| a23_polars_gpu_hybrid | Polars CPU + GPU engine | explicit hand split: CPU normalise/expand → GPU fields (`raise_on_fail=True`) → CPU komponente | Extra (added 2026-11-07, ADR 0011) |

Run Core at S/M/L. Run XL for everything that finishes L in < 5 min. Anti-patterns run only S/M (L if it finishes within the timeout).

Optional thread sweep for the top 5: threads ∈ {1, 2, 4, 8, all}, to show **energy-optimal thread count ≠ time-optimal**.

---

## 8. Track B: Embeddings (Phase 5)

**Task `b.embed`:** input `doc_text` from Track A output (scale M for the matrix, L for the top 3). Settings: max 512 tokens, L2-normalized output, float16 `.npy` + `id` order file.

**Verification:** mean cosine similarity vs. the reference (sentence-transformers fp32, same model) ≥ 0.995, **and** recall@10 on the Track C qrels within 1 point of the reference.

**Models (licence-checked):**
| Model | Licence | Notes |
|---|---|---|
| **BAAI/bge-m3** | MIT | primary; 1024-d, 8k ctx, dense + sparse + multi-vector in one model; strong multilingual retrieval |
| Qwen/Qwen3-Embedding-0.6B (and 4B if time) | Apache 2.0 | Matryoshka (MRL) dims; strong MMTEB |
| intfloat/multilingual-e5-large-instruct | MIT | 1024-d, needs query/passage prefixes, ~512 tokens |
| google/embeddinggemma-300m | Gemma terms (check) | tiny/fast tier |
| *(excluded from final recommendation)* Jina v3/v4 | weights non-commercial | may be benchmarked, marked "non-commercial" |

**Contestants:**
| ID | Runtime | Class |
|---|---|---|
| b00_ort_cpu_int8 | ONNX Runtime CPU, int8 | Core (CPU baseline for the energy comparison) |
| b01_st_fp32 | sentence-transformers fp32 | Core (reference) |
| b02_st_fp16 | sentence-transformers fp16/bf16 | Core |
| b03_st_fp16_sorted | + length-sorted/bucketed batches, tuned batch size | Core |
| b04_ort_cuda | ONNX Runtime CUDA EP fp16 | Core |
| b05_ort_trt | ONNX Runtime TensorRT EP fp16 | Extra |
| b06_tei | HF Text-Embeddings-Inference (Rust server, Blackwell Dockerfile) | Core |
| b07_vllm_pooling | vLLM pooling/embedding runner (Qwen3-Embedding) | Core |
| b08_llamacpp_embed | llama.cpp `--embedding` GGUF (f16/q8) | Core |
| b09_rs_candle | Rust candle | Extra |
| b10_rs_ort | Rust `ort` crate (ONNX Runtime) | Core (feeds the pure-Rust pipeline E4) |
| b11_infinity | Infinity server | Extra |

Sweeps for the top 3: batch size {16, 32, 64, 128, 256} × sorting on/off. Server runtimes: client concurrency {1, 4, 16}.

Extra experiment: **MRL truncation** (1024 → 512 → 256) and **binary/int8 quantization**. Measure the effect on recall vs. storage, index time and energy in Track C.

---

## 9. Track C: Storage, indexing and retrieval (Phase 6)

**Tasks:**
- `c.load`: bulk-load rows + vectors + metadata (best native path: `COPY BINARY`, Arrow, batch upsert). Measure rows/s and J.
- `c.index`: build the vector index (+ full-text index). Measure time, J, disk size, RAM/VRAM.
- `c.query`: 1,000 queries × filter classes:
  - **none**
  - **werk** (≈ 4 % selectivity)
  - **date range** (≈ 10–30 %)
  - **combined** (≈ 1 %)

  Report p50/p95/p99, QPS at concurrency 1 and 16, J/query, and recall@10 vs. **exact filtered brute force**.
- `c.hybrid`: dense + BM25, fused with **RRF (k=60)**. Report nDCG@10 on the qrels. Include the compound-word subset (does BM25 miss „Leckage an der Druckluftleitung" when the query is „Druckluftleitungsleckage"?).

**Fair-play rules:** compare **at equal recall**. HNSW baseline is M=16, ef_construction=200; tune `ef_search`/equivalents until recall@10 ≥ 0.95, then compare speed and energy. Use half-precision vectors where supported. Same vectors (from b01/bge-m3) for all.

**Contestants:**
| ID | System | Notes | Class |
|---|---|---|---|
| c00_exact_gpu | torch fp32 brute force on GPU | **ground truth** for recall (also filtered) | Core |
| c01_faiss_cpu | FAISS HNSW (CPU) | library baseline | Core |
| c02_cuvs_cagra | RAPIDS cuVS CAGRA (GPU) | GPU ANN library | Core |
| c03_pgvector | PostgreSQL 18 + pgvector `halfvec` HNSW, `hnsw.iterative_scan` for filters, `tsvector('german')` + GIN | "one DB for everything" candidate | Core |
| c04_pgvectorscale | + DiskANN index | check PG18 support | Extra |
| c05_duckdb_vss | DuckDB `vss` HNSW + `fts` (stemmer german) | embedded; check index-persistence flag in the installed version | Core |
| c06_sqlite_vec | SQLite + sqlite-vec + FTS5 `bm25()` | embedded, minimal ops | Core |
| c07_lancedb | LanceDB (Rust core), IVF-PQ / HNSW-SQ, optional GPU index build | embedded columnar | Core |
| c08_qdrant | Qdrant server, payload indexes, **GPU indexing (Vulkan)** on/off | dedicated vector DB (Rust) | Core |
| c09_milvus_gpu | Milvus standalone GPU (CAGRA) | heavy (etcd/minio) | Extra |
| c10_clickhouse | ClickHouse vector similarity index | OLAP DB | Extra |
| c11_tantivy | Rust Tantivy BM25 (German stemmer) | lexical baseline | Core |
| c12_opensearch | OpenSearch, German analyzer + decompounder | JVM-heavy lexical/hybrid | Extra |
| c13_paradedb | Postgres `pg_search` (BM25) | real BM25 inside Postgres | Extra |

Notes for the report:
- Postgres `ts_rank` is **not** BM25, so say so.
- German Snowball stemming doesn't decompound. Show what dense retrieval and/or a decompounder fixes.
- sqlite-vec is (mostly) brute force. Show where that's "fast enough".

---

## 10. Track D: LLM row-wise extraction (Phase 7)

**Task `d.extract`:** for each row of `D2k`, produce JSON matching `spec/schema_stoermeldung.json`:
```json
{"komponente": "string|null", "schadensart": "enum", "ursache": "string|null", "massnahme": "string|null",
 "sicherheitsrelevant": "boolean", "stillstand_minuten": "integer|null", "ersatzteil_materialnr": "string|null"}
```
- German system prompt with the enum list. It's identical across rows, so it's a **shared prefix → prefix caching**. Zero-shot and 3-shot variants.
- **Constrained decoding** (JSON schema / grammar) on every engine that supports it. Report the JSON-validity rate either way.
- temperature 0, `max_tokens` 256. **Reasoning/thinking OFF**: Qwen3.5 thinks by default, gpt-oss has a reasoning-effort setting. Verify by counting output tokens, because thinking tokens explode energy use.
- Metrics:
  - quality: exact match for enum/bool/int fields, token-F1 for free-text fields, macro-F1 overall
  - throughput: rows/s, output tok/s
  - energy: **J/row**
  - memory: VRAM peak
  - time to first token (single stream)

**Models (16 GB VRAM shortlist, Oct/Nov 2026; licences must be re-checked on the model cards):**
| Tier | Model | Format | Approx. weights | Why |
|---|---|---|---|---|
| small/fast | Qwen3.5-4B (or 2B), Gemma 4 E4B | Q4_K_M / Q8_0 | 3–5 GB | high rows/s; maybe "good enough" with a cascade |
| mid | **Qwen3.5-9B** | Q4_K_M (≈ 5.7 GB) / Q8_0 (≈ 9.5 GB) | | Apache 2.0, 201 languages, long context |
| mid | **Gemma 4 12B** | Q4 / Q8_0 (≈ 12.7 GB) | | Apache 2.0, 140+ languages |
| mid (EU) | **EuroLLM-9B-Instruct** | Q8_0 / Q4 | | European model, strong German benchmarks |
| mid (DE) | Teuken-7B-instruct-commercial-v0.4 | Q8_0 | | German OpenGPT-X project; tokenizer efficient for German |
| large | **gpt-oss-20b** | MXFP4 (≈ 12.1 GB) | | Apache 2.0, structured outputs, adjustable reasoning |
| large | Ministral 3 14B | Q4_K_M (≈ 8.2 GB) | | JSON/function calling |

**Engines:**
| ID | Engine | Notes | Class |
|---|---|---|---|
| d01_ollama | Ollama | `format` = JSON schema; `OLLAMA_NUM_PARALLEL`, flash-attention and KV-cache-type env knobs | Core (the "easy" baseline) |
| d02_llamacpp | llama.cpp `llama-server` (sm_120 build) | `-ngl 99 -np N`, continuous batching, JSON-schema/grammar | Core |
| d03_vllm | vLLM (sm_120 workarounds from COMPAT.md) | structured outputs, prefix caching, AWQ-Marlin / NVFP4 / MXFP4 | Core |
| d04_sglang | SGLang | if sm_120 works | Extra |
| d05_mistralrs | mistral.rs (pure-Rust engine) | GGUF/ISQ; for the pure-Rust pipeline | Extra |
| d06_trtllm | TensorRT-LLM | stretch; only if sm_120 builds | Extra |
| d10_gliner | **SauerkrautLM-GLiNER** (MIT; trained incl. German) / gliner_multi-v2.1 | span NER for komponente/ersatzteil/ursache | Core |
| d11_cascade | **Cascade:** L0 Track-A rules → L1 GLiNER → L2 LLM only for unresolved/low-confidence fields | report % rows reaching the LLM, total J, F1 | Core |

**Sweep design** (don't brute-force everything):
1. For each engine, use Qwen3.5-9B Q4 to sweep concurrency N ∈ {1, 4, 16, 64} and pick the best N by rows/J.
2. At that N, run all models on all engines that support them.
3. Top 3 configs: quantization comparison (Q4 vs. Q8 vs. MXFP4/NVFP4/AWQ where available), zero- vs. 3-shot.

**Quality gate for "winning":** macro-F1 ≥ 0.9 × best macro-F1 in the track, and JSON validity ≥ 99.5 %.

---

## 11. Track E: End-to-end RAG (Phase 8)

**Metrics:**
- **Time-to-RAG / Energy-to-RAG**: raw cp1252 CSV (L = 1M rows) → queryable index (S1–S5). One number in seconds and Wh.
- **Per question** (`spec/questions_e.jsonl`):
  - latency (p50/p95, time to first token)
  - J/question
  - answer correctness: programmatic for lookup/aggregation, set-F1 of cited row IDs for filtered semantic questions
  - citation precision/recall
- **Router accuracy**: did aggregation questions go to SQL and semantic ones to vector search?

**Text-to-SQL router (safety rules):** the LLM produces SQL only against a whitelisted read-only view. Parse it with `sqlglot` (or the equivalent in Go) and reject anything other than `SELECT`. Execute with a read-only DB role + `statement_timeout`. Log every generated SQL.

**Pipelines:**
| ID | Pipeline | Purpose |
|---|---|---|
| e1_classic_python | pandas → sentence-transformers → FAISS → Ollama | "what most tutorials do" baseline |
| e2_modern_embedded | Polars → TEI/ONNX → DuckDB (vss + fts + SQL router) → llama.cpp | single-file DB, no server |
| e3_postgres_only | Rust or Polars ingest → embeddings → **Postgres** (pgvector + FTS + SQL router + queue) → best LLM engine | operationally simplest |
| e4_pure_rust | a17 Rust parse → `ort`/candle embed → LanceDB (Rust) → mistral.rs or llama.cpp | "pure Rust" story |
| e5_best_of_tracks | composed automatically from Track A–D winners (read from results DB) | the expected winner |

### 11.1 Winner selection (`reports/WINNER.md`, generated)
For each track and for E2E:
1. Drop runs that failed verification or quality gates.
2. **Fastest** = min median wall time (the primary criterion, as requested). **Most efficient** = min median `E_gross`.
3. Show the **Pareto front** (time vs. energy).
4. **ZJ-Score** = √((T/T_best) × (E/E_best)). 1.0 means best at both; lower is better.
5. The Phase 9 production stack = the **fastest E2E pipeline that passes all gates**. If its energy is > 1.5× the most efficient pipeline, or it needs > 2 extra services compared to the next-fastest, write an ADR weighing the alternative and let the user decide at **Gate 2**.

---

## 12. Phase 9: Production stack with Go DevOps (after Gate 2)

The architecture adapts to the winner. The default target (likely winner shape: Rust/Polars ingest + Postgres or Qdrant + llama.cpp/vLLM) looks like this:

```
             ┌───────────────────── Go ─────────────────────┐
 client ──► │ zj-gateway  (REST/OpenAPI + SSE, auth, rate  │──► zj-core (Rust gRPC: parse/normalize/extract)  [if Rust wins A]
            │ limit, router SQL↔vector, OTel, /metrics)     │──► embed server (TEI / ORT / vLLM pooling)       [GPU]
            │ zj-worker   (ingest jobs, batching, retries)  │──► LLM server (llama.cpp / vLLM)                 [GPU]
            └───────────────────────────────────────────────┘──► DB (Postgres+pgvector | Qdrant | DuckDB …)
 zj-energy-exporter (Go: NVML + RAPL [+ plug]) ──► Prometheus ──► Grafana (dashboards: J/request, rows/s, p95, GPU W/°C)
```

### 12.1 Go services
- **zj-gateway** (`services/gateway`):
  - stdlib `net/http` (Go 1.22+ routing patterns) + `log/slog` JSON logs
  - **OpenAPI-first** with `oapi-codegen`
  - endpoints: `POST /v1/ingest` (CSV/Parquet upload → job), `GET /v1/jobs/{id}`, `POST /v1/extract` (rows → JSON), `POST /v1/search` (filters + hybrid), `POST /v1/ask` (SSE streaming answer with row citations)
  - `/healthz`, `/readyz`, `/metrics`
  - API-key auth, `x/time/rate` rate limiting, request-size limits, CORS, timeouts everywhere, graceful shutdown
- **zj-worker** (`services/worker`): ingest pipeline orchestration and batching to embed/LLM servers with backpressure.
  - Queue: if Postgres is in the stack, use **River** (a Postgres-native Go job queue); otherwise use NATS JetStream.
- **Go ↔ Rust boundary** (only if a Rust core wins Track A): gRPC (`buf` for Go codegen, `tonic` for Rust) as the default. *Experiment + ADR:* micro-benchmark gRPC vs. cgo FFI vs. a Go-native port (a19) for the parse step.
- **zj-energy-exporter** (`harness/cmd/zj-exporter`, reusing `harness/internal/energy`): Prometheus metrics `zj_gpu_energy_joules_total`, `zj_gpu_power_watts`, `zj_gpu_temp_celsius`, `zj_cpu_pkg_energy_joules_total`, `zj_wall_power_watts` (optional).
  - The gateway records request windows, so the dashboard can show estimated **J per request** = ΔE over the window / requests.
  - Works on GeForce cards, where DCGM support is limited.
- **Testing:** table-driven unit tests; **testcontainers-go** integration tests against real Postgres/Qdrant/llama.cpp (tiny model); Go fuzz tests (`go test -fuzz`) for German number/date/duration parsers if they exist in Go; contract tests against the OpenAPI spec.
- **Quality:** `golangci-lint`, `govulncheck`, race detector, ≥ 70 % coverage on `internal/`.

### 12.2 Containers and deployment
- Multi-stage Dockerfiles: Go → `gcr.io/distroless/static` (non-root); Rust → distroless/cc; GPU services → pinned CUDA runtime images (sm_120-capable, digests pinned).
- **docker compose** (`deploy/compose/`): profiles `cpu`, `gpu`, `observability`. Healthchecks, resource limits, read-only root FS where possible, `.env`-based config.
- **Observability:** `grafana/otel-lgtm` all-in-one for dev (Grafana + Prometheus/Mimir + Loki + Tempo + OTel collector). Provisioned dashboards (JSON in `deploy/monitoring/grafana/`): *Energie & Durchsatz*, *Latenz & Fehler*, *GPU*. OpenTelemetry tracing across gateway → worker → servers. `node_exporter` (with RAPL collector) as a cross-check.
- **Kubernetes (single node):** **k3s** + NVIDIA container runtime + NVIDIA device plugin (`RuntimeClass nvidia`).
  - **GPU time-slicing** config so the embedding and LLM pods can share the one GPU. Document the trade-off.
  - Helm chart `deploy/helm/zeilenjoule` (values for dev/prod), Kustomize overlays optional, NetworkPolicies, PodSecurity "restricted" where possible, PVCs for the DB and model cache.
- **IaC (optional):** an Ansible playbook `deploy/ansible/` for user-level setup of a fresh Ubuntu box. NVIDIA driver steps stay manual and documented.
- **Backups/runbook:** `pg_dump` or Qdrant snapshots via a CronJob; `docs/RUNBOOK.md` (start/stop, rotate keys, restore, GPU OOM troubleshooting).

### 12.3 SLOs and load tests
- Targets to validate (adjust after measuring): `/v1/search` p95 < 100 ms at 20 RPS; `/v1/ask` time to first token p95 < 1.5 s; ingest ≥ (Track-E winner rows/s × 0.8).
- Load tools: **vegeta** (Go) and/or **k6**. Record J/request under load from the exporter. Report the knee of the latency curve.

### 12.4 Local CI (`task ci`, no GitHub)
Go: vet, golangci-lint, test -race, govulncheck · Rust: fmt --check, clippy -D warnings, test, `cargo audit` · Python: ruff, pyright, pytest, `pip-audit` · Docker: hadolint, build, **trivy** image scan, **syft** SBOM · Helm: `helm lint` + `kubeconform` · secrets: gitleaks (dir mode).
Prepare a `docs/CI.md` describing how this maps to GitHub Actions later, but **don't create `.github/`**.

---

## 13. Phase 10: Portfolio and application kit

- `README.de.md` (first) + `README.md`: problem, headline numbers (auto-inserted from results), architecture diagram (Mermaid), "reproduce in 3 commands", hardware note, licence/data attribution.
- `reports/REPORT.de.md` + `REPORT.md`, generated by `task report`. Figures (SVG):
  - per-track bar charts (log scale) of rows/s and J/row
  - **Pareto scatter** (time vs. energy)
  - stacked stage breakdown
  - Track C recall-vs-QPS curves
  - Track D F1-vs-J/row
  - cascade savings
  - E2E Time-/Energy-to-RAG
- Blog-post draft (German): *„Wie viel Energie kostet eine Zeile? pandas vs. Polars vs. Rust vs. GPU auf einer RTX 5060 Ti"*.
- **Bewerbungs-Kit** (`docs/BEWERBUNG.md`):
  - a one-page summary
  - 5 talking points mapped to typical NRW Werkstudent requirements (Python/SQL/ETL, Datenbanken, Docker/Kubernetes, Monitoring, KI/LLM/RAG, Rust/Go, **Energieeffizienz/Nachhaltigkeit**)
  - the skills analysis from the BA Werkstudent ads dataset (§6.2)
  - a short demo script (`task demo`: compose up → ingest sample → ask 3 German questions → show the J/question panel)
- Small demo page served by the gateway (plain HTML + htmx): a question box, the answer with cited rows, and live J/question.

---

## 14. Risks and mitigations
| Risk | Mitigation |
|---|---|
| Engine X broken on sm_120 | COMPAT.md first; documented workaround or exclusion; never block the project on one engine |
| Energy noise on short runs | scales sized so runs ≥ 10 s; `energy_confidence=low` flag; more reps |
| GPU engines silently fall back to CPU | `raise_on_fail`, fallback profiles, assertion on GPU utilization > 0 during the run |
| Spec ambiguity across languages | golden vectors first, RE2-only regex, by-value verification |
| VRAM contention in E2E | budget table per pipeline (model + KV cache + embedder ≤ 14.5 GB); fall back to smaller quant |
| Disk usage (models + images + XL data) | check `df` in `task doctor`; prune with user consent |
| API terms / rate limits for real data | caching, ≤ 1 req/s, small volumes, attribution |
| Scope creep | Core first at S/M with the report wired; Extras only after Gate 2 or if time permits |

---

## 15. Appendix

### 15.1 `config/bench.yaml` (starting point)
```yaml
reps: 5
warmup: 1
timeout_s: 1800
sample_hz: 20
cooldown: {gpu_temp_c: 50, max_wait_s: 60}
idle_baseline: {duration_s: 60, every_min: 30}
load_guard: {max_loadavg1: 1.0}
interleave: true
seed: 20261106
scales: {S: 10000, M: 100000, L: 1000000, XL: 10000000}
energy:
  price_eur_per_kwh: {household: 0.373, industry: 0.167}   # BDEW 10/2026; industry = SME new contracts 2026
  co2_g_per_kwh: 344                                        # UBA estimate 2025
  wall_meter: {enabled: false, kind: shelly, url: "http://192.168.x.y"}
vram_budget_gb: 14.5
```

### 15.2 Kickoff prompt for the first session
> Read `CLAUDE.md` and `docs/PLAN.md` completely. Create `docs/PROGRESS.md`. Execute **Phase 0** (environment audit + compatibility probe), write `docs/ENVIRONMENT.md` and `docs/COMPAT.md`, prepare the sudo/permission checklist, then stop at **Gate 1** and summarize what works, what needs a workaround, and what I must run.

### 15.3 Sources (checked 2026-11-06)
- RTX 5060 Ti specs: TechPowerUp review (ASUS TUF 16 GB) https://techpowerup.com/review/asus-geforce-rtx-5060-ti-tuf-16-gb/single-page.html · TechPowerUp GPU DB https://techpowerup.com/gpu-specs/geforce-rtx-5060-ti-16-gb.c4246
- vLLM on RTX 5060 Ti (SM_120) field report: https://discuss.vllm.ai/t/field-report-awq-on-rtx-5060-ti-sm-120-blackwell-awq-marlin-triton-attn-working/2463
- llama.cpp Blackwell build guide: https://bestllmfor.com/guides/llama-cpp-cuda-blackwell-sm120-build/
- TEI Blackwell Dockerfile PR: https://github.com/huggingface/text-embeddings-inference/pull/735
- Ubuntu 26.04 NVIDIA drivers / CUDA: https://linuxconfig.org/how-to-install-nvidia-drivers-on-ubuntu-26-04 · https://linuxize.com/post/how-to-install-nvidia-cuda-toolkit-on-ubuntu-26-04/
- Local LLMs by VRAM (Oct 2026): https://vdf.ai/blog/best-local-llm-by-vram/
- Gemma releases: https://ai.google.dev/gemma/docs/releases · Qwen3.5-9B: https://modal.com/library/qwen/qwen3-5-9b · EuroLLM-9B report: https://arxiv.org/abs/2506.04079
- German embedding models 2026: https://wz-it.com/en/blog/best-embedding-models-german/ · https://blckalpaca.at/en/knowledge-base/ai-agents/what-is-a-rag-system/embedding-models-2026-compared-text-embedding-3-cohere-bge-m3-voyage-jina
- SauerkrautLM-GLiNER: https://huggingface.co/VAGOsolutions/SauerkrautLM-GLiNER
- Polars GPU engine: https://docs.pola.rs/user-guide/lazy/gpu/ · RAPIDS cudf-polars: https://docs.rapids.ai/api/cudf/nightly/cudf_polars/
- DuckDB 2.0 LTS: https://duckdb.org/2026/10/21/announcing-duckdb-200
- pgvector for PG18 (PGDG): https://www.ubuntuupdates.org/package/postgresql/jammy-pgdg/main/base/postgresql-18-pgvector
- Qdrant GPU indexing: https://qdrant.tech/blog/qdrant-1.13.x/ · https://qdrant.tech/gpu-indexing/
- RAPL powercap: https://joule-profiler.github.io/sources/rapl/powercap.html · AMD RAPL in powercap: https://www.phoronix.com/news/AMD-RAPL-Linux-Now-19h
- TED API: https://docs.ted.europa.eu/api/latest/index.html · https://docs.ted.europa.eu/api/endpoints/ted-europa-eu.html
- BA Jobsuche API (community docs): https://github.com/bundesAPI/jobsuche-api
- thru.de PRTR (SQLite download): https://www.umweltbundesamt.de/themen/schadstofffreisetzungen-in-thrude-jetzt-auf-einem · https://thru.de/downloads/
- Electricity prices: BDEW Strompreisanalyse https://www.bdew.de/service/daten-und-grafiken/bdew-strompreisanalyse/
- CO₂ factor: UBA https://www.umweltbundesamt.de/themen/co2-emissionen-pro-kilowattstunde-strom-2025-nur
- Go 1.27 release notes: https://go.dev/doc/go1.27
