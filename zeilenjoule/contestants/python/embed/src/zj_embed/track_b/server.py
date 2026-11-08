"""Docker-served Track B contestants (b06 TEI, b07 vLLM, b08 llama.cpp): container lifecycle + HTTP client.

Each contestant owns its server (ADR 0015): `docker run` → wait for `/health` → check the log for the GPU
device lines (rule 6) → the `load` stage ends. The documents then go over HTTP with several requests in
flight, so the server's own dynamic batching does the work. The container is always removed afterwards,
and its log is copied to the contestant's stderr (= the harness run log).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Generator, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from zj_embed.track_b.common import DIM, REPO

HOST = "127.0.0.1"
HUB = REPO / "models" / "hub"  # mounted read-only at /hub


@dataclass(frozen=True)
class Server:
    name: str  # container name, e.g. zj-b06-tei
    image: str  # pinned by digest (rule 9)
    args: list[str]
    port: int
    gpu_log: str | None  # regex that must occur in the container log once ready (rule 6), None = none
    min_vram_mb: int  # GPU memory the server's processes must hold once ready (rule 6: weights on the GPU)
    mounts: list[tuple[Path, str]] = field(default_factory=lambda: [(HUB, "/hub")])
    rw_mounts: list[tuple[Path, str]] = field(default_factory=list[tuple[Path, str]])  # compile caches
    env: dict[str, str] = field(default_factory=dict[str, str])
    extra_docker: list[str] = field(default_factory=list[str])
    health: str = "/health"


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=check)


def docker_cmd(s: Server) -> list[str]:
    cmd = ["docker", "run", "-d", "--name", s.name, "--network", "host", "--runtime=nvidia",
           "-e", "NVIDIA_VISIBLE_DEVICES=all", *s.extra_docker]  # fmt: skip
    for src, dst in s.mounts:
        cmd += ["-v", f"{src}:{dst}:ro"]
    for src, dst in s.rw_mounts:
        src.mkdir(parents=True, exist_ok=True)
        cmd += ["-v", f"{src}:{dst}"]
    for k, v in s.env.items():
        cmd += ["-e", f"{k}={v}"]
    return [*cmd, s.image, *s.args]


def logs(s: Server) -> str:
    r = _docker("logs", s.name, check=False)
    return r.stdout + r.stderr


def wait_ready(s: Server, timeout_s: float) -> None:
    url = f"http://{HOST}:{s.port}{s.health}"
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout_s:
        state = _docker("inspect", "-f", "{{.State.Running}}", s.name, check=False).stdout.strip()
        if state != "true":
            raise RuntimeError(f"{s.name} exited during start-up:\n{logs(s)[-4000:]}")
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            pass
        time.sleep(0.2)
    raise TimeoutError(f"{s.name} not healthy after {timeout_s:.0f} s:\n{logs(s)[-4000:]}")


def container_vram_mb(s: Server) -> float:
    """GPU memory held by the container's processes (host NVML view, matched by host PID)."""
    import pynvml  # noqa: PLC0415

    top = _docker("top", s.name, "-eo", "pid", check=False).stdout.split()[1:]
    pids = {int(p) for p in top if p.isdigit()}
    pynvml.nvmlInit()
    try:
        h = pynvml.nvmlDeviceGetHandleByIndex(0)
        procs = pynvml.nvmlDeviceGetComputeRunningProcesses(h)
        return sum((p.usedGpuMemory or 0) for p in procs if p.pid in pids) / 2**20
    finally:
        pynvml.nvmlShutdown()


def require_gpu(s: Server) -> dict[str, object]:
    """Fail loudly unless the server holds its weights on the GPU (rule 6). Returns the evidence."""
    held = container_vram_mb(s)
    if held < s.min_vram_mb:
        raise RuntimeError(f"{s.name}: only {held:.0f} MiB on the GPU, expected ≥ {s.min_vram_mb} (CPU run?)")
    evidence: dict[str, object] = {"server_vram_mb": round(held)}
    if s.gpu_log:
        log = logs(s)
        m = re.search(s.gpu_log, log)
        if not m:
            raise RuntimeError(f"{s.name}: GPU marker /{s.gpu_log}/ not in the server log:\n{log[-4000:]}")
        evidence["gpu_log"] = m.group(0)
    return evidence


@contextmanager
def running(s: Server, timeout_s: float = 900) -> Generator[dict[str, object]]:
    """Start the container, wait until healthy and on the GPU; yields the GPU evidence."""
    _docker("rm", "-f", s.name, check=False)  # leftover from an aborted run
    _docker(*docker_cmd(s)[1:])
    try:
        wait_ready(s, timeout_s)
        yield require_gpu(s)
    finally:
        print(f"----- {s.name} container log -----\n{logs(s)}----- end -----", file=sys.stderr)
        _docker("rm", "-f", s.name, check=False)


def image_digest(image: str) -> str:
    return _docker("image", "inspect", "-f", "{{index .RepoDigests 0}}", image, check=False).stdout.strip()


def post(port: int, path: str, body: object, timeout_s: float = 600) -> Any:
    req = urllib.request.Request(
        f"http://{HOST}:{port}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} from {path}: {e.read()[:2000]!r}") from e


def embed_all(
    items: Sequence[Any], request: Callable[[list[Any]], list[list[float]]], chunk: int, in_flight: int
) -> np.ndarray:
    """Send `items` in chunks with `in_flight` concurrent requests; rows come back in input order,
    L2-normalised in fp32 (a no-op for servers that already normalise)."""
    out = np.empty((len(items), DIM), dtype=np.float32)
    starts = range(0, len(items), chunk)

    def one(s: int) -> None:
        part = list(items[s : s + chunk])
        vecs = np.asarray(request(part), dtype=np.float32)
        if vecs.shape != (len(part), DIM):
            raise RuntimeError(f"server returned shape {vecs.shape} for {len(part)} inputs")
        out[s : s + len(part)] = vecs

    with ThreadPoolExecutor(max_workers=in_flight) as pool:
        for f in [pool.submit(one, s) for s in starts]:
            f.result()  # re-raise the first error
    out /= np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-12)
    return out.astype(np.float16)


def uid_gid() -> str:
    return f"{os.getuid()}:{os.getgid()}"
