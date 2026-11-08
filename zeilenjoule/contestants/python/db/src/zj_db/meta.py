"""In-memory metadata filtering for the library contestants (FAISS, cuVS): numpy masks over docs.parquet."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np
import pyarrow as pa

from zj_db.common import Query

EPOCH = dt.date(1970, 1, 1)


@dataclass(frozen=True)
class MetaArrays:
    ort: np.ndarray  # object
    bezirk: np.ndarray  # object
    datum: np.ndarray  # int32 days since epoch
    has_datum: np.ndarray  # bool

    @classmethod
    def from_table(cls, t: pa.Table) -> MetaArrays:
        d = t.column("datum").combine_chunks()
        has = np.asarray(d.is_valid())
        days = np.asarray(d.cast(pa.int32()).fill_null(0))
        return cls(np.asarray(t.column("ort").to_numpy(zero_copy_only=False), dtype=object),
                   np.asarray(t.column("bezirk").to_numpy(zero_copy_only=False), dtype=object),
                   days.astype(np.int32), has)  # fmt: skip

    def mask(self, q: Query) -> np.ndarray | None:
        """Rows passing the filter, or None for an unfiltered query."""
        if q.unfiltered:
            return None
        m = np.ones(len(self.ort), dtype=bool)
        if q.ort is not None:
            m &= self.ort == q.ort
        if q.bezirk is not None:
            m &= self.bezirk == q.bezirk
        if q.has_date:
            m &= self.has_datum
            if q.date_from is not None:
                m &= self.datum >= (q.date_from - EPOCH).days
            if q.date_to is not None:
                m &= self.datum <= (q.date_to - EPOCH).days
        return m


def exact_subset(vectors: np.ndarray, rows: np.ndarray, q: np.ndarray, k: int) -> list[int]:
    """Exact top-k among `rows` (fp32 compute over fp16 storage), best first."""
    if len(rows) == 0:
        return []
    s = np.asarray(vectors[rows], dtype=np.float32) @ q
    kk = min(k, len(rows))
    part = np.argpartition(-s, kk - 1)[:kk]
    order = part[np.argsort(-s[part], kind="stable")]
    return rows[order].tolist()
