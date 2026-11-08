# ADR 0001: Docker engine = docker-ce (snap removed)

- Status: accepted (user, Gate 1, 2026-11-06). Execution pending user's sudo step.
- Context: docker-ce 29.9.3 and the docker snap 29.8.0 were both installed, and the snap owned `/var/run/docker.sock`. Consequences (COMPAT §1): `--gpus all` / CDI fails, bind mounts from `/tmp` are empty, and two engines make version fingerprints ambiguous.
- Decision: keep **docker-ce** (apt, Docker's repo) and remove the snap (`sudo snap remove --purge docker`). Configure the NVIDIA runtime with `nvidia-ctk runtime configure --runtime=docker`.
- Migration: snap-only data backed up to `~/docker-snap-migration/` (open-webui volume, local images, inspect dumps). `restore.sh` recreates the containers without sudo.
- Consequences:
  - Compose files use the standard `deploy.resources.reservations.devices` (`driver: nvidia, capabilities: [gpu]`) instead of `runtime: nvidia`.
  - The harness's `doctor` asserts `DockerRootDir` is not under `/snap/` and that `--gpus all` works.
  - Probe images (~30 GB) must be re-pulled into docker-ce. They're pinned by the digests recorded in ENVIRONMENT.md §8.
  - Keep `--user 1000:1000` for containers writing into `models/`/`data/` anyway (root-owned files are an engine-independent problem).
