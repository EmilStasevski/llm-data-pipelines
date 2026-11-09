"""Docker-served LLM engines (Track D): container lifecycle, GPU proof, JSON over HTTP.

Same pattern as the Track B servers (ADR 0015): `docker run --network host` → wait for the health endpoint →
check that the server's processes hold GPU memory (rule 6) → the `load` stage ends. The container is always
removed afterwards and its log is copied to stderr (= the harness run log).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

HOST = "127.0.0.1"
REPO = Path(__file__).resolve().parents[5]


@dataclass(frozen=True)
class Server:
    name: str  # container name
    image: str  # pinned by digest (rule 9)
    args: list[str]
    port: int
    min_vram_mb: int  # GPU memory the server must hold once ready (rule 6)
    mounts: list[tuple[Path, str]] = field(default_factory=list[tuple[Path, str]])  # read-only
    rw_mounts: list[tuple[Path, str]] = field(default_factory=list[tuple[Path, str]])
    env: dict[str, str] = field(default_factory=dict[str, str])
    extra_docker: list[str] = field(default_factory=list[str])
    health: str = "/health"


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=check)


def docker_cmd(s: Server) -> list[str]:
    cmd = [
        "docker",
        "run",
        "-d",
        "--name",
        s.name,
        "--network",
        "host",
        "--runtime=nvidia",
        "-e",
        "NVIDIA_VISIBLE_DEVICES=all",
        *s.extra_docker,
    ]
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
        if _docker("inspect", "-f", "{{.State.Running}}", s.name, check=False).stdout.strip() != "true":
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
    import pynvml  # pyright: ignore[reportMissingTypeStubs]  # noqa: PLC0415

    nv = cast("Any", pynvml)
    top = _docker("top", s.name, "-eo", "pid", check=False).stdout.split()[1:]
    pids = {int(p) for p in top if p.isdigit()}
    nv.nvmlInit()
    try:
        procs = cast("list[Any]", nv.nvmlDeviceGetComputeRunningProcesses(nv.nvmlDeviceGetHandleByIndex(0)))
        return sum(int(p.usedGpuMemory or 0) for p in procs if int(p.pid) in pids) / 2**20
    finally:
        nv.nvmlShutdown()


def require_gpu(s: Server) -> dict[str, object]:
    held = container_vram_mb(s)
    if held < s.min_vram_mb:
        raise RuntimeError(f"{s.name}: only {held:.0f} MiB on the GPU, expected ≥ {s.min_vram_mb} (CPU run?)")
    return {"server_vram_mb": round(held)}


@contextmanager
def running(s: Server, timeout_s: float = 900) -> Generator[dict[str, object]]:
    _docker("rm", "-f", s.name, check=False)
    _docker(*docker_cmd(s)[1:])
    try:
        wait_ready(s, timeout_s)
        yield require_gpu(s)
    finally:
        print(f"----- {s.name} container log -----\n{logs(s)[-200_000:]}----- end -----", file=sys.stderr)
        _docker("rm", "-f", s.name, check=False)


def exec_in(s: Server, *cmd: str) -> str:
    r = _docker("exec", s.name, *cmd, check=False)
    if r.returncode:
        raise RuntimeError(f"{s.name}: {' '.join(cmd)} failed:\n{r.stdout}{r.stderr}")
    return r.stdout


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


def post_stream(port: int, path: str, body: object, timeout_s: float = 600) -> float:
    """Seconds until the first streamed chunk with content (time to first token)."""
    req = urllib.request.Request(
        f"http://{HOST}:{port}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout_s) as r:
        for raw in r:
            line = raw.decode().strip().removeprefix("data:").strip()
            if not line or line == "[DONE]":
                continue
            chunk = cast("dict[str, Any]", json.loads(line))
            choices = cast("list[dict[str, Any]]", chunk.get("choices") or [{}])
            delta = cast("dict[str, Any]", choices[0].get("delta") or {})
            message = cast("dict[str, Any]", chunk.get("message") or {})
            if delta.get("content") or delta.get("reasoning_content") or message.get("content"):
                return time.perf_counter() - t0
    raise RuntimeError(f"no content streamed from {path}")


def uid_gid() -> str:
    return f"{os.getuid()}:{os.getgid()}"
