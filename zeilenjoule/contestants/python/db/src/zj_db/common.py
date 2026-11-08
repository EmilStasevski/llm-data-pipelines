"""CLI contract, inputs, query loop and output of the Track C `c.dense` contestants (spec/track_c.md)."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

DIM = 1024
K = 10
REPO = Path(__file__).resolve().parents[5]
HNSW = {"m": 16, "ef_construction": 200}  # PLAN §9 fair-play baseline
# same for every HNSW engine; 64 left real/none at recall 0.958-0.963, too close to the 0.95 gate (ADR 0016)
EF_SEARCH = 100


@dataclass(frozen=True)
class Query:
    qid: str
    filter_class: str
    ort: str | None
    bezirk: str | None
    date_from: dt.date | None
    date_to: dt.date | None

    @property
    def has_date(self) -> bool:
        return self.date_from is not None or self.date_to is not None

    @property
    def unfiltered(self) -> bool:
        return self.ort is None and self.bezirk is None and not self.has_date


@dataclass(frozen=True)
class Inputs:
    docs: pa.Table  # id, ort, bezirk, datum, doc_text
    vectors: np.ndarray  # float16 (N, 1024), memory-mapped
    queries: list[Query]
    qvec: np.ndarray  # float32 (Q, 1024)

    @property
    def n(self) -> int:
        return self.docs.num_rows


def parse_args(argv: Sequence[str] | None, prog: str, **knobs: int) -> argparse.Namespace:
    """--input/--output/--threads plus integer engine knobs (e.g. ef_search=100 → --ef-search)."""
    p = argparse.ArgumentParser(prog=prog)
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--threads", type=int, default=0)
    for name, default in knobs.items():
        p.add_argument("--" + name.replace("_", "-"), type=int, default=default)
    return p.parse_args(argv)


def _query(d: Mapping[str, Any]) -> Query:
    f: Mapping[str, str] = d["filter"]

    def day(k: str) -> dt.date | None:
        return dt.date.fromisoformat(f[k]) if k in f else None

    return Query(str(d["qid"]), str(d["filter_class"]), f.get("ort"), f.get("bezirk"), day("date_from"),
                 day("date_to"))  # fmt: skip


def read_inputs(docs_path: Path) -> Inputs:
    d = docs_path.parent
    docs = pq.read_table(docs_path)
    vectors = np.load(d / "vectors.f16.npy", mmap_mode="r")
    queries = [_query(json.loads(x)) for x in (d / "queries.jsonl").read_text("utf-8").splitlines() if x]
    qvec = np.load(d / "queries.f32.npy")
    if vectors.shape != (docs.num_rows, DIM) or qvec.shape != (len(queries), DIM):
        raise ValueError(f"inconsistent inputs in {d}: {vectors.shape}, {docs.num_rows} docs, {qvec.shape}")
    return Inputs(docs, vectors, queries, qvec)


def sql_where(q: Query, style: str = "%s", date_col: str = "datum") -> tuple[str, list[Any]]:
    """Filter as SQL predicate (`style` = driver placeholder) + parameters; a NULL datum never matches."""
    parts: list[str] = []
    params: list[Any] = []
    if q.ort is not None:
        parts.append(f"ort = {style}")
        params.append(q.ort)
    if q.bezirk is not None:
        parts.append(f"bezirk = {style}")
        params.append(q.bezirk)
    if q.date_from is not None:
        parts.append(f"{date_col} >= {style}")
        params.append(q.date_from)
    if q.date_to is not None:
        parts.append(f"{date_col} <= {style}")
        params.append(q.date_to)
    return (" AND ".join(parts) or "TRUE"), params


def run_queries(
    inp: Inputs, search: Callable[[Query, np.ndarray], Sequence[int]]
) -> tuple[np.ndarray, np.ndarray]:
    """Run every query once, in order (concurrency 1). Returns (int32 (Q, 10) -1 padded, latency ms)."""
    top = np.full((len(inp.queries), K), -1, dtype=np.int32)
    lat = np.empty(len(inp.queries))
    for j, q in enumerate(inp.queries):
        t0 = time.perf_counter()
        ids = search(q, inp.qvec[j])
        lat[j] = (time.perf_counter() - t0) * 1e3
        ids = list(ids)[:K]
        top[j, : len(ids)] = ids
    return top, lat


CONCURRENCY = 16  # PLAN §6: QPS at concurrency 1 and 16


def run_concurrent(
    inp: Inputs,
    make_search: Callable[[], Callable[[Query, np.ndarray], Sequence[int]]],
    workers: int = CONCURRENCY,
) -> tuple[np.ndarray, float]:
    """The same 1 000 queries from `workers` client threads (each with its own `make_search()` handle,
    e.g. its own connection). Returns (int32 (Q, 10) -1 padded, wall seconds)."""
    import threading  # noqa: PLC0415
    from concurrent.futures import ThreadPoolExecutor  # noqa: PLC0415

    local = threading.local()
    top = np.full((len(inp.queries), K), -1, dtype=np.int32)

    barrier = threading.Barrier(workers)

    def init(_: int) -> None:  # one per worker thread (the barrier pins one task to each)
        local.fn = make_search()
        barrier.wait()

    def one(j: int) -> None:
        ids = list(local.fn(inp.queries[j], inp.qvec[j]))[:K]
        top[j, : len(ids)] = ids

    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(init, range(workers)))  # handles (connections) are set up outside the timed part
        t0 = time.perf_counter()
        list(ex.map(one, range(len(inp.queries))))
        wall = time.perf_counter() - t0
    return top, wall


def c16_sidecar(output: Path) -> Path:
    return output.with_name(f"{output.stem}.c16{output.suffix}")


def throughput(lat: np.ndarray, top1: np.ndarray, top16: np.ndarray, wall16: float) -> dict[str, Any]:
    """`extra.throughput`: QPS at concurrency 1 (sum of latencies) and 16 (wall), agreement of the two."""
    return {"qps_c1": round(len(lat) / (float(lat.sum()) / 1e3), 1),
            "qps_c16": round(len(top16) / wall16, 1), "concurrency": CONCURRENCY,
            "c16_identical_rows": round(float((top1 == top16).all(axis=1).mean()), 4)}  # fmt: skip


def latency_ms(lat: np.ndarray) -> dict[str, float]:
    return {f"p{p}": round(float(np.percentile(lat, p)), 3) for p in (50, 95, 99)}


def write_output(path: Path, top: np.ndarray) -> None:
    np.save(path, top.astype(np.int32, copy=False), allow_pickle=False)


def dir_bytes(path: Path) -> int:
    return (
        sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
        if path.is_dir()
        else path.stat().st_size
    )


def batches(n: int, size: int) -> list[tuple[int, int]]:
    return [(s, min(s + size, n)) for s in range(0, n, size)]
