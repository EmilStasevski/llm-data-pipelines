"""Track C contestant c07_lancedb: LanceDB (Rust core, embedded, on-disk), IVF_HNSW_SQ index (M=16, efC=200).

- ingest: one Arrow table (vectors as fp16 FixedSizeList) → `create_table`.
- index: IVF_HNSW_SQ (dot) + scalar indexes (BITMAP ort/bezirk, BTREE datum).
- query: `search(...).where(filter, prefilter=True)`: the filter runs first, so filtered queries never come
  back short.
"""

from __future__ import annotations

import shutil
import sys
from collections.abc import Sequence
from typing import Any

import numpy as np
import pyarrow as pa

from zj_db.common import (
    DIM,
    EF_SEARCH,
    HNSW,
    K,
    Query,
    c16_sidecar,
    dir_bytes,
    latency_ms,
    parse_args,
    read_inputs,
    run_concurrent,
    run_queries,
    throughput,
    write_output,
)  # fmt: skip


def where(q: Query) -> str | None:
    """Lance SQL filter. Values come from spec files (no quotes inside); still escaped."""

    def esc(s: str) -> str:
        return s.replace("'", "''")

    parts: list[str] = []
    if q.ort is not None:
        parts.append(f"ort = '{esc(q.ort)}'")
    if q.bezirk is not None:
        parts.append(f"bezirk = '{esc(q.bezirk)}'")
    if q.date_from is not None:
        parts.append(f"datum >= date '{q.date_from.isoformat()}'")
    if q.date_to is not None:
        parts.append(f"datum <= date '{q.date_to.isoformat()}'")
    return " AND ".join(parts) or None


def main(argv: list[str] | None = None) -> int:
    from zjmark import result, stage  # noqa: PLC0415

    a = parse_args(argv, "c07_lancedb", ef_search=EF_SEARCH, nprobes=0, nprobes_filtered=0, partitions=0)
    store = a.output.parent / "c07_store"
    shutil.rmtree(store, ignore_errors=True)
    with stage("load"):
        import lancedb  # noqa: PLC0415

        db = lancedb.connect(str(store))
    with stage("ingest"):
        inp = read_inputs(a.input)
        vec = pa.FixedSizeListArray.from_arrays(pa.array(np.asarray(inp.vectors).ravel(), pa.float16()), DIM)
        t = inp.docs.drop_columns(["doc_text", "id"]).append_column("vector", vec)
        t = t.add_column(0, "i", pa.array(np.arange(inp.n, dtype=np.int32)))
        tbl: Any = db.create_table("docs", t)  # lancedb's stubs are partial (query builder methods)
        del vec, t
    # √N/4 partitions unless overridden. bge-m3 vectors spread over many IVF cells: in unmeasured probes at
    # real, nprobes = ¼ of the partitions gave recall@10 0.72–0.87 and ½ gave 0.944 (unfiltered), below the
    # 0.95 gate → every partition is probed (filtered subsets are scattered across all cells anyway)
    parts = a.partitions or max(1, round(inp.n**0.5 / 4))
    nprobes = a.nprobes or parts
    nprobes_f = a.nprobes_filtered or parts
    with stage("index"):
        tbl.create_index(metric="dot", vector_column_name="vector", index_type="IVF_HNSW_SQ",
                         num_partitions=parts, m=HNSW["m"],
                         ef_construction=HNSW["ef_construction"])  # fmt: skip
        tbl.create_scalar_index("ort", index_type="BITMAP")
        tbl.create_scalar_index("bezirk", index_type="BITMAP")
        tbl.create_scalar_index("datum", index_type="BTREE")

    def search(q: Query, qv: np.ndarray) -> Sequence[int]:
        w = where(q)
        s = tbl.search(qv, vector_column_name="vector").distance_type("dot").limit(K)
        s = (
            s.nprobes(nprobes_f if w else nprobes).ef(a.ef_search).select(["i", "_distance"])
        )  # explicit: no warning
        if w:
            s = s.where(w, prefilter=True)
        return [int(x) for x in s.to_arrow().column("i").to_pylist()]

    with stage("query"):
        top, lat = run_queries(inp, search)
    with stage("query_c16"):
        top16, wall16 = run_concurrent(inp, lambda: search)  # one table handle, thread-safe (Rust core)
    with stage("write"):
        write_output(a.output, top)
        write_output(c16_sidecar(a.output), top16)
    disk = dir_bytes(store)
    shutil.rmtree(store, ignore_errors=True)
    result(rows=inp.n, notes="LanceDB IVF_HNSW_SQ (dot) + bitmap/btree scalar indexes, prefilter", extra={
        "engine": "cpu", "lancedb": lancedb.__version__, "latency_ms": latency_ms(lat), "disk_bytes": disk,
        "throughput": throughput(lat, top, top16, wall16),
        "index": {"type": "ivf_hnsw_sq", **HNSW, "ef_search": a.ef_search, "partitions": parts,
                  "nprobes": nprobes, "nprobes_filtered": nprobes_f},
    })  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
