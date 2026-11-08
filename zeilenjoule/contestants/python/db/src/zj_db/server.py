"""Docker lifecycle of the server contestants (pgvector, Qdrant), same pattern as Track B (ADR 0015).

The contestant starts its own container (pinned digest, `--network host`, bound to 127.0.0.1) inside stage
`load`, waits until `ready()` succeeds, and always removes it afterwards; the container log is copied to the
contestant's stderr (= the harness run log). Every run therefore starts from an empty store.
"""

from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Container:
    name: str
    image: str  # pinned by digest (rule 9)
    args: list[str] = field(default_factory=list[str])
    env: dict[str, str] = field(default_factory=dict[str, str])
    extra_docker: list[str] = field(default_factory=list[str])


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=check)


def docker_cmd(c: Container) -> list[str]:
    cmd = ["docker", "run", "-d", "--name", c.name, "--network", "host", *c.extra_docker]
    for k, v in c.env.items():
        cmd += ["-e", f"{k}={v}"]
    return [*cmd, c.image, *c.args]


def logs(c: Container) -> str:
    r = _docker("logs", c.name, check=False)
    return r.stdout + r.stderr


def image_digest(image: str) -> str:
    return _docker("image", "inspect", "-f", "{{index .RepoDigests 0}}", image, check=False).stdout.strip()


@contextmanager
def running(c: Container, ready: Callable[[], bool], timeout_s: float = 300) -> Generator[None]:
    _docker("rm", "-f", c.name, check=False)  # leftover from an aborted run
    _docker(*docker_cmd(c)[1:])
    try:
        t0 = time.monotonic()
        while True:
            if _docker("inspect", "-f", "{{.State.Running}}", c.name, check=False).stdout.strip() != "true":
                raise RuntimeError(f"{c.name} exited during start-up:\n{logs(c)[-4000:]}")
            try:
                if ready():
                    break
            except Exception:
                pass
            if time.monotonic() - t0 > timeout_s:
                raise TimeoutError(f"{c.name} not ready after {timeout_s:.0f} s:\n{logs(c)[-4000:]}")
            time.sleep(0.1)
        yield
    finally:
        print(f"----- {c.name} container log -----\n{logs(c)[-20000:]}----- end -----", file=sys.stderr)
        _docker("rm", "-f", c.name, check=False)
