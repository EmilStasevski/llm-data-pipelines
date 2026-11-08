"""Track C contestant c01_faiss_cpu: FAISS HNSW (fp16 scalar-quantised storage, M=16, efC=200), CPU.

Filtered queries the way a FAISS expert does it (FAISS has no query planner): a numpy mask over the
metadata; small candidate sets (≤ --brute-max rows) are scored exactly, larger ones go through HNSW with an
IDSelectorBitmap and an ef_search raised in proportion to 1/selectivity (capped).
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from typing import Any

import numpy as np

from zj_db.common import (
    EF_SEARCH,
    HNSW,
    K,
    Query,
    c16_sidecar,
    latency_ms,
    parse_args,
    read_inputs,
    run_concurrent,
    run_queries,
    throughput,
    write_output,
)
from zj_db.meta import MetaArrays, exact_subset


def main(argv: list[str] | None = None) -> int:
    from zjmark import result, stage  # noqa: PLC0415

    a = parse_args(argv, "c01_faiss_cpu", ef_search=EF_SEARCH, brute_max=8000, ef_max=1024)
    with stage("load"):
        import faiss as _faiss  # noqa: PLC0415

        faiss: Any = _faiss  # the bundled stubs lack SearchParametersHNSW & co.

        if a.threads > 0:
            faiss.omp_set_num_threads(a.threads)
    with stage("ingest"):
        inp = read_inputs(a.input)
        vectors = np.ascontiguousarray(inp.vectors)  # fp16 in RAM (exact path)
        meta = MetaArrays.from_table(inp.docs)
    with stage("index"):
        index = faiss.IndexHNSWSQ(vectors.shape[1], faiss.ScalarQuantizer.QT_fp16, HNSW["m"],
                                  faiss.METRIC_INNER_PRODUCT)  # fmt: skip
        index.hnsw.efConstruction = HNSW["ef_construction"]
        xb = np.asarray(vectors, dtype=np.float32)
        index.train(xb)
        index.add(xb)
        del xb
    n = inp.n
    stats = {"exact": 0, "hnsw": 0, "hnsw_filtered": 0}

    def search(q: Query, qv: np.ndarray) -> Sequence[int]:
        m = meta.mask(q)
        x = qv.reshape(1, -1)
        if m is None:
            stats["hnsw"] += 1
            _, ids = index.search(x, K, params=faiss.SearchParametersHNSW(efSearch=a.ef_search))
            return [int(i) for i in ids[0] if i >= 0]
        rows = np.flatnonzero(m)
        if len(rows) <= a.brute_max:
            stats["exact"] += 1
            return exact_subset(vectors, rows, qv, K)
        stats["hnsw_filtered"] += 1
        ef = min(a.ef_max, max(a.ef_search, int(a.ef_search * n / len(rows))))
        sel = faiss.IDSelectorBitmap(np.packbits(m, bitorder="little"))
        _, ids = index.search(x, K, params=faiss.SearchParametersHNSW(efSearch=ef, sel=sel))
        return [int(i) for i in ids[0] if i >= 0]

    with stage("query"):
        top, lat = run_queries(inp, search)
    plan_counts = dict(stats)  # of the concurrency-1 pass
    with stage("query_c16"):
        top16, wall16 = run_concurrent(inp, lambda: search)  # FAISS search is thread-safe, releases the GIL
    with stage("write"):
        write_output(a.output, top)
        write_output(c16_sidecar(a.output), top16)
    result(rows=n, notes="FAISS IndexHNSWSQ fp16 IP; selective filters → exact on the subset", extra={
        "engine": "cpu", "faiss": faiss.__version__, "latency_ms": latency_ms(lat),
        "plan_counts": plan_counts,
        "throughput": throughput(lat, top, top16, wall16),
        "index": {"type": "hnsw_sq_fp16", **HNSW, "ef_search": a.ef_search, "brute_max": a.brute_max,
                  "ef_max": a.ef_max},
    })  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
