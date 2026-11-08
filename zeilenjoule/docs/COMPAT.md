# Kompatibilitätsmatrix / Compatibility matrix — RTX 5060 Ti (sm_120)

*Phase 0, 2026-11-06. Driver 580.178.04 (CUDA 13.0), Ubuntu 24.04.5, snap Docker 29.8.0.*
*Rohausgaben / raw outputs: `docs/env/probe_*.out`. Probe-Skripte: `docs/env/probes/`.*

Status: ✅ **works** · 🟡 **works with workaround** · ❌ **broken** · ⏳ **not yet verified**

> Throughput numbers in this file (TFLOPS, tok/s) are **smoke-test observations from single probe calls on a loaded machine** (loadavg 5–8). They are **not benchmark results** and must not be cited in reports.

## Summary

| # | Component | Status | Short note |
|---|---|---|---|
| 1 | Docker GPU passthrough | ✅ | fixed 2026-11-06 by ADR 0001 (docker-ce, snap removed): `--gpus all` works |
| 2 | torch matmul on GPU | ✅ | torch 2.14.1+cu130, native sm_120 |
| 3 | onnxruntime-gpu providers | ✅ | 1.31.1; CUDA EP session runs; TensorRT EP listed (not yet exercised) |
| 4 | cuDF import + op | ✅ | cudf-cu13 26.10.2 |
| 5 | cudf.pandas | ✅ | `--profile` logs CPU fallbacks per function |
| 6 | Polars GPU (`raise_on_fail=True`) | 🟡 | works; but forces **polars 1.44.2** (CPU env resolves 2.1.0) |
| 7 | llama.cpp prebuilt image | ✅ | `server-cuda` includes `ARCHS=…,1200`; all layers on CUDA0 |
| 8 | llama.cpp source build sm_120 | 🟡 | builds + runs; container link needs `libcuda.so.1` stub symlink |
| 9 | Ollama + model, 100 % GPU | 🟡 | works, but 0.13.5 is ~27 releases old |
| 10 | vLLM container, ≤ 1B model | ✅ | v0.33.0 (CUDA 13.0.2); default FLASH_ATTN (FA2) works on sm_120 |
| 11 | TEI Blackwell + small model | ✅ | official `120-1.9.4` image; no source build needed |
| 12 | Qdrant GPU image (Vulkan) | ✅ | GPU HNSW build verified in Phase 6 (c08_qdrant_gpu, see Track C rows) |
| 13 | Postgres 18 + pgvector | ✅ | PG 18.6, pgvector 0.8.7, halfvec HNSW + iterative scan + German FTS |
| 14 | DuckDB vss + fts | 🟡 | 2.0.0 loads both; FTS query pattern changed (see detail) |
| 15 | NVML energy counter | 🟡 | readable without root, but **reads ~19 % low under load** (146 W vs 180 W); GPU energy uses power samples instead (ADR 0006) |
| 16 | RAPL (CPU energy) | ❌ → 🟡 | root only; needs checklist item C1 |

## Details

### 1. Docker GPU passthrough ✅ (was 🟡)
**Resolved 2026-11-06 (ADR 0001).** After removing the snap and running `nvidia-ctk runtime configure --runtime=docker`, `docker run --rm --gpus all nvidia/cuda:13.0.2-base-ubuntu24.04 nvidia-smi -L` prints the GPU. The probe commands below that use `--runtime=nvidia -e NVIDIA_VISIBLE_DEVICES=all` still work, but project code uses `--gpus all` / compose `deploy.resources.reservations.devices`. The `/tmp` mount issue no longer applies; `--user 1000:1000` is still required.

