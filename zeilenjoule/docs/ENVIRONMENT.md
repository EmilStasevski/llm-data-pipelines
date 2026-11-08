# Umgebung / Environment — Phase 0 audit

*Erfasst / recorded: 2026-11-06 (Phase 0). Rohdaten / raw outputs: `docs/env/`. Probe-Skripte: `docs/env/probes/`.*

> Alle Werte unten stammen aus Befehlsausgaben in `docs/env/`. Nichts ist geschätzt.
> All values below come from command outputs in `docs/env/`. Nothing is estimated.

## 1. Host

| Item | Value | Source |
|---|---|---|
| OS | **Ubuntu 24.04.5 LTS (noble)**, not 26.04 | `env/lsb_release.txt` |
| Kernel | 6.17.0-1023-oem, PREEMPT_DYNAMIC | `env/uname.txt` |
| Board / BIOS | Gigabyte B760 DS3H AX, BIOS F12 | `env/system_extra.txt` |
| CPU | **Intel Core i5-14400F** (Raptor Lake): 6 P-cores (HT) + 4 E-cores = **10 cores / 16 threads**; max 4.7 GHz; L3 20 MiB; 1 NUMA node | `env/lscpu.txt` |
| CPU topology | P-core threads = CPU 0–11, E-cores = CPU 12–15 (`/sys/devices/cpu_core`, `cpu_atom`) | `env/cpu_power.txt` |
| CPU power limits (RAPL) | PL1 (long_term) **125 W**, PL2 (short_term) **150 W**. Set by the board; Intel's spec is 65 W base / 148 W turbo | `env/system_extra.txt` |
| RAM | 31 GiB (+ 8 GiB swap), swappiness 60, THP `madvise` | `env/free.txt` |
| Disk | Lexar NM790 1 TB NVMe; `/` 937 G, **232 G free** (74 % used). Just above the ≥ 200 GB recommendation | `env/df.txt` |
| cgroup | v2 | |

## 2. GPU

| Item | Value | Source |
|---|---|---|
| GPU | NVIDIA GeForce RTX 5060 Ti, 16311 MiB, **compute capability 12.0** | `env/nvidia-smi-query.csv` |
| Driver | **580.178.04**, open kernel module (`nvidia-driver-580-open`). Driver API = **CUDA 13.0** | `env/nvidia-smi.txt`, `env/proc-driver-nvidia.txt` |
| Power limit | 180 W (default 180 W, min 150 W, max 216 W) | `env/nvml_energy.txt` |
| Persistence mode | **Disabled** | `env/nvidia-smi-query.csv` |
| PCIe link | **Gen 4 x8** (the card supports Gen 5, but the B760 slot tops out at Gen 4) | `env/cpu_power.txt` |
| Display | The GPU also drives the desktop (Xorg, gnome-shell, Chrome): about **470 MiB VRAM** in use and ~6 W at idle (P8) | `env/nvidia-smi.txt` |
| CUDA toolkit on host | **none** (`nvcc` missing, no `/usr/local/cuda`). Builds run in `nvidia/cuda:13.0.2-devel` containers instead | `env/nvcc.txt` |
| NVML energy counter | `nvmlDeviceGetTotalEnergyConsumption` **works without root**. Idle cross-check: counter 6.02 W vs. sampled 6.12 W over 3 s | `env/probe_nvml.out` |
| NVML power update rate | At idle `nvmlDeviceGetPowerUsage` changed only **6 times in 3 s** (~2 Hz) despite 297 polls. So the energy counter is the primary GPU energy source, and 20 Hz power samples are only for the timeline | `env/probe_nvml.out` |

## 3. CPU energy (RAPL) and frequency

| Item | Value |
|---|---|
| RAPL zones | `intel-rapl:0` = `package-0`, `intel-rapl:0:0` = `core`. **No `dram`, no `psys`** zone |
| `energy_uj` readable as user? | **No.** Mode `-r--------` root only → checklist item C1 |
| Counter wrap | `max_energy_range_uj` = 262 143 328 850 µJ ≈ 262 kJ, which is ~29 min at 150 W. The harness must handle wrap-around |
| Scaling driver | `intel_pstate` (active), governors available: `performance`, `powersave` |
| Governor | **`powersave`** on all 16 CPUs, EPP = `power` |
| Turbo | enabled (`no_turbo=0`) |
| SMT | on |
| `perf_event_paranoid` | 4 (perf counters are restricted; not needed for the plan) |

