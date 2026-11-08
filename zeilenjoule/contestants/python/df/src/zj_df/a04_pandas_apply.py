"""Track A `a.full` contestant a04_pandas_apply: pandas apply(axis=1), common in the wild."""

from __future__ import annotations

import sys
from typing import Any, cast

import pandas as pd

from zj_df.track_a.common import load_lexicon, parse_args
from zj_df.track_a.output import write_rows
from zj_df.track_a.rowwise import Rules
from zjmark import result, stage


def main(argv: list[str] | None = None) -> int:
    a = parse_args(argv, "a04_pandas_apply")
    rules = Rules(load_lexicon(a.lexicon))
    with stage("read"):
        df = pd.read_csv(
            a.input, sep=";", encoding="cp1252", dtype=str, keep_default_na=False, na_filter=False
        )
    with stage("transform"):
        rows = cast("list[dict[str, Any]]", df.apply(rules.row, axis=1).tolist())
    with stage("write"):
        write_rows(rows, a.output)
    result(rows=len(rows), notes="apply", extra={"threads": 1})
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
