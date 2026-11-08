"""Track C contestant c03_pgvector_hybrid (`c.hybrid`): PostgreSQL 18 + pgvector + built-in full-text search.

Same container, ingest and HNSW index as c03_pgvector, plus `tsv tsvector GENERATED … to_tsvector('german',
doc_text)` with a GIN index. One SQL statement per query (pgvector's documented hybrid pattern): a dense CTE
(HNSW, LIMIT 100) and a lexical CTE (`tsv @@ query`, ranked by `ts_rank`, LIMIT 100), fused by RRF.
**`ts_rank` is not BM25** (no IDF, no length saturation; PLAN §9). The lexical query is the OR of the
query's German lexemes (`plainto_tsquery` is AND-only: real-data titles would match nothing).
"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from typing import Any

import numpy as np

from zj_db.c03_pgvector import DSN, IMAGE, container, sql_text
from zj_db.common import EF_SEARCH, HNSW, Query, latency_ms, parse_args, read_inputs, sql_where, write_output
from zj_db.hybrid import DEPTH, collect_legs, read_texts, rrf_sql, run_hybrid, write_legs
from zj_db.server import image_digest, running

TSQ = "replace(plainto_tsquery('german', %s)::text, '&', '|')::tsquery"  # OR of the lexemes


def legs_sql(where: str) -> tuple[str, str]:
    dense = (f"SELECT i, row_number() OVER (ORDER BY dist) AS r FROM ("
             f"SELECT i, emb <#> %s AS dist FROM docs WHERE {where} "
             f"ORDER BY dist LIMIT {DEPTH}) x")  # fmt: skip
    lexical = (f"SELECT i, row_number() OVER (ORDER BY rank DESC, i) AS r FROM ("
               f"SELECT i, ts_rank(tsv, t.q) AS rank FROM docs, (SELECT {TSQ} AS q) t "
               f"WHERE tsv @@ t.q AND {where} "
               f"ORDER BY rank DESC, i LIMIT {DEPTH}) y")  # fmt: skip
    return dense, lexical


def main(argv: list[str] | None = None) -> int:  # noqa: PLR0915
    from zjmark import result, stage  # noqa: PLC0415

    a = parse_args(argv, "c03_pgvector_hybrid", ef_search=EF_SEARCH)
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
            texts = read_texts(a.input, inp)
            conn.execute(sql_text("CREATE TABLE docs (i int4 PRIMARY KEY, ort text, bezirk text, datum date, "
                              f"doc_text text NOT NULL, emb halfvec({inp.vectors.shape[1]}) NOT NULL, "
                              "tsv tsvector GENERATED ALWAYS AS "
                              "(to_tsvector('german', doc_text)) STORED)"))  # fmt: skip
            cols = [inp.docs.column(x).to_pylist() for x in ("ort", "bezirk", "datum", "doc_text")]
            vec = np.asarray(inp.vectors)
            with (
                conn.cursor() as cur,
                cur.copy("COPY docs (i, ort, bezirk, datum, doc_text, emb) FROM STDIN (FORMAT BINARY)") as cp,
            ):
                cp.set_types(["int4", "text", "text", "date", "text", "halfvec"])
                for i in range(inp.n):
                    cp.write_row(
                        (i, cols[0][i], cols[1][i], cols[2][i], cols[3][i] or "", HalfVector(vec[i]))
                    )
        with stage("index"):
            conn.execute(sql_text(f"CREATE INDEX docs_hnsw ON docs USING hnsw (emb halfvec_ip_ops) "
                              f"WITH (m = {HNSW['m']}, "
                              f"ef_construction = {HNSW['ef_construction']})"))  # fmt: skip
            conn.execute("CREATE INDEX docs_tsv ON docs USING gin (tsv)")
            for col in ("ort", "bezirk", "datum"):
                conn.execute(sql_text(f"CREATE INDEX docs_{col} ON docs ({col})"))
            conn.execute("VACUUM ANALYZE docs")
        conn.execute(sql_text(f"SET hnsw.ef_search = {max(int(a.ef_search), DEPTH)}"))
        conn.execute("SET hnsw.iterative_scan = strict_order")

        def search(q: Query, qv: np.ndarray, text: str) -> Sequence[int]:
            where, params = sql_where(q, "%s")
            dense, lexical = legs_sql(where)
            sql = f"WITH d AS ({dense}), l AS ({lexical}) {rrf_sql()}"
            hv = HalfVector(qv)
            args = [hv, *params, text, *params]
            rows: list[Any] = conn.execute(sql_text(sql), args, prepare=True).fetchall()
            return [int(r[0]) for r in rows]

        def legs(q: Query, qv: np.ndarray, text: str) -> tuple[list[int], list[int]]:
            where, params = sql_where(q, "%s")
            dense, lexical = legs_sql(where)
            d = conn.execute(sql_text(f"{dense} ORDER BY r"), [HalfVector(qv), *params]).fetchall()
            lx = conn.execute(sql_text(f"{lexical} ORDER BY r"), [text, *params]).fetchall()
            return [int(r[0]) for r in d], [int(r[0]) for r in lx]

        with stage("query"):
            top, lat = run_hybrid(inp, texts, search)
        with stage("write"):
            write_output(a.output, top)
        with stage("legs"):
            leg_info = write_legs(a.output, *collect_legs(inp, texts, legs))
        size = conn.execute("SELECT pg_database_size(current_database())").fetchone()
        version = conn.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'").fetchone()
        server_version = conn.execute("SHOW server_version").fetchone()
        conn.close()
    finally:
        srv.__exit__(None, None, None)
    result(rows=inp.n, notes="pgvector HNSW + tsvector('german')/GIN ranked by ts_rank (not BM25), "
           "RRF in one SQL statement", extra={
        "engine": "cpu (server)", "image": image_digest(IMAGE),
        "postgres": server_version[0] if server_version else None,
        "pgvector": version[0] if version else None, "latency_ms": latency_ms(lat),
        "disk_bytes": int(size[0]) if size else None, "legs": leg_info,
        "index": {"type": "hnsw + gin(tsvector german)", **HNSW, "ef_search": max(a.ef_search, DEPTH),
                  "lexical": "ts_rank over OR of plainto_tsquery lexemes"},
    })  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
