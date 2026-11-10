"""Version fingerprint of the `e2e` env (the pipelines call the other envs' contestants as subprocesses)."""

from __future__ import annotations

import sys

from zjmark import canonical_json
from zjmark.envinfo import Fingerprint, fingerprint

DISTS = ["sqlglot", "duckdb", "faiss-cpu", "psycopg", "qdrant-client", "pandas", "numpy", "pyarrow", "zjmark"]


def check(gpu: bool = False) -> Fingerprint:
    del gpu  # orchestration only; GPU work happens in the called contestants and servers
    return fingerprint(DISTS, {"env": "e2e"})


if __name__ == "__main__":
    print(canonical_json(check("--gpu" in sys.argv)))
