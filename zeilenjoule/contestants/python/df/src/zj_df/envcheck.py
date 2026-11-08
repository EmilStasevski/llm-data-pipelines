"""Version fingerprint of the `df` env: `uv run --project contestants/python/df python -m zj_df.envcheck`."""

from __future__ import annotations

import sys

from zjmark import canonical_json
from zjmark.envinfo import Fingerprint, fingerprint

DISTS = ["polars", "pandas", "pyarrow", "duckdb", "numpy", "zjmark"]


def check(gpu: bool = False) -> Fingerprint:
    del gpu  # CPU-only env
    return fingerprint(DISTS, {"env": "df"})


if __name__ == "__main__":
    print(canonical_json(check("--gpu" in sys.argv)))
