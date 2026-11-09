"""Version fingerprint of the `llm-clients` env."""

from __future__ import annotations

import sys

from zjmark import canonical_json
from zjmark.envinfo import Fingerprint, fingerprint

DISTS = ["httpx", "pydantic", "jsonschema", "nvidia-ml-py", "zjmark"]


def check(gpu: bool = False) -> Fingerprint:
    del gpu  # CPU-only env (talks to servers over HTTP)
    return fingerprint(DISTS, {"env": "llm-clients"})


if __name__ == "__main__":
    print(canonical_json(check("--gpu" in sys.argv)))
