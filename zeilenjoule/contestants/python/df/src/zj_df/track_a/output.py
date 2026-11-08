"""Write a list of row dicts with the exact Track A schema (row-loop contestants)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from zj_df.track_a.common import SCHEMA


def write_rows(rows: list[dict[str, Any]], output: Path) -> None:
    rows.sort(key=lambda r: r["meldung_id"])
    output.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=SCHEMA), output, compression="zstd")  # pyright: ignore[reportUnknownMemberType]
