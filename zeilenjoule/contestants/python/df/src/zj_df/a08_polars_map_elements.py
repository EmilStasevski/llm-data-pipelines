"""Track A `a.full` contestant a08_polars_map_elements (ANTI-PATTERN, CLAUDE.md rule 5).

Polars as a container, but the logic is a Python UDF per row (`map_elements` over a struct): the GIL
serialises every row and Polars warns about it. Same row logic as a01–a04 (rowwise.Rules), so the
difference to a09–a11 is purely "native expressions vs Python per row".
"""

from __future__ import annotations

import os
import sys
import warnings
from typing import cast

from zj_df.track_a.common import INPUT_COLUMNS, SCHEMA, load_lexicon, parse_args
from zj_df.track_a.rowwise import Rules
from zjmark import result, stage


def main(argv: list[str] | None = None) -> int:
    a = parse_args(argv, "a08_polars_map_elements")
    if a.threads > 0:
        os.environ["POLARS_MAX_THREADS"] = str(a.threads)
    import polars as pl  # noqa: PLC0415

    from zj_df.track_a.polars_impl import CSV_OPTIONS  # noqa: PLC0415

    rules = Rules(load_lexicon(a.lexicon))
    empty = cast("pl.DataFrame", pl.from_arrow(SCHEMA.empty_table()))  # pyright: ignore[reportUnknownMemberType]
    out_dtype = pl.Struct(empty.schema)
    with stage("read"):
        df = pl.read_csv(a.input, **CSV_OPTIONS)
    # PolarsInefficientMapWarning is the point of this baseline
    with stage("transform"), warnings.catch_warnings():
        warnings.simplefilter("ignore")
        out = (
            df.select(r=pl.struct(INPUT_COLUMNS).map_elements(rules.row, return_dtype=out_dtype))
            .unnest("r")
            .sort("meldung_id")
        )
    with stage("write"):
        out.write_parquet(a.output, compression="zstd")
    result(rows=out.height, notes="polars map_elements (anti-pattern)", extra={"polars": pl.__version__})
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