## 4. Toolchains

> **Update Phase 1 (2026-11-06):** user-level installs: uv-managed **CPython 3.12.14** (project interpreter), **golangci-lint 2.15.0**, **govulncheck 1.9.0** (both `~/go/bin`, which is *not* on the default PATH; the Taskfile resolves them via `go env GOPATH`). User actions: RAPL `energy_uj` is now readable, persistence mode is **Enabled**, **stress-ng 0.17.06** is installed (all confirmed by `zj doctor`). Rust pinned via `rust-toolchain.toml` = 1.99.1.

| Tool | Version | Path / note |
|---|---|---|
| Python | 3.12.3 (system `/usr/bin/python3.12`); 3.13.13 (miniforge, first on PATH) | Use `uv` with `-p 3.12`. Don't rely on the miniforge `python3` |
| uv | 0.12.21 | `~/.local/bin` |
| Rust | rustc 1.99.1, cargo 1.99.1, rustup 1.29.2 (clippy, rustfmt present) | `~/.cargo/bin` |
| Go | **1.27.3** (snap, classic) | `/snap/bin/go` |
| gcc / g++ | 13.3.0 | |
| cmake | 3.28.3 | |
| clang | **missing** | optional, see checklist |
| ninja | **missing** | optional |
| go-task | **3.54.0**, installed in this session via `go install …/task/v3/cmd/task@latest` | `~/go/bin/task` (on PATH) |
| DuckDB CLI | **2.0.0** (Cyanoptera) d54cbbe3df, installed in this session from the GitHub release | `~/.local/bin/duckdb` |
| sqlite3 | 3.45.1 | |
| jq / curl / git | 1.7 / present / 2.43.0 | |
| tmux | **missing**. Background runs use `nohup … > logs/*.log` instead | |
| stress-ng | **missing** (needed for calibration §5.7) → checklist | |
| cpupower | present (`/usr/bin/cpupower`) | |
| vulkaninfo | missing (not needed: Qdrant logs the Vulkan device itself) | |
| Docker | **two engines installed** (see §5) | |
| NVIDIA Container Toolkit | 1.20.1 (host package) | |
| Ollama | **0.13.5** (system service). The latest release is v0.40.15 (2026-11-03) | `/usr/local/bin/ollama` |
| k3s | v1.36.5+k3s1, **running** as a system service | Phase 9 target is already installed |

## 5. Docker

> **Update 2026-11-06 18:20 (ADR 0001):** the snap engine has been removed (`snap remove --purge docker`) and the NVIDIA runtime configured in docker-ce (`/etc/docker/daemon.json`). Now: **docker-ce 29.9.3**, root `/var/lib/docker`, runtimes `nvidia runc`. `docker run --rm --gpus all nvidia/cuda:13.0.2-base-ubuntu24.04 nvidia-smi -L` → `GPU 0: NVIDIA GeForce RTX 5060 Ti` ✅. open-webui, toyapp and anomaly-inspect-mlflow were restored from `~/docker-snap-migration/`. The text below describes the **original** state found by the audit.

### 5.1 Original state (audit): two engines installed

