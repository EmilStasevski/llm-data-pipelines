"""Track A `a.full` contestant a05_pandas_vectorized: pandas defaults (C parser, default dtypes) with
vectorised `.str` methods (ADR 0009). a07 runs this exact module under cudf.pandas."""

from __future__ import annotations

import sys
from typing import Any, cast

import pandas as pd
import pyarrow.parquet as pq

from zj_df.track_a.common import load_lexicon, parse_args
from zj_df.track_a.pandas_impl import PandasRules
from zjmark import result, stage

READ_OPTIONS: dict[str, Any] = {
    "sep": ";",
    "encoding": "cp1252",
    "dtype": str,
    "keep_default_na": False,
    "na_filter": False,
}


def run(argv: list[str] | None, prog: str, notes: str) -> dict[str, object]:
    a = parse_args(argv, prog)
    rules = PandasRules(load_lexicon(a.lexicon))
    with stage("read"):
        df = cast("pd.DataFrame", pd.read_csv(a.input, **READ_OPTIONS))  # pyright: ignore[reportArgumentType,reportCallIssue,reportUnknownMemberType]
    with stage("transform"):
        table = rules.transform(df)
    with stage("write"):
        pq.write_table(table, a.output, compression="zstd")  # pyright: ignore[reportUnknownMemberType]
    extra: dict[str, object] = {"pandas": pd.__version__, "string_dtype": str(df.dtypes.iloc[0])}
    return {"rows": table.num_rows, "notes": notes, "extra": extra}


def main(argv: list[str] | None = None) -> int:
    r = run(argv, "a05_pandas_vectorized", "pandas vectorised, default dtypes")
    result(rows=r["rows"], notes=r["notes"], extra=r["extra"])  # pyright: ignore[reportArgumentType]
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
