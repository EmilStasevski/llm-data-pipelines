"""Version fingerprint of the `gpu_df` env; with --gpu also proves cuDF + Polars GPU run on the device."""

from __future__ import annotations

import sys

from zjmark import canonical_json
from zjmark.envinfo import Fingerprint, fingerprint

DISTS = ["cudf-cu13", "cudf-polars-cu13", "pylibcudf-cu13", "rmm-cu13", "polars", "pandas", "zjmark"]


def _gpu_probe() -> dict[str, object]:
    import cudf  # noqa: PLC0415  (heavy, GPU-only import)
    import polars as pl  # noqa: PLC0415

    s = cudf.Series([1, 2, 3])
    lf = pl.LazyFrame({"a": [1, 2, 3]})
    # raise_on_fail=True: an unsupported op must raise instead of silently running on CPU (rule 6)
    got = lf.select(pl.col("a").sum()).collect(engine=pl.GPUEngine(raise_on_fail=True)).item()
    return {"cudf_sum": int(s.sum()), "polars_gpu_sum": int(got)}


def check(gpu: bool = False) -> Fingerprint:
    return fingerprint(DISTS, {"env": "gpu_df", **({"gpu": _gpu_probe()} if gpu else {})})


if __name__ == "__main__":
    print(canonical_json(check("--gpu" in sys.argv)))
