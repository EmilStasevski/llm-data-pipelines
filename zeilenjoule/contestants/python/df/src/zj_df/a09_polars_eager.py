"""Track A `a.full` contestant a09_polars_eager: Polars eager API, native expressions only."""

from __future__ import annotations

import os
import sys

from zj_df.track_a.common import load_lexicon, parse_args
from zjmark import result, stage


def main(argv: list[str] | None = None) -> int:
    a = parse_args(argv, "a09_polars_eager")
    if a.threads > 0:
        os.environ["POLARS_MAX_THREADS"] = str(a.threads)  # must be set before polars is imported
    import polars as pl  # noqa: PLC0415

    from zj_df.track_a.polars_impl import CSV_OPTIONS, PolarsRules  # noqa: PLC0415

    rules = PolarsRules(load_lexicon(a.lexicon))
    with stage("read"):
        df = pl.read_csv(a.input, **CSV_OPTIONS)
    with stage("transform"):
        out = rules.transform(df)
    with stage("write"):
        out.write_parquet(a.output, compression="zstd")
    result(
        rows=out.height,
        notes="polars eager",
        extra={"threads": pl.thread_pool_size(), "polars": pl.__version__},
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
