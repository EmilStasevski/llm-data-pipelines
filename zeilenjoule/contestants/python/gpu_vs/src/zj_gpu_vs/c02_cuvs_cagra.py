"""Track C contestant c02_cuvs_cagra: RAPIDS cuVS CAGRA graph index on the GPU, fp16 dataset, inner product.

Like c01 (a library, no query planner): metadata masks are computed on the GPU (cupy); small candidate sets
(≤ --brute-max rows) are scored exactly on the GPU, larger filtered sets go through CAGRA with a bitset
filter and a search width (itopk) raised in proportion to 1/selectivity (capped). Everything runs on the GPU;
there is no CPU path (rule 6: fails loudly without CUDA).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

K = 10
EPOCH = dt.date(1970, 1, 1)


def concurrent(
    n_queries: int, one: Callable[[int], np.ndarray], workers: int = 16
) -> tuple[np.ndarray, float]:
    """The same queries from `workers` client threads sharing the GPU (no batching). → (top, wall s)."""
    top = np.full((n_queries, K), -1, dtype=np.int32)

    def run(j: int) -> None:
        top[j] = one(j)

    with ThreadPoolExecutor(workers) as ex:
        t0 = time.perf_counter()
        list(ex.map(run, range(n_queries)))
        wall = time.perf_counter() - t0
    return top, wall


def throughput(lat: np.ndarray, top1: np.ndarray, top16: np.ndarray, wall16: float) -> dict[str, Any]:
    return {"qps_c1": round(len(lat) / (float(lat.sum()) / 1e3), 1),
            "qps_c16": round(len(top16) / wall16, 1), "concurrency": 16,
            "c16_identical_rows": round(float((top1 == top16).all(axis=1).mean()), 4)}  # fmt: skip


def _codes(col: pa.ChunkedArray) -> tuple[np.ndarray, dict[str, int]]:  # pyright: ignore[reportUnknownParameterType]
    enc = pc.dictionary_encode(col).combine_chunks()  # pyright: ignore[reportAttributeAccessIssue,reportUnknownMemberType]
    return np.array(enc.indices.fill_null(-1), dtype=np.int32), {
        str(v): i for i, v in enumerate(enc.dictionary.to_pylist())
    }


def main(argv: list[str] | None = None) -> int:  # noqa: PLR0915
    from zjmark import result, stage  # noqa: PLC0415

    p = argparse.ArgumentParser(prog="c02_cuvs_cagra")
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--threads", type=int, default=0)
    p.add_argument("--itopk", type=int, default=64)
    p.add_argument("--itopk-max", type=int, default=512)
    p.add_argument("--brute-max", type=int, default=20000)
    a = p.parse_args(argv)
    with stage("load"):
        import cupy as cp  # noqa: PLC0415
        import cuvs  # noqa: PLC0415
        from cuvs.common import Resources  # noqa: PLC0415
        from cuvs.neighbors import cagra, filters  # noqa: PLC0415

        if cp.cuda.runtime.getDeviceCount() < 1:
            raise RuntimeError("no CUDA device")
        cp.zeros(1).sum()  # context + memory pool belong to load
    with stage("ingest"):
        d = a.input.parent
        docs = pq.read_table(a.input, columns=["ort", "bezirk", "datum"])
        vec = cp.asarray(np.load(d / "vectors.f16.npy"))  # fp16 on the GPU
        ort, ort_ix = _codes(docs.column("ort"))
        bez, bez_ix = _codes(docs.column("bezirk"))
        dat = docs.column("datum").combine_chunks()
        has = cp.asarray(np.array(dat.is_valid(), dtype=bool))
        day = cp.asarray(np.array(dat.cast(pa.int32()).fill_null(0), dtype=np.int32))
        ort_g, bez_g = cp.asarray(ort), cp.asarray(bez)
        queries = [json.loads(x) for x in (d / "queries.jsonl").read_text("utf-8").splitlines() if x]
        qv32 = cp.asarray(np.load(d / "queries.f32.npy"))
        qv16 = qv32.astype(cp.float16)
        cp.cuda.Device().synchronize()
    n = vec.shape[0]
    with stage("index"):
        # cuVS defaults (graph_degree 64, intermediate 128): CAGRA has no M/efC (PLAN §9's HNSW baseline)
        params = cagra.IndexParams(metric="inner_product")
        index = cagra.build(params, vec)
        cp.cuda.Device().synchronize()
    stats = {"exact": 0, "cagra": 0, "cagra_filtered": 0}
    pad_bits = (-n) % 32

    def mask_for(f: dict[str, Any]) -> Any:
        m = cp.ones(n, dtype=cp.bool_)
        if "ort" in f:
            m &= ort_g == ort_ix.get(f["ort"], -2)
        if "bezirk" in f:
            m &= bez_g == bez_ix.get(f["bezirk"], -2)
        if "date_from" in f or "date_to" in f:
            m &= has
            if "date_from" in f:
                m &= day >= (dt.date.fromisoformat(f["date_from"]) - EPOCH).days
            if "date_to" in f:
                m &= day <= (dt.date.fromisoformat(f["date_to"]) - EPOCH).days
        return m

    local = threading.local()

    def search(j: int, f: dict[str, Any]) -> np.ndarray:
        # each client thread: its own cupy stream and its own cuVS handle (own stream); the cupy stream is
        # synchronised before every cuVS call, so cuVS never reads a bitset cupy has not finished writing
        if not hasattr(local, "stream"):
            local.stream = cp.cuda.Stream(non_blocking=True)
            local.res = Resources()
        with local.stream:
            return _search(j, f, local.res)

    def _search(j: int, f: dict[str, Any], res: Any) -> np.ndarray:
        out = np.full(K, -1, dtype=np.int32)
        if not f:
            stats["cagra"] += 1
            local.stream.synchronize()
            _, nb = cagra.search(cagra.SearchParams(itopk_size=a.itopk), index, qv16[j : j + 1], K,
                                 resources=res)  # fmt: skip
            res.sync()
            return cp.asnumpy(cp.asarray(nb)[0]).astype(np.int32)
        m = mask_for(f)
        cnt = int(m.sum())  # synchronises
        if cnt == 0:
            return out
        if cnt <= a.brute_max:
            stats["exact"] += 1
            rows = cp.flatnonzero(m)
            s = vec[rows].astype(cp.float32) @ qv32[j]
            kk = min(K, cnt)
            part = cp.argpartition(-s, kk - 1)[:kk]
            order = part[cp.argsort(-s[part])]
            out[:kk] = cp.asnumpy(rows[order])
            return out
        stats["cagra_filtered"] += 1
        bits = cp.packbits(cp.concatenate([m, cp.zeros(pad_bits, dtype=cp.bool_)]), bitorder="little")
        flt = filters.from_bitset(bits.view(cp.uint32))
        itopk = min(a.itopk_max, max(a.itopk, int(a.itopk * n / cnt)))
        local.stream.synchronize()
        _, nb = cagra.search(cagra.SearchParams(itopk_size=itopk), index, qv16[j : j + 1], K, filter=flt,
                             resources=res)  # fmt: skip
        res.sync()
        got = cp.asnumpy(cp.asarray(nb)[0]).astype(np.int64)
        got = got[(got >= 0) & (got < n)]
        out[: len(got)] = got
        return out

    top = np.full((len(queries), K), -1, dtype=np.int32)
    lat = np.empty(len(queries))
    with stage("query"):
        for j, q in enumerate(queries):
            t0 = time.perf_counter()
            top[j] = search(j, q["filter"])
            lat[j] = (time.perf_counter() - t0) * 1e3
    plan_counts = dict(stats)  # of the concurrency-1 pass
    with stage("query_c16"):
        top16, wall16 = concurrent(len(queries), lambda j: search(j, queries[j]["filter"]))
    with stage("write"):
        np.save(a.output, top, allow_pickle=False)
        np.save(a.output.with_name(f"{a.output.stem}.c16{a.output.suffix}"), top16, allow_pickle=False)
    free, total = cp.cuda.runtime.memGetInfo()
    result(rows=n, notes="cuVS CAGRA fp16 (inner product) on the GPU; selective filters → exact on the GPU",
           extra={
        "engine": "cuda", "cuvs": cuvs.__version__, "latency_ms":
        {f"p{q}": round(float(np.percentile(lat, q)), 3) for q in (50, 95, 99)},
        "plan_counts": plan_counts, "throughput": throughput(lat, top, top16, wall16),
        "gpu_used_mb": round((total - free) / 2**20),
        "index": {"type": "cagra", "graph_degree": params.graph_degree,
                  "intermediate_graph_degree": params.intermediate_graph_degree, "itopk": a.itopk,
                  "itopk_max": a.itopk_max, "brute_max": a.brute_max, "dtype": "fp16"},
    })  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
