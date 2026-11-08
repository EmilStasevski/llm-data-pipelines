"""Track A `a.full` contestant a01_py_csv_loop: Pure-Python baseline: csv module + per-row functions."""

from __future__ import annotations

import csv
import sys
from typing import Any

from zj_df.track_a.common import load_lexicon, parse_args
from zj_df.track_a.output import write_rows
from zj_df.track_a.rowwise import Rules
from zjmark import result, stage


def main(argv: list[str] | None = None) -> int:
    a = parse_args(argv, "a01_py_csv_loop")
    rules = Rules(load_lexicon(a.lexicon))
    with stage("read"), a.input.open(encoding="cp1252", newline="") as f:
        records = list(csv.DictReader(f, delimiter=";", quotechar='"'))
    with stage("transform"):
        rows: list[dict[str, Any]] = [rules.row(r) for r in records]
    with stage("write"):
        write_rows(rows, a.output)
    result(rows=len(rows), notes="loop", extra={"threads": 1})
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