Original finding:
```
docker run --rm --gpus all nvidia/cuda:13.0.2-base-ubuntu24.04 nvidia-smi
→ OCI runtime create failed: … open /usr/bin/nvidia-cuda-mps-control: no such file or directory
docker run --rm --runtime=nvidia -e NVIDIA_VISIBLE_DEVICES=all nvidia/cuda:13.0.2-base-ubuntu24.04 nvidia-smi -L
→ GPU 0: NVIDIA GeForce RTX 5060 Ti (UUID: GPU-1e06e0cd-…)   ✅
docker run --rm --device nvidia.com/gpu=all …   → same error as --gpus
```
Cause: `/var/run/docker.sock` is served by the **snap** dockerd. The host CDI spec (`/var/run/cdi/nvidia.yaml`) references host paths that are invisible inside the snap confinement. The snap's own `nvidia` runtime (`/snap/docker/3618/usr/bin/nvidia-container-runtime`) works.
**Workaround (adopted for now):** every GPU container uses `--runtime=nvidia -e NVIDIA_VISIBLE_DEVICES=all` (compose: `runtime: nvidia` + env).
**Second snap pitfall:** bind mounts from `/tmp` are empty (private `/tmp`), so all mounts come from `$HOME`. Containers write root-owned files unless run with `--user 1000:1000`.
**Proper fix:** remove one of the two engines (checklist C4). Then `--gpus all` should work via `nvidia-ctk runtime configure`.

### 2. torch ✅
`venv: torch --index-url https://download.pytorch.org/whl/cu130` → `python -I docs/env/probes/probe_torch_ort.py`
torch 2.14.1+cu130, CUDA 13.0, capability (12, 0), arch list includes `sm_120`. 8192² fp16 matmul ran (probe observation ~50 TFLOPS); SDPA (flash enabled) ran.

### 3. ONNX Runtime GPU ✅
`onnxruntime-gpu 1.31.1` (PyPI default, CUDA 13 build) + `nvidia-cudnn-cu13 9.24` from the torch install.
`get_available_providers()` = `[TensorrtExecutionProvider, CUDAExecutionProvider, CPUExecutionProvider]`. A MatMul session with `providers=["CUDAExecutionProvider"]` ran on CUDA and matched NumPy (max abs diff 5.9e-3 on values ≈ 64: TF32-level, rel. ~1e-4).
Open: the TensorRT EP needs `tensorrt` libs (not installed). Check in Phase 5 (b05 is Extra).
Note for the harness: ORT can silently place unsupported nodes on CPU. b04 must log `session.get_providers()` and use `session_options.log_severity_level` / `disable_cpu_ep_fallback` (rule 6).

### 4./5. cuDF and cudf.pandas ✅
`uv pip install cudf-cu13 cudf-polars-cu13 polars --extra-index-url https://pypi.nvidia.com` → `probe_gpu_df.py`: groupby and `str.contains` correct.
`python -m cudf.pandas --profile script.py` prints a GPU/CPU call table and lists CPU fallbacks (probe: `Series.apply` fell back). a07 must run with `--profile` (or the `cudf.pandas.profiler` API) and store the fallback list in `extra`.

### 6. Polars GPU engine 🟡
`q.collect(engine=pl.GPUEngine(raise_on_fail=True))`: filter + `str.contains` + group_by ran on GPU and was correct. A `map_elements` UDF **raised `ComputeError`** instead of silently falling back, which is the behaviour we want.
**Constraint:** `cudf-polars-cu13 26.10.2` requires **polars 1.44.2**, whereas a free resolve gives **polars 2.1.0** (2.0 is final).
→ Fairness decision needed (ADR in Phase 1). Proposal: run *all* Polars contestants (a08–a12) on 1.44.2 for the like-for-like CPU-vs-GPU comparison, and add `a09b_polars2_eager` on 2.1.0 as a version-delta contestant.

### 7. llama.cpp prebuilt image ✅
```
docker run --user 1000:1000 --runtime=nvidia -e NVIDIA_VISIBLE_DEVICES=all -v $PWD/models/gguf:/models:ro \
  ghcr.io/ggml-org/llama.cpp:server-cuda -m /models/Qwen3-0.6B-Q8_0.gguf -ngl 99 -c 2048
```
Log: `build 12169 (3ed7284f2)`, `CUDA : ARCHS = 500,610,700,750,800,860,890,900,1200`, `using device CUDA0 (NVIDIA GeForce RTX 5060 Ti)`, every layer assigned to CUDA0. JSON-schema `response_format` returned valid JSON. The PLAN's "prebuilt binaries silently fall back to CPU" caveat **does not apply** to this image. The harness should still assert `offloaded`/device lines in the server log.
Note: the image ships a combined `llama` binary plus `llama-server`. There's no `/app/llama-cli` any more.

