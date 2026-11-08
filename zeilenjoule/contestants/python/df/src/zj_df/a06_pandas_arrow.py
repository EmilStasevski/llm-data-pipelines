"""Track A `a.full` contestant a06_pandas_arrow: pandas with the pyarrow CSV engine and
`dtype_backend="pyarrow"` (ArrowDtype columns, multithreaded parse); same transform as a05 (ADR 0009)."""

from __future__ import annotations

import sys
from typing import Any, cast

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from zj_df.track_a.common import load_lexicon, parse_args
from zj_df.track_a.pandas_impl import PandasRules
from zjmark import result, stage

READ_OPTIONS: dict[str, Any] = {
    "sep": ";",
    "encoding": "cp1252",
    "dtype": pd.ArrowDtype(pa.string()),  # dtype=str would override the backend with the default str
    "keep_default_na": False,
    "engine": "pyarrow",
    "dtype_backend": "pyarrow",
}


def main(argv: list[str] | None = None) -> int:
    a = parse_args(argv, "a06_pandas_arrow")
    if a.threads > 0:
        pa.set_cpu_count(a.threads)
        pa.set_io_thread_count(a.threads)
    rules = PandasRules(load_lexicon(a.lexicon))
    with stage("read"):
        df = cast("pd.DataFrame", pd.read_csv(a.input, **READ_OPTIONS))  # pyright: ignore[reportArgumentType,reportCallIssue,reportUnknownMemberType]
    with stage("transform"):
        table = rules.transform(df)
    with stage("write"):
        pq.write_table(table, a.output, compression="zstd")  # pyright: ignore[reportUnknownMemberType]
    result(
        rows=table.num_rows,
        notes="pandas pyarrow engine + ArrowDtype",
        extra={"pandas": pd.__version__, "string_dtype": str(df.dtypes.iloc[0])},
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