- Packages: `docker-ce` 29.9.3 (Docker apt repo) **and** the `docker` snap 29.8.0 (rev 3618). Both `docker.service` and `snap.docker.dockerd` are active.
- `/var/run/docker.sock` is served by the **snap** daemon (`docker info` → Server 29.8.0, root dir `/var/snap/docker/common/var-lib-docker`).
- Consequences found during the probes:
  1. `docker run --gpus all …` **fails** with `open /usr/bin/nvidia-cuda-mps-control: no such file or directory`. The host CDI spec `/var/run/cdi/nvidia.yaml` points at host paths the snap sandbox can't see.
     **Workaround that works:** `--runtime=nvidia -e NVIDIA_VISIBLE_DEVICES=all` (the snap's bundled runtime). In compose files: `runtime: nvidia`.
  2. Bind mounts from **`/tmp` arrive empty**, because the snap has a private `/tmp`. All mounts must come from `$HOME`.
  3. Containers run as root write root-owned files into bind mounts (seen in `models/hf`). Always use `--user 1000:1000` where the image allows it.
- User `emil` is in the `docker` group (no sudo needed for docker).
- Already running (not from this project): `open-webui`, `anomaly-inspect-mlflow-1`, `toyapp`.

## 6. Background load (relevant for measurement hygiene)

At audit time **loadavg was 5–8**, well above the `load_guard.max_loadavg1: 1.0` threshold. Sources:
- `~/cytopk/benchmarks/sbi_flow/jax_slowdown.py`: another project's benchmark loop (repeated short Python processes at 200–300 % CPU)
- `k3s server` (~8 % CPU constantly) plus its pods
- 3 Docker containers (open-webui, mlflow, toyapp)
- Chrome (several renderers + GPU process), gnome-shell on the same GPU
- 2 other Claude Code sessions

Measured benchmark sessions need these stopped. See checklist items C5/C6.

## 7. Versions resolved by probes (Python, 2026-11-06)

| Package | Version | Env |
|---|---|---|
| torch | 2.14.1+cu130 (arch list includes sm_120) | probe `embed` |
| onnxruntime-gpu | 1.31.1 (providers: TensorRT, CUDA, CPU); cudnn-cu13 9.24.0.43 | probe `embed` |
| cudf-cu13 / cudf-polars-cu13 / rmm-cu13 | 26.10.2 / 26.10.2 / 26.10.0 | probe `gpu_df` |
| polars (GPU env) | **1.44.2** (pinned by cudf-polars 26.10) | probe `gpu_df` |
| pandas (GPU env) | 3.0.6 | probe `gpu_df` |
| polars (CPU env) | **2.1.0** | probe `df` |
| pandas (CPU env) | 3.1.1 | probe `df` |
| pyarrow | 26.0.1 | probe `df` |
| duckdb (Python) | 2.0.0 | probe `df` |

Probe venvs were throwaway (scratchpad). Phase 1 creates the real locked uv projects.

## 8. Container images used by the probes (digests)

| Image | Digest | CUDA in image |
|---|---|---|
| `nvidia/cuda:13.0.2-base-ubuntu24.04` | `sha256:2ab6381d970b211fb93853796dc6707eb8a72575a375c422b17cf4d8b2641701` | 13.0.2 |
| `nvidia/cuda:13.0.2-devel-ubuntu24.04` | `sha256:5dc1bca23d05bd37b011be68ec470c03b403a5da07ec3a86e41af9470e9d0cc6` | 13.0.2 |
| `pgvector/pgvector:0.8.7-pg18-trixie` | `sha256:5895949dfcdf308eb0cd31c950c93c14895c77f33fbedfecf1fa511ab1d4bcd2` | – |
| `vllm/vllm-openai:v0.33.0` | `sha256:d825baf9ce4b1b6bfc5ecc7b79880c7e6748c65a72d697ce3d47607aafa84c6e` | 13.0.2 |
| `ghcr.io/ggml-org/llama.cpp:server-cuda` (build 12169, 3ed7284f2) | `sha256:5bb779a449a18d8f4333e9814e887746342fe555c528f5eabc92b2093e32673a` | 12.8.1 |
| `ghcr.io/huggingface/text-embeddings-inference:120-1.9.4` | `sha256:bd8e5b1954146f7fe8590b64b959bc194433c6c38c036592a84d736841ca9400` | 12.9 |
| `qdrant/qdrant:v1.20.0-gpu-nvidia` | `sha256:002d66bfe0d7115eb4bb60beaa90c21d649e7356756737f1904481caa72cec67` | – (Vulkan) |

**Driver ceiling:** driver 580 = CUDA 13.0. The newest vLLM nightlies are tagged `cu134` (CUDA 13.4) and would need a newer driver (≥ 590-series). Stay on images ≤ CUDA 13.0, or upgrade the driver (checklist item C8, optional).
