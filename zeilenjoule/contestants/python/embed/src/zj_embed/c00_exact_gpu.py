"""Track C contestant c00_exact_gpu: exact filtered brute force, torch fp32 on the GPU (spec/track_c.md).

The vectors live on the GPU as fp32 (PLAN §9: fp32 ground-truth path), metadata as integer tensors; each query
is one masked matrix-vector product + top-k. No index (the `index` stage is empty). Fails loudly without CUDA.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
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
    idx = np.asarray(enc.indices.fill_null(-1), dtype=np.int32)
    return idx, {str(v): i for i, v in enumerate(enc.dictionary.to_pylist())}


def main(argv: list[str] | None = None) -> int:  # noqa: PLR0915
    from zjmark import result, stage  # noqa: PLC0415

    p = argparse.ArgumentParser(prog="c00_exact_gpu")
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--threads", type=int, default=0)
    a = p.parse_args(argv)
    with stage("load"):
        import torch  # noqa: PLC0415

        if not torch.cuda.is_available():
            raise RuntimeError("CUDA not available")  # rule 6: no silent CPU run
        dev = torch.device("cuda:0")
        torch.zeros(1, device=dev)  # context creation belongs to load
    with stage("ingest"):
        d = a.input.parent
        docs = pq.read_table(a.input, columns=["ort", "bezirk", "datum"])
        vec = torch.from_numpy(np.load(d / "vectors.f16.npy")).to(dev).float()
        ort, ort_ix = _codes(docs.column("ort"))
        bez, bez_ix = _codes(docs.column("bezirk"))
        dat = docs.column("datum").combine_chunks()
        has = torch.from_numpy(np.array(dat.is_valid(), dtype=bool)).to(dev)
        day = torch.from_numpy(np.array(dat.cast(pa.int32()).fill_null(0), dtype=np.int32)).to(dev)
        ort_t, bez_t = torch.from_numpy(ort).to(dev), torch.from_numpy(bez).to(dev)
        queries = [json.loads(x) for x in (d / "queries.jsonl").read_text("utf-8").splitlines() if x]
        qv = torch.from_numpy(np.load(d / "queries.f32.npy")).to(dev)
        torch.cuda.synchronize()
    with stage("index"):
        pass  # brute force: nothing to build
    n = vec.shape[0]
    top = np.full((len(queries), K), -1, dtype=np.int32)
    lat = np.empty(len(queries))
    neg_inf = torch.tensor(float("-inf"), device=dev)

    def one(j: int) -> np.ndarray:
        f = queries[j]["filter"]
        m = torch.ones(n, dtype=torch.bool, device=dev)
        if "ort" in f:
            m &= ort_t == ort_ix.get(f["ort"], -2)
        if "bezirk" in f:
            m &= bez_t == bez_ix.get(f["bezirk"], -2)
        if "date_from" in f or "date_to" in f:
            m &= has
            if "date_from" in f:
                m &= day >= (dt.date.fromisoformat(f["date_from"]) - EPOCH).days
            if "date_to" in f:
                m &= day <= (dt.date.fromisoformat(f["date_to"]) - EPOCH).days
        s = torch.where(m, vec @ qv[j], neg_inf)
        vals, idx = torch.topk(s, K)
        return torch.where(torch.isfinite(vals), idx, -1).cpu().numpy()  # .cpu() synchronises

    with stage("query"):
        for j in range(len(queries)):
            t0 = time.perf_counter()
            top[j] = one(j)
            lat[j] = (time.perf_counter() - t0) * 1e3
    with stage("query_c16"):
        top16, wall16 = concurrent(len(queries), one)
    with stage("write"):
        np.save(a.output, top, allow_pickle=False)
        np.save(a.output.with_name(f"{a.output.stem}.c16{a.output.suffix}"), top16, allow_pickle=False)
    result(rows=n, notes="torch fp32 exact filtered brute force on the GPU (no index)", extra={
        "engine": "cuda", "torch": torch.__version__, "device": torch.cuda.get_device_name(0),
        "vram_mb": round(torch.cuda.max_memory_allocated() / 2**20),
        "latency_ms": {f"p{p}": round(float(np.percentile(lat, p)), 3) for p in (50, 95, 99)},
        "index": {"type": "flat (exact)", "dtype": "fp32", "metric": "ip"},
        "throughput": throughput(lat, top, top16, wall16),
    })  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
