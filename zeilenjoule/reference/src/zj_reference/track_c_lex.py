"""Track C lexical + hybrid reference (spec/track_c.md §c.lexical / §c.hybrid).

Slow and obviously correct:
- **Analyzer** = Tantivy's text pipeline, written out: split on every non-alphanumeric character
  (`SimpleTokenizer`), drop tokens of ≥ 40 bytes (`RemoveLongFilter`), lower-case (`LowerCaser`), German
  Snowball stem (`Stemmer(German)`: Tantivy 0.27 ships `frostem` = current Snowball German, here
  `snowballstemmer` 3.1.1). No stop words, no decompounding.
- **BM25** (Robertson/Lucene/Tantivy): k1 = 1.2, b = 0.75, idf = ln(1 + (N - n + 0.5) / (n + 0.5)), query =
  the *set* of its analysed terms (OR), score = Σ idf · tf (k1 + 1) / (tf + k1 (1 - b + b · dl / avgdl)).
  N, n, avgdl are corpus-wide (filters do not change them). Only documents with score > 0 are hits.
- **RRF** (k = 60, ranks from 1) over the dense top-`DEPTH` and the BM25 top-`DEPTH`, both inside the filter.

Ties always go to the lower row index.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import snowballstemmer  # pyright: ignore[reportMissingTypeStubs]

K1, B = 1.2, 0.75
MAX_TOKEN_BYTES = 40
RRF_K = 60
DEPTH = 100  # per-leg candidate depth for c.hybrid
_SPLIT = re.compile(r"[^\w]|_")  # \w minus '_' = Unicode letters + digits (Rust char::is_alphanumeric)


class Analyzer:
    def __init__(self) -> None:
        self._stem = snowballstemmer.stemmer("german")  # pyright: ignore[reportUnknownMemberType]
        self._cache: dict[str, str] = {}

    def __call__(self, text: str) -> list[str]:
        out: list[str] = []
        for tok in _SPLIT.split(text):
            if not tok or len(tok.encode("utf-8")) >= MAX_TOKEN_BYTES:
                continue
            s = self._cache.get(tok)
            if s is None:
                s = str(self._stem.stemWord(tok.lower()))  # pyright: ignore[reportUnknownMemberType,reportUnknownArgumentType]
                self._cache[tok] = s
            out.append(s)
        return out


@dataclass(frozen=True)
class Bm25Index:
    """Column-wise postings: term t's documents are doc[ptr[t]:ptr[t+1]] with term frequencies tf[...]."""

    vocab: dict[str, int]
    ptr: np.ndarray  # int64 (T+1,)
    doc: np.ndarray  # int32
    tf: np.ndarray  # float32
    dl: np.ndarray  # float32 (N,) tokens per document
    avgdl: float

    @property
    def n(self) -> int:
        return len(self.dl)

    def save(self, path: Path) -> None:
        np.savez(path, ptr=self.ptr, doc=self.doc, tf=self.tf, dl=self.dl,
                 vocab=np.array(json.dumps(self.vocab, ensure_ascii=False)))  # fmt: skip

    @classmethod
    def load(cls, path: Path) -> Bm25Index:
        z = np.load(path, allow_pickle=False)
        dl = z["dl"]
        return cls(json.loads(str(z["vocab"])), z["ptr"], z["doc"], z["tf"], dl, float(dl.mean()))

    def scores(self, terms: Iterable[str]) -> np.ndarray:
        """BM25 of every document for one query (float64 (N,)); 0 for documents without a query term."""
        s = np.zeros(self.n)
        norm = K1 * (1 - B + B * self.dl / self.avgdl)
        for t in set(terms):
            tid = self.vocab.get(t)
            if tid is None:
                continue
            a, e = self.ptr[tid], self.ptr[tid + 1]
            docs, tf = self.doc[a:e], self.tf[a:e].astype(np.float64)
            df = e - a
            idf = math.log(1 + (self.n - df + 0.5) / (df + 0.5))
            s[docs] += idf * tf * (K1 + 1) / (tf + norm[docs])
        return s


def build_index(texts: Sequence[str], analyzer: Analyzer | None = None) -> Bm25Index:
    an = analyzer or Analyzer()
    vocab: dict[str, int] = {}
    rows: list[int] = []
    cols: list[int] = []
    cnts: list[int] = []
    dl = np.zeros(len(texts), dtype=np.float32)
    for i, text in enumerate(texts):
        toks = an(text)
        dl[i] = len(toks)
        counts: dict[int, int] = {}
        for t in toks:
            tid = vocab.setdefault(t, len(vocab))
            counts[tid] = counts.get(tid, 0) + 1
        for tid, c in counts.items():
            rows.append(i)
            cols.append(tid)
            cnts.append(c)
    r = np.asarray(rows, dtype=np.int32)
    c = np.asarray(cols, dtype=np.int64)
    order = np.lexsort((r, c))  # by term, then document
    ptr = np.zeros(len(vocab) + 1, dtype=np.int64)
    np.add.at(ptr, c + 1, 1)
    return Bm25Index(vocab, np.cumsum(ptr), r[order], np.asarray(cnts, dtype=np.float32)[order], dl,
                     float(dl.mean()) if len(dl) else 0.0)  # fmt: skip


def topk(scores: np.ndarray, mask: np.ndarray, k: int, positive: bool = True) -> np.ndarray:
    """int32 (k,) best rows inside `mask` (score > 0 if `positive`), ties → lower row, -1 padded."""
    ok = mask & (scores > 0) if positive else mask.copy()
    rows = np.flatnonzero(ok)
    out = np.full(k, -1, dtype=np.int32)
    if len(rows):
        order = np.lexsort((rows, -scores[rows]))[:k]
        out[: len(order)] = rows[order]
    return out


def rrf(legs: Sequence[np.ndarray], k: int, rrf_k: int = RRF_K) -> tuple[np.ndarray, dict[int, float]]:
    """Reciprocal rank fusion of ranked row lists (-1 = padding). Returns top-k rows and all fused scores."""
    score: dict[int, float] = {}
    for leg in legs:
        for rank, row in enumerate(leg.tolist(), start=1):
            if row >= 0:
                score[row] = score.get(row, 0.0) + 1.0 / (rrf_k + rank)
    best = sorted(score.items(), key=lambda x: (-x[1], x[0]))[:k]
    out = np.full(k, -1, dtype=np.int32)
    out[: len(best)] = [r for r, _ in best]
    return out, score


def is_compound(text: str) -> bool:
    """Compound-word subset (PLAN §9): the query is one word, e.g. „Druckluftleitungsleckage“."""
    return len(_SPLIT.sub(" ", text).split()) == 1
