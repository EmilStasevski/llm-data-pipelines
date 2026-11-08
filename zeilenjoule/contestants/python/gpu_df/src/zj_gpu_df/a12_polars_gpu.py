"""Track A `a.full` contestant a12_polars_gpu: the a10 lazy plan (same PolarsRules expressions,
imported from contestants/python/df/src) collected on the RAPIDS GPU engine.

`GPUEngine(raise_on_fail=True)`: if any node of the plan is not supported on the GPU, the run fails
loudly instead of silently executing on the CPU (CLAUDE.md rule 6). Polars here is 1.44.2, pinned by
cudf-polars (ADR 0002); the version is recorded in the result.
"""

from __future__ import annotations

import sys
from pathlib import Path

DF_SRC = Path(__file__).resolve().parents[3] / "df" / "src"


def main(argv: list[str] | None = None) -> int:
    sys.path.insert(0, str(DF_SRC))
    import polars as pl  # noqa: PLC0415

    from zj_df.track_a.common import load_lexicon, parse_args  # noqa: PLC0415
    from zj_df.track_a.polars_impl import SCAN_OPTIONS, PolarsRules, transcode_to_utf8  # noqa: PLC0415
    from zjmark import result, stage  # noqa: PLC0415

    a = parse_args(argv, "a12_polars_gpu")
    rules = PolarsRules(load_lexicon(a.lexicon))
    engine = pl.GPUEngine(device=0, raise_on_fail=True)
    utf8 = a.output.with_name(a.output.stem + ".input.utf8.csv")
    with stage("transcode"):  # scan_csv reads UTF-8 only
        transcode_to_utf8(a.input, utf8)
    with stage("query"):
        out = rules.transform(pl.scan_csv(utf8, **SCAN_OPTIONS)).collect(engine=engine)
    utf8.unlink()
    with stage("write"):
        out.write_parquet(a.output, compression="zstd")
    import cudf_polars  # noqa: PLC0415

    result(
        rows=out.height,
        notes="polars lazy on GPUEngine(raise_on_fail=True)",
        extra={"engine": "gpu", "fallback_ops": [], "polars": pl.__version__,
               "cudf_polars": cudf_polars.__version__, "same_plan_as": "a10_polars_lazy"},
    )  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
