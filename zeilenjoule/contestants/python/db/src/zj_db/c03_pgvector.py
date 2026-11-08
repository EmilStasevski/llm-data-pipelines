"""Track C contestant c03_pgvector: PostgreSQL 18 + pgvector 0.8.7, `halfvec` HNSW (M=16, efC=200).

- load: own container (pinned digest), memory settings for bulk load + parallel index build.
- ingest: `COPY … FROM STDIN (FORMAT BINARY)` with halfvec values (pgvector's psycopg binary adapter).
- index: HNSW `halfvec_ip_ops` built in parallel, B-tree indexes on the filter columns, ANALYZE.
- query: `ORDER BY emb <#> $1 LIMIT 10` with `hnsw.iterative_scan = strict_order`; the planner picks the
  HNSW scan or, for selective filters, a B-tree scan + exact sort (PostgreSQL's own cost model).
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from typing import Any, LiteralString, cast

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
    sql_where,
    throughput,
    write_output,
)
from zj_db.server import Container, image_digest, running

PORT = 15432
IMAGE = ("pgvector/pgvector:0.8.7-pg18-trixie"
         "@sha256:5895949dfcdf308eb0cd31c950c93c14895c77f33fbedfecf1fa511ab1d4bcd2")  # fmt: skip
DSN = f"host=127.0.0.1 port={PORT} user=postgres dbname=postgres"


def container(threads: int) -> Container:
    workers = max(1, threads - 1)
    return Container(
        name="zj-c03-pgvector", image=IMAGE,
        args=["-c", f"port={PORT}", "-c", "listen_addresses=127.0.0.1", "-c", "shared_buffers=4GB",
              "-c", "maintenance_work_mem=8GB", "-c", f"max_parallel_maintenance_workers={workers}",
              "-c", f"max_worker_processes={workers + 8}", "-c", "effective_cache_size=16GB",
              "-c", "work_mem=256MB", "-c", "max_wal_size=8GB", "-c", "checkpoint_timeout=30min"],
        env={"POSTGRES_HOST_AUTH_METHOD": "trust"},  # bound to 127.0.0.1, throw-away instance
        extra_docker=["--shm-size=10g"],  # parallel HNSW build shares the graph via dynamic shared memory
    )  # fmt: skip


def sql_text(s: str) -> LiteralString:
    """Our own SQL text built from constants and placeholders only (values go as parameters)."""
    return cast("LiteralString", s)


def _nodes(plan: dict[str, Any]) -> list[str]:
    """Node types of an EXPLAIN (FORMAT JSON) plan, depth first (+ index name where there is one)."""
    name = plan["Node Type"] + (f"({plan['Index Name']})" if "Index Name" in plan else "")
    return [name] + [n for child in plan.get("Plans", []) for n in _nodes(child)]


def main(argv: list[str] | None = None) -> int:  # noqa: PLR0915
    import os  # noqa: PLC0415

    from zjmark import result, stage  # noqa: PLC0415

    a = parse_args(argv, "c03_pgvector", ef_search=EF_SEARCH)
    threads = a.threads or os.cpu_count() or 1
    c = container(threads)
    with stage("load"):
        import psycopg  # noqa: PLC0415
        from pgvector import HalfVector  # noqa: PLC0415
        from pgvector.psycopg import register_vector  # noqa: PLC0415

        def ready() -> bool:
            with psycopg.connect(DSN, connect_timeout=2) as probe:
                return probe.execute("SELECT 1").fetchone() == (1,)

        srv = running(c, ready)
        srv.__enter__()
    try:
        conn = psycopg.connect(DSN, autocommit=True)
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        register_vector(conn)
        with stage("ingest"):
            inp = read_inputs(a.input)
            conn.execute(sql_text("CREATE TABLE docs (i int4 PRIMARY KEY, ort text, bezirk text, datum date, "
                         f"emb halfvec({inp.vectors.shape[1]}) NOT NULL)"))  # fmt: skip
            cols = [inp.docs.column(x).to_pylist() for x in ("ort", "bezirk", "datum")]
            vec = np.asarray(inp.vectors)
            with (
                conn.cursor() as cur,
                cur.copy("COPY docs (i, ort, bezirk, datum, emb) FROM STDIN (FORMAT BINARY)") as cp,
            ):
                cp.set_types(["int4", "text", "text", "date", "halfvec"])
                for i in range(inp.n):
                    cp.write_row((i, cols[0][i], cols[1][i], cols[2][i], HalfVector(vec[i])))
        with stage("index"):
            conn.execute(sql_text(f"CREATE INDEX docs_hnsw ON docs USING hnsw (emb halfvec_ip_ops) "
                         f"WITH (m = {HNSW['m']}, ef_construction = {HNSW['ef_construction']})"))  # fmt: skip
            for col in ("ort", "bezirk", "datum"):
                conn.execute(sql_text(f"CREATE INDEX docs_{col} ON docs ({col})"))
            conn.execute("VACUUM ANALYZE docs")

        def session(cn: Any) -> Callable[[Query, np.ndarray], Sequence[int]]:
            cn.execute(sql_text(f"SET hnsw.ef_search = {int(a.ef_search)}"))
            cn.execute("SET hnsw.iterative_scan = strict_order")

            def search(q: Query, qv: np.ndarray) -> Sequence[int]:
                where, params = sql_where(q, "%s")
                sql = f"SELECT i FROM docs WHERE {where} ORDER BY emb <#> %s LIMIT {K}"
                rows: list[Any] = cn.execute(
                    sql_text(sql), [*params, HalfVector(qv)], prepare=True
                ).fetchall()
                return [int(r[0]) for r in rows]

            return search

        plans: dict[str, str] = {}
        with stage("query"):
            top, lat = run_queries(inp, session(conn))
        clients: list[Any] = []

        def per_thread() -> Callable[[Query, np.ndarray], Sequence[int]]:
            cn = psycopg.connect(DSN, autocommit=True)  # one connection (= backend process) per client
            register_vector(cn)
            clients.append(cn)
            return session(cn)

        with stage("query_c16"):
            top16, wall16 = run_concurrent(inp, per_thread)
        for cn in clients:
            cn.close()
        with stage("write"):
            write_output(a.output, top)
            write_output(c16_sidecar(a.output), top16)
        # plan mix per filter class (after the measured part; one EXPLAIN per class)
        seen: set[str] = set()
        for j, q in enumerate(inp.queries):
            if q.filter_class in seen:
                continue
            seen.add(q.filter_class)
            where, params = sql_where(q, "%s")
            plan = conn.execute(
                sql_text(
                    f"EXPLAIN (FORMAT JSON) SELECT i FROM docs WHERE {where} ORDER BY emb <#> %s LIMIT {K}"
                ),
                [*params, HalfVector(inp.qvec[j])],
            ).fetchone()
            plans[q.filter_class] = " > ".join(_nodes(plan[0][0]["Plan"])) if plan else "?"
        size = conn.execute("SELECT pg_database_size(current_database())").fetchone()
        version = conn.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'").fetchone()
        server_version = conn.execute("SHOW server_version").fetchone()
        conn.close()
    finally:
        srv.__exit__(None, None, None)
    result(rows=inp.n, notes="pgvector halfvec HNSW (ip), COPY BINARY, B-tree filter indexes, iterative scan",
           extra={
        "engine": "cpu (server)", "image": image_digest(IMAGE),
        "postgres": server_version[0] if server_version else None,
        "pgvector": version[0] if version else None, "latency_ms": latency_ms(lat),
        "throughput": throughput(lat, top, top16, wall16),
        "disk_bytes": int(size[0]) if size else None, "plans_sample": plans,
        "index": {"type": "hnsw", **HNSW, "ef_search": a.ef_search, "opclass": "halfvec_ip_ops",
                  "iterative_scan": "strict_order"},
    })  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
