"""Track A `a.full` contestant a11_polars_streaming: lazy plan executed by the streaming engine into
sink_parquet (bounded memory; read, transform and write fused in one stage)."""

from __future__ import annotations

import os
import sys

from zj_df.track_a.common import load_lexicon, parse_args
from zjmark import result, stage


def main(argv: list[str] | None = None) -> int:
    a = parse_args(argv, "a11_polars_streaming")
    if a.threads > 0:
        os.environ["POLARS_MAX_THREADS"] = str(a.threads)
    import polars as pl  # noqa: PLC0415

    from zj_df.track_a.polars_impl import SCAN_OPTIONS, PolarsRules, transcode_to_utf8  # noqa: PLC0415

    rules = PolarsRules(load_lexicon(a.lexicon))
    utf8 = a.output.with_name(a.output.stem + ".input.utf8.csv")
    with stage("transcode"):  # scan_csv reads UTF-8 only (Polars 2.1)
        transcode_to_utf8(a.input, utf8)
    with stage("sink"):
        rules.transform(pl.scan_csv(utf8, **SCAN_OPTIONS)).sink_parquet(a.output, compression="zstd")
    utf8.unlink()
    rows = pl.scan_parquet(a.output).select(pl.len()).collect().item()  # metadata read, cheap
    result(
        rows=int(rows),
        notes="polars streaming",
        extra={"threads": pl.thread_pool_size(), "polars": pl.__version__},
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
