"""Shared parts of the Track C `c.hybrid` contestants (spec/track_c.md §c.hybrid).

Every contestant fuses its own dense top-`DEPTH` (vector index, filter applied) and its own lexical
top-`DEPTH` (the engine's full-text ranking, filter applied) with RRF (k = 60, ranks from 1) and returns the
top-10, the way the engine's documentation does it (one SQL statement, or the native fusion API).

After the measured `query` + `write` stages, stage `legs` asks the engine for the two candidate lists once
more and writes them next to the output (`<out>.dense.npy`, `<out>.lex.npy`): the verifier recomputes the
fusion from them. That stage is outside the comparison (report: stage `query`).
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Sequence
from pathlib import Path

import numpy as np

from zj_db.common import Inputs, K, Query, write_output

DEPTH = 100
RRF_K = 60
_WORD = re.compile(r"[^\W_]+")  # Unicode letters + digits


def read_texts(docs_path: Path, inp: Inputs) -> list[str]:
    rows = [
        json.loads(x) for x in (docs_path.parent / "queries.text.jsonl").read_text("utf-8").splitlines() if x
    ]
    if [r["qid"] for r in rows] != [q.qid for q in inp.queries]:
        raise ValueError("queries.text.jsonl and queries.jsonl disagree on query order")
    return [str(r["text"]) for r in rows]


def words(text: str) -> list[str]:
    """Distinct words of a query in order (for engines whose query syntax needs explicit OR terms)."""
    return list(dict.fromkeys(w.lower() for w in _WORD.findall(text)))


def rrf_sql(dense: str = "d", lexical: str = "l") -> str:
    """Fusion of two CTEs (i, r) by reciprocal rank (ties → lower row): the generic SQL form."""
    return (f"SELECT i FROM (SELECT i, r FROM {dense} UNION ALL SELECT i, r FROM {lexical}) u "
            f"GROUP BY i ORDER BY sum(1.0 / ({RRF_K} + r)) DESC, i LIMIT {K}")  # fmt: skip


def run_hybrid(
    inp: Inputs, texts: Sequence[str], search: Callable[[Query, np.ndarray, str], Sequence[int]]
) -> tuple[np.ndarray, np.ndarray]:
    """Every query once, in order (concurrency 1) → (int32 (Q, 10) -1 padded, latency ms)."""
    top = np.full((len(inp.queries), K), -1, dtype=np.int32)
    lat = np.empty(len(inp.queries))
    for j, q in enumerate(inp.queries):
        t0 = time.perf_counter()
        ids = list(search(q, inp.qvec[j], texts[j]))[:K]
        lat[j] = (time.perf_counter() - t0) * 1e3
        top[j, : len(ids)] = ids
    return top, lat


def collect_legs(
    inp: Inputs, texts: Sequence[str],
    legs: Callable[[Query, np.ndarray, str], tuple[Sequence[int], Sequence[int]]],
) -> tuple[np.ndarray, np.ndarray]:  # fmt: skip
    dense = np.full((len(inp.queries), DEPTH), -1, dtype=np.int32)
    lexical = np.full((len(inp.queries), DEPTH), -1, dtype=np.int32)
    for j, q in enumerate(inp.queries):
        d, lx = legs(q, inp.qvec[j], texts[j])
        d, lx = list(d)[:DEPTH], list(lx)[:DEPTH]
        dense[j, : len(d)] = d
        lexical[j, : len(lx)] = lx
    return dense, lexical


def write_legs(output: Path, dense: np.ndarray, lexical: np.ndarray) -> dict[str, int]:
    write_output(output.with_name(f"{output.stem}.dense{output.suffix}"), dense)
    write_output(output.with_name(f"{output.stem}.lex{output.suffix}"), lexical)
    return {"lex_empty_queries": int((lexical[:, 0] < 0).sum()), "depth": DEPTH, "rrf_k": RRF_K}
