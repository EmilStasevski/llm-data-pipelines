"""Track A `a.full` contestant a02_pandas_iterrows.

ANTI-PATTERN baseline on purpose: pandas iterrows(), to show what the naive approach costs.
"""

from __future__ import annotations

import sys
from typing import Any

import pandas as pd

from zj_df.track_a.common import load_lexicon, parse_args
from zj_df.track_a.output import write_rows
from zj_df.track_a.rowwise import Rules
from zjmark import result, stage


def main(argv: list[str] | None = None) -> int:
    a = parse_args(argv, "a02_pandas_iterrows")
    rules = Rules(load_lexicon(a.lexicon))
    with stage("read"):
        df = pd.read_csv(
            a.input, sep=";", encoding="cp1252", dtype=str, keep_default_na=False, na_filter=False
        )
    with stage("transform"):
        rows: list[dict[str, Any]] = [rules.row(r) for _, r in df.iterrows()]  # the anti-pattern on purpose
    with stage("write"):
        write_rows(rows, a.output)
    result(rows=len(rows), notes="iterrows", extra={"threads": 1})
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