### 8. llama.cpp source build (sm_120) 🟡
```
docker run --rm -v ~/.cache/zeilenjoule/llama.cpp:/src -w /src nvidia/cuda:13.0.2-devel-ubuntu24.04 bash -c \
 'apt-get install cmake libssl-dev && cmake -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=120 -DLLAMA_CURL=OFF -DCMAKE_BUILD_TYPE=Release \
  && cmake --build build -j 14 --target llama-server llama-cli llama-bench'
```
Commit `6c01da6` (2026-11-06). Build time ≈ 5 min wall (-j 14).
**First attempt failed at link time:** `libggml-cuda.so: undefined reference to cuMemCreate, cuDeviceGet, …`. A non-GPU build container has no driver `libcuda.so.1`. **Fix:** inside the build container, `ln -sf /usr/local/cuda/lib64/stubs/libcuda.so /usr/local/cuda/lib64/stubs/libcuda.so.1` before linking (as upstream's Dockerfile does). The real driver lib is injected at runtime by `--runtime=nvidia`.
Runtime check (`llama-bench -m Qwen3-0.6B-Q8_0.gguf -ngl 99 -p 128 -n 32`, run in the `devel` image because `base` lacks `libgomp.so.1`): `ggml_cuda_init: found 1 CUDA devices`, backend `CUDA`, ngl 99, pp128 and tg32 completed (probe observation only: pp ~9.8k t/s, tg ~390 t/s, single rep, loaded machine. **Not a result.**)
To fix in Phase 1: install `git` in the build container (build info currently says `build: unknown (0)`; the harness needs the commit), and build into a slim runtime image (`nvidia/cuda:13.0.2-runtime` + `libgomp1`).
Decision for Track D: the PLAN wants a source build; both source and prebuilt work. Proposal: d02 uses the **source build** (pinned commit, `-DCMAKE_CUDA_ARCHITECTURES=120` only), and the prebuilt image is an optional control.

### 9. Ollama 🟡
`POST /api/generate {"model":"qwen3:8b","think":false,…}` → German answer; `ollama ps` → `qwen3:8b 6.0 GB 100% GPU`.
Installed **0.13.5**; latest is **v0.40.15** (2026-11-03). Models on the shortlist (Qwen3.5, Gemma 4, gpt-oss MXFP4 …) will probably need a newer Ollama.
Options: (a) upgrade the system install (sudo, checklist C7), or (b) sudo-free: run `ollama/ollama:<pinned>` in Docker with `--runtime=nvidia` on a different port. Preferred for reproducibility: (b) for d01 contestants, with the image digest pinned.
Existing local models: bge-m3, qwen3:8b, qwen3:14b, qwen3.6:35b-a3b (23 GB, does not fit in VRAM).

### 10. vLLM ✅
```
docker run --runtime=nvidia -e NVIDIA_VISIBLE_DEVICES=all --ipc=host -v $PWD/models/hf:/root/.cache/huggingface/hub \
  vllm/vllm-openai:v0.33.0 --model Qwen/Qwen3-0.6B --max-model-len 2048 --gpu-memory-utilization 0.45
```
`Using FLASH_ATTN attention backend out of potential backends: ['FLASH_ATTN', 'FLASHINFER', 'TRITON_ATTN', 'FLEX_ATTENTION']` + `FlashAttention version 2`. The PLAN's caveat ("FA unsupported on SM120 → TRITON_ATTN") is **outdated for v0.33.0**. Structured output (`response_format: json_schema` with enum) returned valid JSON; `chat_template_kwargs.enable_thinking=false` worked (34 completion tokens).
Caveats:
- Cold start ≈ 5 min (torch.compile + CUDA graph capture + FlashInfer autotune). Phase 1 must mount persistent `~/.cache/vllm` and `~/.cache/flashinfer` volumes. The harness measures this as the `load` stage.
- Image tags: stable releases are CUDA 13.0 (or `-cu129`). **Nightlies are `cu134` → need a newer driver.** Pin `v0.33.0` by digest.
- Still to test in Phase 7: AWQ-Marlin / NVFP4 / MXFP4 quant kernels on sm_120, and the TRITON_ATTN vs FLASH_ATTN energy comparison.

### 11. TEI ✅
```
docker run --user 1000:1000 --runtime=nvidia -e NVIDIA_VISIBLE_DEVICES=all -v $PWD/models/hf:/data \
  ghcr.io/huggingface/text-embeddings-inference:120-1.9.4 --model-id intfloat/multilingual-e5-small
```
`Starting FlashBert model on Cuda`, dtype float16, `/embed` → 384-d, L2 norm 1.0. HF now publishes `120-*` / `blackwell-latest` tags, so **no source build is needed** (PLAN §3.2 is outdated).
⚠ TEI warns that it uses **GeLU-tanh** instead of exact GeLU for models with `hidden_act=gelu`. That can matter for Track B's cosine ≥ 0.995 gate vs. sentence-transformers. Check with bge-m3 in Phase 5 before relying on it.

### 12. Qdrant GPU 🟡
```
docker run --runtime=nvidia -e NVIDIA_VISIBLE_DEVICES=all -e NVIDIA_DRIVER_CAPABILITIES=compute,graphics,utility \
  -e QDRANT__GPU__INDEXING=true qdrant/qdrant:v1.20.0-gpu-nvidia
```
Log: `Found GPU device: NVIDIA GeForce RTX 5060 Ti` … `Initialized GPU device`. It also lists `llvmpipe` (CPU Vulkan). Phase 6 must pin the device (`QDRANT__GPU__DEVICE_FILTER=nvidia`). Filtered query worked.
**Not yet verified:** an actual GPU HNSW build (2000 × 4-d points stayed below the indexing threshold, `indexed_vectors_count=0`). Do it in Phase 6 with real vectors and check the log for GPU indexing.
`graphics` capability is required for Vulkan.

### 13. Postgres 18 + pgvector ✅
`pgvector/pgvector:0.8.7-pg18-trixie` → PostgreSQL 18.6, `vector` 0.8.7. `halfvec(3)` + `hnsw (halfvec_cosine_ops) WITH (m=16, ef_construction=200)`, `SET hnsw.iterative_scan = relaxed_order` + filtered ORDER BY ran. `tsvector('german')` + GIN ran.
Observation (expected, for Track C): `plainto_tsquery('german','Leckagen')` matched „Leckage an der Druckluftleitung" but **not** „Druckluftleitungsleckage". Snowball doesn't decompound.

### 14. DuckDB 🟡
Python `duckdb 2.0.0` and CLI `v2.0.0`: `INSTALL/LOAD vss` and `fts` OK; HNSW index + `array_cosine_distance` query OK; `hnsw_enable_experimental_persistence` still exists (default false), which matters for c05.
**2.0 behaviour change:** `SELECT …, fts_main_d.match_bm25(id,'q') s FROM d WHERE s IS NOT NULL` now fails with *"Alias "s" referenced – but the expression has side effects"*. Wrap it in a subquery (as the probe now does). Same compound-word miss as Postgres.
Release asset names changed to `duckdb-cli-linux-amd64.tar.gz` (no zip).

### 15. NVML 🟡 (was ✅; Phase 2 calibration finding)
**Phase 2 (2026-11-06):** under a saturating fp16 GEMM the energy counter advanced at **146.3 W**, while `GetPowerUsage`, `POWER_INSTANT` and `POWER_AVERAGE` all read **180.0 W** (= enforced limit). Probe: `docs/env/probes/probe_nvml_load.py` → `docs/env/probe_nvml_load.out`. At idle both agree. Decision: GPU energy = integral of power samples, counter kept as a cross-check (ADR 0006).

Phase 0 finding:
`nvidia-ml-py` → `nvmlDeviceGetTotalEnergyConsumption` works as an unprivileged user. Idle 3 s: counter 6.02 W vs. polled 6.12 W. The power reading updates at only ~2 Hz at idle, so the **energy counter is the primary source**, and calibration (§5.7) must check the update rate under load. `nvidia-smi --query-gpu=total_energy_consumption` is not a valid field in 580; use NVML directly (the Go harness uses `github.com/NVIDIA/go-nvml`).

### 16. RAPL ❌ → 🟡
Zones: `package-0`, `core` (no `dram`, no `psys`). `energy_uj` is `0400 root`. Workaround = checklist C1. Until then, CPU energy is "nicht gemessen / not measured" and the harness records `cpu_pkg: null`.

## Corrections to PLAN.md assumptions
| PLAN says | Reality (2026-11-06) |
|---|---|
| Ubuntu 26.04 likely, driver 595 | Ubuntu **24.04.5**, driver **580** (CUDA 13.0 ceiling) |
| PCIe 5.0 x8 | runs at **PCIe 4.0 x8** (B760 slot) |
| vLLM: use TRITON_ATTN, FA unsupported | v0.33.0 picks FLASH_ATTN (FA2) automatically and it works |
| TEI: build `Dockerfile-cuda-blackwell` | official `120-1.9.4` image exists |
| llama.cpp prebuilt may fall back to CPU | official CUDA image ships sm_120 SASS |
| Polars 2.0 RC | Polars **2.1.0** final; GPU engine still pinned to 1.44.2 |
| RAPL readable? | root only |

## Phase 4 findings (Track A, 2026-11-07)

| Component | Status | Finding |
|---|---|---|
| Polars 2.1 `read_csv` cp1252 | ✅ | `encoding="windows-1252"` decodes correctly (incl. € = 0x80); `latin1` mis-decodes € as `\x80` |
| Polars 2.1 `scan_csv` cp1252 | 🟡 | lazy/streaming scanners accept **only UTF-8** → a10/a11 transcode cp1252→UTF-8 first (measured stage `transcode`) |
| Polars 2.1 `read_csv` empty fields | 🟡 | `missing_utf8_is_empty_string` removed in 2.x → `empty_string_is_null=False` |
| Polars `Expr.round` | 🟡 | default `half_to_even` → durations computed with exact integer arithmetic (spec: half away from zero) |
| DuckDB 2.0 `read_csv(encoding='windows-1252')` | ✅ | via `encodings` extension (installed in the contestant's build step, never during a measured run); `latin-1` rejects the file |
| DuckDB `regexp_extract` | 🟡 | returns `''` (not NULL) without a match → `nullif(…, '')`; `read_csv` yields NULL for empty fields → `coalesce(…, '')` |
| pandas 3.1 default string dtype | 🟡 | `dtype=str` gives the pyarrow-backed `str` dtype (PLAN assumed NumPy object strings) → a05 = defaults, ADR 0009 |
| pandas 3.1 `engine="pyarrow"` | 🟡 | `na_filter` unsupported; `dtype=str` overrides `dtype_backend="pyarrow"` → `dtype=pd.ArrowDtype(pa.string())` |
| cudf.pandas 26.10 (a07) | 🟡 | runs a05 correctly; CPU fallbacks at S: `read_csv` (cp1252), `str.normalize`, `str.findall`, `(?i)` `str.extract`, `str.fullmatch`, `to_datetime`, callable `str.replace`, `astype(object)`/`where`; 621 GPU / 163 CPU calls; counted per run (ADR 0009) |
| cudf-polars 26.10 GPU engine (a12) | ❌ | `raise_on_fail=True` raises for a10's plan: `normalize`, `extract_many`, `list.join`, `(?i)` regex, regex `replace`, `replace_strict` w/o default, strptime cache. Fails loudly as intended (rule 6) |
| cudf-polars 26.10, hand-split plan (a23) | 🟡 | GPU-capable after rewrites: `extract_groups` + struct field **crashes** at runtime (`All child columns must … have the same size and null count`) → one `str.extract` per group; `to_date` needs `cache=False`; regex `replace_all` unsupported (`literal=False`) → literal `replace_many`; `(?i)` unsupported → explicit case classes (Python IGNORECASE set incl. ſ/İ/ı); `str.zfill` raises on non-ASCII columns → `when(len==1)`. NFC, `list.eval`, `extract_many` stay on CPU (ADR 0011) |
| Polars Rust crate 0.55.2 (a15) | ✅ | all expressions of the Python plan exist (features `string_normalize`, `find_many`, `extract_groups`, `list_eval`, `replace`, `is_between`, …); CSV reader UTF-8 only → encoding_rs |
| Polars default engine | 🟡 | Python Polars 2.x `collect()` default `engine="auto"` = **streaming** engine; Rust crate 0.55 `Engine::Auto` = in-memory → a15 uses `Engine::Streaming` explicitly (ADR 0010) |
| arrow-rs 60.1 (a16/a17) | ✅ | arrow-csv reads empty fields as null (mapped to ""); parallel row-group encoding via `ArrowRowGroupWriterFactory` |
| bge-m3 + sentence-transformers 6.2 / torch 2.14 (b01–b03) | ✅ | fp16 vs fp32: mean cos 0.999998 (S). Repo has no safetensors, only `pytorch_model.bin` (loads with torch's `weights_only` default) |
| ORT 1.31.1 CUDA EP without torch import (b04) | 🟡 | CUDA EP fails to load (`Require cuDNN 9.* and CUDA 13.*`) and the session **silently runs on CPU** unless `onnxruntime.preload_dlls()` is called first (libs live in the nvidia-* wheels). The Phase 0 probe passed only because it imported torch first |
| ORT 1.31.1 `disable_cpu_ep_fallback` (b04) | 🟡 | unusable for bge-m3: ORT places 4 shape-arithmetic nodes (Gather/Unsqueeze/Equal/Where on `Shape` outputs) on the CPU by design → session refuses. Replaced by a static proof that the CPU nodes compute on shapes only + per-run logging (ADR 0013) |
| ORT transformers optimizer on the bge-m3 export | 🟡 | fuses 24 Attention / 24 BiasGelu / 48 SkipLayerNorm; the fused graph fails `onnx.checker` (LayerNormalization at opset 11) but loads in ORT; onnx rejects external data behind symlinks **and** hard links → plain copy of the export |
| ORT dynamic int8 on bge-m3 (b00) | 🟡 | default (incl. Gather + fused Attention): mean cos 0.985 (gate 0.995 ✗); MatMul only 0.991 ✗; MatMul except the 24 FFN down-projections 0.9966 ✓ (ADR 0013). `DefaultTensorType=FLOAT` needed after fusion |

| TEI `120-1.9.4` + bge-m3 (b06) | 🟡 | loads only safetensors (repo has `pytorch_model.bin`) → `server_prep hf512` converts; TEI has no length flag, but honours `max_seq_length`/`model_max_length` = 512 in the model dir (long docs: mean cos 0.99998). GeLU-tanh warning: effect negligible (mean cos 0.999986 on real). Binds 0.0.0.0 unless `--hostname 127.0.0.1`. `--max-client-batch-size` × requests above `--max-concurrent-requests` → HTTP 429 |
| vLLM v0.33.0 pooling runner + bge-m3 (b07) | ✅ | `--runner pooling` picks `pooling_type=CLS(source=sentence_transformers)`; `--max-model-len 512` + `truncate_prompt_tokens` truncate like the reference; runs as non-root with `HOME` on a mounted cache dir; cold start 56 s, warm (compile cache) ≈ 28 s |
| llama.cpp `server-cuda-b12169` + bge-m3 GGUF (b08) | 🟡 | converter of the same commit needs `conversion/` next to `convert_hf_to_gguf.py`; its pin `protobuf<5` breaks the tokenizer path (`runtime_version` import) → protobuf 5.29.5. Server **rejects** inputs > slot context (HTTP 400, no truncation) → client sends token ids. Device/offload lines only at `-lv 4` (which also logs every request); token-embedding table stays in host RAM by design. Packed sequences share one masked attention per micro-batch → `-fa on -ub 1024` (probe 73 s vs 90–180 s, unmeasured) |
| Rust `ort` 2.0.0-rc.15 `load-dynamic` (b10) | ✅ | loads the embed env's `libonnxruntime.so.1.31.1`; `ep::cuda::preload_dylibs` = Python's `preload_dlls`; `error_on_failure()` makes a failed CUDA EP registration fatal; output identical to b04 (mean cos 0.999997, same min) |

| DuckDB 2.0 `vss` filtered HNSW (c05) | ✅ | `WHERE … ORDER BY array_negative_inner_product(…) LIMIT 10` keeps the HNSW index scan (setting `hnsw_prefilter`, default true, "static table filters before searching"). Per-query thread fan-out dominates small probes: `SET threads = 1` for the query stage cut ≈ 14 ms → 2 ms per unfiltered query (unmeasured); numpy array parameters are faster than Python lists |
| sqlite-vec 0.1.9 `vec0` metadata columns (c06) | 🟡 | metadata columns reject NULL (`Expected text for TEXT metadata column …, received NULL`) → '' sentinel + lower date bound on every date filter; exact scan only |
| pgvector 0.8.7 planner (c03) | ✅ | filtered queries choose `Bitmap Index Scan` on the B-tree + exact `Sort` (`hnsw.iterative_scan = strict_order` set, but not needed); unfiltered → `Index Scan(docs_hnsw)`. Parallel HNSW build needs `--shm-size` |
| Qdrant v1.20.0-gpu-nvidia GPU HNSW build (c08) | ✅ (was 🟡 #12) | with real vectors every segment logs `building HNSW for N vectors with 8 CPUs and GPU NVIDIA GeForce RTX 5060 Ti` (needs `QDRANT__LOG_LEVEL=…,segment::index::hnsw_index=debug`; at INFO only device init is logged) |
| LanceDB 0.41 IVF_HNSW_SQ on bge-m3 (c07) | 🟡 | recall@10 (real, unmeasured): nprobes ¼ of 31 partitions 0.72–0.87, ½ 0.944, all 0.977; `refine_factor` did not help → all partitions probed. Selecting columns without `_distance` logs a deprecation warning per query |
| cuVS `cuvs-cu13` 26.10.0 (c02) | 🟡 | the wheel does not declare libcudart → `ImportError: libcuvs_c.so` (the loader swallows the real `libcudart.so.13` error); fix: explicit `cuda-toolkit[cudart]==13.5.0`. First CAGRA run compiles kernels (≈ 13 s once, then cached). Separate env `gpu_vs` so Track A's `gpu_df` lock stays unchanged; download ran at ≈ 100 KB/s |
| Tantivy 0.27.0 German stemmer (c11) | ✅ | `Stemmer(Language::German)` is `frostem` (Snowball main, German 3.x), not rust-stemmers → the Python reference pins `snowballstemmer` 3.1.1; tie-aware BM25 recall@10 vs reference 0.9995 (real) / 1.0 (M), unmeasured. Task name `c.bm25` rejected by the harness (letters only) → `c.lexical` |
| Qdrant 1.20 BM25 text index + `TextQuery` (c08 hybrid) | ✅ | native BM25 over a payload text index (`scoring: bm25`, Snowball German, `word` tokenizer); usable as an RRF prefetch (`RrfQuery(k=60)`). Recomputed fusion agreement 0.986 (real) / 0.970 (M): rank origin differs from RRF-from-1 |
| DuckDB `fts` (c05 hybrid) | 🟡 | defaults `ignore='(\\.\|[^a-z])+'` and `strip_accents=1` split/fold German words → `ignore='(\\.\|[^a-z0-9äöüß])+'`, `strip_accents=0`; `INSTALL fts` added to the build step |
| SQLite FTS5 + `vec0` in one statement (c06 hybrid) | 🟡 | vec0 rejects a window's `ORDER BY` pushed into the KNN (`Only a single 'ORDER BY distance' clause …`) → KNN in a `MATERIALIZED` CTE; FTS5 has no German stemmer; an empty MATCH is an error (queries without words) |
| LanceDB 0.41 hybrid (c07 hybrid) | 🟡 | `RRFReranker` = RRF k 60 from rank 1 (same as reference); the hybrid builder's inner legs log one deprecation warning per leg and query (2 000 lines per run) → `LANCEDB_LOG=error` (`LANCE_LOG`/`RUST_LOG` do not apply) |
| cuVS CAGRA determinism (c02) | 🟡 | two identical single-threaded runs differ in 136/250 unfiltered top-10 rows (recall unchanged); with 16 client threads a per-thread cuVS handle + explicit stream sync is needed (`Resources(stream=…)` from a cupy stream pointer segfaults) |
