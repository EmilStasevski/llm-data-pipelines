"""Track C contestant c05_duckdb_vss: DuckDB 2.0 + `vss` HNSW (metric ip, M=16, efC=200), embedded.

Ingest: one Arrow table (vectors as FLOAT[1024], zero-copy from numpy) → `CREATE TABLE AS`, on-disk
database. vss has no half-precision arrays, so vectors are stored as FLOAT. Filters: vss pre-filters
static table filters before the HNSW search (`hnsw_prefilter`, default on in vss 2.0).
"""

from __future__ import annotations

import shutil
import sys
from collections.abc import Callable, Sequence

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
    sql_where,
    throughput,
)
from zj_db.common import write_output as _write


def main(argv: list[str] | None = None) -> int:
    from zjmark import result, stage  # noqa: PLC0415

    a = parse_args(argv, "c05_duckdb_vss", ef_search=EF_SEARCH, query_threads=1)
    store = a.output.parent / "c05_store"
    shutil.rmtree(store, ignore_errors=True)
    store.mkdir(parents=True)
    with stage("load"):
        import duckdb  # noqa: PLC0415

        con = duckdb.connect(str(store / "zj.duckdb"))
        con.execute("LOAD vss; SET hnsw_enable_experimental_persistence = true")
        if a.threads > 0:
            con.execute(f"SET threads = {int(a.threads)}")
    with stage("ingest"):
        inp = read_inputs(a.input)
        flat = pa.array(np.asarray(inp.vectors, dtype=np.float32).ravel())
        docs = inp.docs.drop_columns(["doc_text", "id"]).append_column(
            "vec", pa.FixedSizeListArray.from_arrays(flat, DIM)
        )
        docs = docs.add_column(0, "i", pa.array(np.arange(inp.n, dtype=np.int32)))
        con.register("docs_arrow", docs)
        con.execute(
            f"CREATE TABLE docs AS SELECT i, ort, bezirk, datum, vec::FLOAT[{DIM}] AS vec FROM docs_arrow"
        )
        con.unregister("docs_arrow")
        del flat, docs
    with stage("index"):
        con.execute(f"CREATE INDEX docs_hnsw ON docs USING HNSW (vec) WITH (metric = 'ip', "
                    f"M = {HNSW['m']}, ef_construction = {HNSW['ef_construction']})")  # fmt: skip
        con.execute("CHECKPOINT")
    con.execute(f"SET hnsw_ef_search = {int(a.ef_search)}")
    # one small index probe per query: DuckDB's per-query thread fan-out costs more than it brings
    # (unmeasured probe: ≈ 14 ms vs 2 ms per unfiltered query at real); 0 = keep all threads
    if a.query_threads > 0:
        con.execute(f"SET threads = {int(a.query_threads)}")

    def make_search(c: duckdb.DuckDBPyConnection) -> Callable[[Query, np.ndarray], Sequence[int]]:
        c.execute(f"SET hnsw_ef_search = {int(a.ef_search)}")

        def search(q: Query, qv: np.ndarray) -> Sequence[int]:
            return _search(c, q, qv)

        return search

    def _search(c: duckdb.DuckDBPyConnection, q: Query, qv: np.ndarray) -> Sequence[int]:
        where, params = sql_where(q, "?")
        rows = c.execute(
            f"SELECT i FROM docs WHERE {where} "
            f"ORDER BY array_negative_inner_product(vec, ?::FLOAT[{DIM}]) LIMIT {K}",
            [*params, qv],
        ).fetchall()
        return [int(r[0]) for r in rows]

    with stage("query"):
        top, lat = run_queries(inp, make_search(con))
    with stage("query_c16"):  # one cursor (= connection to the same database) per client thread
        top16, wall16 = run_concurrent(inp, lambda: make_search(con.cursor()))
    with stage("write"):
        _write(a.output, top)
        _write(c16_sidecar(a.output), top16)
    disk = dir_bytes(store)
    con.close()
    shutil.rmtree(store, ignore_errors=True)
    result(rows=inp.n, notes="DuckDB vss HNSW (FLOAT[1024], ip), on-disk, filters pre-applied by vss", extra={
        "engine": "cpu", "duckdb": duckdb.__version__, "latency_ms": latency_ms(lat), "disk_bytes": disk,
        "index": {"type": "hnsw", **HNSW, "ef_search": a.ef_search, "metric": "ip"},
        "query_threads": a.query_threads, "throughput": throughput(lat, top, top16, wall16),
    })  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
