# Gate 1: Checkliste für den Nutzer / User checklist

*Stand 2026-11-06. **Nichts davon wurde vom Agenten ausgeführt. / None of this was executed by the agent.***
Background: `docs/ENVIRONMENT.md`, `docs/COMPAT.md`.

## Required for meaningful measurements

- [x] **C1: Make RAPL readable** *(confirmed by `zj doctor` 2026-11-06)* (otherwise CPU energy stays "not measured")
  Non-persistent (until reboot):
  ```bash
  sudo chmod o+r /sys/class/powercap/intel-rapl:0/energy_uj /sys/class/powercap/intel-rapl:0:0/energy_uj
  ```
  Persistent alternative (udev rule; re-applies on every boot):
  ```bash
  echo 'SUBSYSTEM=="powercap", ACTION=="add", RUN+="/bin/chmod o+r /sys%p/energy_uj"' | sudo tee /etc/udev/rules.d/99-zeilenjoule-rapl.rules
  sudo udevadm control --reload && sudo udevadm trigger --subsystem-match=powercap
  ```
  Risk: world-readable RAPL is a minor power side channel. Acceptable on a personal dev box.

- [x] **C2: GPU persistence mode** *(confirmed by `zj doctor` 2026-11-06)* (stable idle power and init time; currently `Disabled`)
  ```bash
  sudo nvidia-smi -pm 1
  ```
  Not persistent across reboots. To persist: `sudo systemctl enable --now nvidia-persistenced`.

- [x] **C3: Calibration tool** *(confirmed by `zj doctor` 2026-11-06)*
  ```bash
  sudo apt install stress-ng
  ```
  Optional in the same call: `tmux` (convenient for long runs), `ninja-build`, `clang`.

- [x] **C4: Resolve the two Docker engines** *(done 2026-11-06, option A, ADR 0001)* (decision needed)
  Both `docker-ce` 29.9.3 (apt) and the `docker` snap 29.8.0 are installed; the snap currently owns `/var/run/docker.sock`. Effects: `--gpus all` fails, `/tmp` mounts are empty.
  **The existing containers (open-webui, mlflow, toyapp) and the 1.1 GB volume live in the snap's data root** (`/var/snap/docker/common/var-lib-docker`). Removing the snap loses them unless they're migrated first.
  - **Option A (recommended long term):** keep docker-ce, remove the snap after migrating what you need:
    ```bash
    sudo snap remove docker            # deletes snap data root! migrate first
    sudo nvidia-ctk runtime configure --runtime=docker
    sudo systemctl restart docker
    docker run --rm --gpus all nvidia/cuda:13.0.2-base-ubuntu24.04 nvidia-smi   # verify
    ```
  - **Option B (no change):** keep the setup as is. The project uses `--runtime=nvidia` / compose `runtime: nvidia` and mounts only from `$HOME`. This works today.
  Tell me A or B. With B I'll proceed without needing any action.

- [ ] **C5: Quiet machine during measured sessions.** Currently loadavg is 5–8 (gate is 1.0):
  ```bash
  sudo systemctl stop k3s          # restart afterwards: sudo systemctl start k3s
  ```
  (Or `/usr/local/bin/k3s-killall.sh` to also stop the pods.)

- [ ] **C6: Pause other workloads during measured sessions** (no sudo, but they're yours, so I won't touch them):
  - the `~/cytopk/benchmarks/sbi_flow/jax_slowdown.py` loop
  - containers `open-webui`, `anomaly-inspect-mlflow-1`, `toyapp` (`docker stop …`)
  - Chrome, if possible. Its GPU process shares the RTX 5060 Ti and affects idle power.
  The harness will refuse to start runs while `loadavg1 > 1.0` and will record the idle baseline. You don't need to do this now, only before Phase 4+ measurement sessions.

## Optional

- [x] ~~**C7: Ollama upgrade**~~ not needed (ADR 0004). 0.13.5 → v0.40.15 (needed for newer models in Track D)
  ```bash
  curl -fsSL https://ollama.com/install.sh | sh     # asks for sudo
  ```
  Sudo-free alternative I can use instead: a pinned `ollama/ollama` Docker image. **Preferred for reproducibility**; just say so.
- [ ] **C8: NVIDIA driver upgrade** (580 → 590+/595) to use CUDA ≥ 13.1 images (e.g. vLLM `cu134` nightlies).
  **Not needed now.** All probed stable images run on CUDA 13.0. I recommend staying on 580 for the project's duration (stable fingerprint).
- [ ] **C9: CPU governor** `performance` during benchmark sessions (currently `powersave`, EPP `power`):
  ```bash
  sudo cpupower frequency-set -g performance    # revert: -g powersave
  ```
  The harness records the governor either way. Decide whether benchmarks run on `powersave` (realistic default) or `performance`. **Recommendation:** `powersave` as primary, plus one `performance` comparison for the top contestants (energy-vs-time story).
- [ ] **C10: Cold-cache runs:** passwordless sudo only for drop_caches:
  ```bash
  echo 'emil ALL=(root) NOPASSWD: /usr/bin/tee /proc/sys/vm/drop_caches' | sudo tee /etc/sudoers.d/zeilenjoule-dropcaches
  sudo chmod 440 /etc/sudoers.d/zeilenjoule-dropcaches
  ```
- [ ] **C11: Wall-power ground truth.** A smart plug with a local API (Shelly Plus/Gen3 or Tasmota), PC only. Costs money, so it's entirely your call.
- [ ] **C12: k3s kubeconfig** (Phase 9 only): `/etc/rancher/k3s/k3s.yaml` is root-only. Later: `sudo k3s kubectl config view --raw > ~/.kube/config-k3s && chmod 600 ~/.kube/config-k3s`.

## Decisions (answered 2026-11-06)
1. C4 → **A** (docker-ce; snap removed). Backups + steps: `~/docker-snap-migration/README.md`. ADR 0001.
2. Polars → **latest stable only** (2.1.0 CPU; a12 GPU on 1.44.2 as required by stable cudf-polars; no delta contestant). ADR 0002.
3. Governor → **powersave primary + performance comparison**. ADR 0003.
4. Ollama → **pinned Docker image, no sudo**. ADR 0004.

## Original questions
1. **C4: Docker option A or B?**
2. **Polars versions:** the GPU engine (cudf-polars 26.10) pins polars 1.44.2, while CPU Polars is 2.1.0. Proposal: all Polars contestants on 1.44.2 (fair CPU vs. GPU), plus one extra `polars 2.1.0` contestant showing the version delta. OK?
3. **Governor policy (C9):** `powersave` primary + `performance` comparison?
4. **Ollama (C7):** system upgrade, or pinned Docker image (my preference)?
