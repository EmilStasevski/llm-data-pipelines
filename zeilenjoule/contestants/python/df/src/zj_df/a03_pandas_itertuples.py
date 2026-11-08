"""Track A `a.full` contestant a03_pandas_itertuples: pandas itertuples() row loop."""

from __future__ import annotations

import sys
from typing import Any

import pandas as pd

from zj_df.track_a.common import INPUT_COLUMNS, load_lexicon, parse_args
from zj_df.track_a.output import write_rows
from zj_df.track_a.rowwise import Rules
from zjmark import result, stage


def main(argv: list[str] | None = None) -> int:
    a = parse_args(argv, "a03_pandas_itertuples")
    rules = Rules(load_lexicon(a.lexicon))
    with stage("read"):
        df = pd.read_csv(
            a.input, sep=";", encoding="cp1252", dtype=str, keep_default_na=False, na_filter=False
        )
    with stage("transform"):
        rows: list[dict[str, Any]] = []
        for t in df.itertuples(index=False, name=None):
            rec: dict[str, Any] = dict(zip(INPUT_COLUMNS, t, strict=True))
            rows.append(rules.row(rec))
    with stage("write"):
        write_rows(rows, a.output)
    result(rows=len(rows), notes="itertuples", extra={"threads": 1})
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
