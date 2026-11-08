"""Write generated tables in all formats (PLAN §6.1) and a manifest with checksums.

Formats per table and scale:
- <t>.cp1252.csv  ';', decimal comma, CRLF line endings (Windows/SAP export), cp1252, strict encoding
- <t>.utf8.csv    same content, UTF-8, LF
- <t>.parquet     visible columns as strings (zstd), identical content to the CSVs
- <t>_gold.parquet gold labels with typed columns (zstd)
"""

from __future__ import annotations

import csv
import hashlib
import io
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq


class TableWriter:
    """Streams chunks (dicts of equal-length column lists) into all formats."""

    def __init__(self, outdir: Path, table: str, visible: Sequence[str], gold_schema: pa.Schema) -> None:
        outdir.mkdir(parents=True, exist_ok=True)
        self.paths = {
            "cp1252.csv": outdir / f"{table}.cp1252.csv",
            "utf8.csv": outdir / f"{table}.utf8.csv",
            "parquet": outdir / f"{table}.parquet",
            "gold.parquet": outdir / f"{table}_gold.parquet",
        }
        self.visible = list(visible)
        self.rows = 0
        self._cp = self.paths["cp1252.csv"].open("w", encoding="cp1252", errors="strict", newline="")
        self._u8 = self.paths["utf8.csv"].open("w", encoding="utf-8", newline="")
        self._cpw = csv.writer(self._cp, delimiter=";", quotechar='"', lineterminator="\r\n")
        self._u8w = csv.writer(self._u8, delimiter=";", quotechar='"', lineterminator="\n")
        for w in (self._cpw, self._u8w):
            w.writerow(self.visible)
        vis_schema = pa.schema([(c, pa.string()) for c in self.visible])
        self._pq = pq.ParquetWriter(self.paths["parquet"], vis_schema, compression="zstd")
        self._gq = pq.ParquetWriter(self.paths["gold.parquet"], gold_schema, compression="zstd")
        self._vis_schema, self._gold_schema = vis_schema, gold_schema

    def write(self, vis: dict[str, list[Any]], gold: dict[str, list[Any]]) -> None:
        rows = list(zip(*(vis[c] for c in self.visible), strict=True))
        self._cpw.writerows(rows)  # raises UnicodeEncodeError on any non-cp1252 character
        self._u8w.writerows(rows)
        self._pq.write_table(pa.Table.from_pydict(vis, schema=self._vis_schema))
        self._gq.write_table(pa.Table.from_pydict(gold, schema=self._gold_schema))
        self.rows += len(rows)

    def close(self) -> dict[str, dict[str, Any]]:
        for f in (self._cp, self._u8):
            f.close()
        self._pq.close()
        self._gq.close()
        return {
            name: {"path": p.name, "bytes": p.stat().st_size, "sha256": sha256_file(p)}
            for name, p in self.paths.items()
        }


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        while b := f.read(1 << 20):
            h.update(b)
    return h.hexdigest()


def write_xlsx_sample(path: Path, header: Sequence[str], rows: Iterable[Sequence[Any]]) -> None:
    """Small Excel sample for the demo (openpyxl, write-only mode)."""
    from openpyxl import Workbook  # noqa: PLC0415  (only needed for the demo sample)

    wb = Workbook(write_only=True)
    ws = wb.create_sheet("Störmeldungen")
    ws.append(list(header))
    for r in rows:
        ws.append(list(r))
    buf = io.BytesIO()
    wb.save(buf)
    path.write_bytes(buf.getvalue())


# pyarrow-stubs leave pq.read_table/write_table partially untyped (IO[Unknown] parameters); these two
# wrappers keep strict typing everywhere else.
def read_table(path: Path) -> pa.Table:
    return pq.read_table(path)  # pyright: ignore[reportUnknownMemberType]


def write_table(table: pa.Table, path: Path) -> None:
    pq.write_table(table, path, compression="zstd")  # pyright: ignore[reportUnknownMemberType]
