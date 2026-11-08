"""Track C contestant c05_duckdb_hybrid (`c.hybrid`): DuckDB 2.0 `vss` HNSW + `fts` BM25, embedded.

Same store and HNSW index as c05_duckdb_vss, plus `PRAGMA create_fts_index` over `doc_text` (Snowball
`german` stemmer, no stop words, umlauts and ß kept: the default `ignore` pattern `[^a-z]` would cut German
words apart, the default `strip_accents` would fold ä → a). Lexical ranking = `match_bm25` (BM25, k1 1.2,
b 0.75, OR of the query terms). One SQL statement per query: dense CTE (HNSW, LIMIT 100) + lexical CTE
(BM25 inside the filter, LIMIT 100), fused by RRF.
"""

from __future__ import annotations

import shutil
import sys
from collections.abc import Sequence

import numpy as np
import pyarrow as pa

from zj_db.common import (
    DIM,
    EF_SEARCH,
    HNSW,
    Query,
    dir_bytes,
    latency_ms,
    parse_args,
    read_inputs,
    sql_where,
    write_output,
)
from zj_db.hybrid import DEPTH, collect_legs, read_texts, rrf_sql, run_hybrid, write_legs

IGNORE = r"(\.|[^a-z0-9äöüß])+"


def legs_sql(where: str) -> tuple[str, str]:
    dense = (f"SELECT i, row_number() OVER (ORDER BY dist, i) AS r FROM (SELECT i, "
             f"array_negative_inner_product(vec, ?::FLOAT[{DIM}]) AS dist FROM docs WHERE {where} "
             f"ORDER BY dist LIMIT {DEPTH})")  # fmt: skip
    lexical = (f"SELECT i, row_number() OVER (ORDER BY s DESC, i) AS r FROM (SELECT i, s FROM ("
               f"SELECT i, fts_main_docs.match_bm25(i, ?) AS s FROM docs WHERE {where}) "
               f"WHERE s IS NOT NULL ORDER BY s DESC, i LIMIT {DEPTH})")  # fmt: skip
    return dense, lexical


def main(argv: list[str] | None = None) -> int:
    from zjmark import result, stage  # noqa: PLC0415

    a = parse_args(argv, "c05_duckdb_hybrid", ef_search=EF_SEARCH, query_threads=1)
    store = a.output.parent / "c05h_store"
    shutil.rmtree(store, ignore_errors=True)
    store.mkdir(parents=True)
    with stage("load"):
        import duckdb  # noqa: PLC0415

        con = duckdb.connect(str(store / "zj.duckdb"))
        con.execute("LOAD vss; LOAD fts; SET hnsw_enable_experimental_persistence = true")
        if a.threads > 0:
            con.execute(f"SET threads = {int(a.threads)}")
    with stage("ingest"):
        inp = read_inputs(a.input)
        texts = read_texts(a.input, inp)
        flat = pa.array(np.asarray(inp.vectors, dtype=np.float32).ravel())
        docs = inp.docs.drop_columns(["id"]).append_column(
            "vec", pa.FixedSizeListArray.from_arrays(flat, DIM)
        )
        docs = docs.add_column(0, "i", pa.array(np.arange(inp.n, dtype=np.int32)))
        con.register("docs_arrow", docs)
        con.execute("CREATE TABLE docs AS SELECT i, ort, bezirk, datum, coalesce(doc_text, '') AS doc_text, "
                    f"vec::FLOAT[{DIM}] AS vec FROM docs_arrow")  # fmt: skip
        con.unregister("docs_arrow")
        del flat, docs
    with stage("index"):
        con.execute(f"CREATE INDEX docs_hnsw ON docs USING HNSW (vec) WITH (metric = 'ip', "
                    f"M = {HNSW['m']}, ef_construction = {HNSW['ef_construction']})")  # fmt: skip
        con.execute("PRAGMA create_fts_index('docs', 'i', 'doc_text', stemmer = 'german', "
                    "stopwords = 'none', "
                    f"ignore = '{IGNORE}', strip_accents = 0, lower = 1, overwrite = 1)")  # fmt: skip
        con.execute("CHECKPOINT")
    con.execute(f"SET hnsw_ef_search = {max(int(a.ef_search), DEPTH)}")
    if a.query_threads > 0:  # per-query fan-out costs more than it brings (c05_duckdb_vss)
        con.execute(f"SET threads = {int(a.query_threads)}")

    def search(q: Query, qv: np.ndarray, text: str) -> Sequence[int]:
        where, params = sql_where(q, "?")
        dense, lexical = legs_sql(where)
        rows = con.execute(f"WITH d AS ({dense}), l AS ({lexical}) {rrf_sql()}",
                           [qv, *params, text, *params]).fetchall()  # fmt: skip
        return [int(r[0]) for r in rows]

    def legs(q: Query, qv: np.ndarray, text: str) -> tuple[list[int], list[int]]:
        where, params = sql_where(q, "?")
        dense, lexical = legs_sql(where)
        d = con.execute(f"{dense} ORDER BY r", [qv, *params]).fetchall()
        lx = con.execute(f"{lexical} ORDER BY r", [text, *params]).fetchall()
        return [int(r[0]) for r in d], [int(r[0]) for r in lx]

    with stage("query"):
        top, lat = run_hybrid(inp, texts, search)
    with stage("write"):
        write_output(a.output, top)
    with stage("legs"):
        leg_info = write_legs(a.output, *collect_legs(inp, texts, legs))
    disk = dir_bytes(store)
    con.close()
    shutil.rmtree(store, ignore_errors=True)
    result(rows=inp.n, notes="DuckDB vss HNSW + fts match_bm25 (german stemmer), RRF in one SQL statement",
           extra={
        "engine": "cpu", "duckdb": duckdb.__version__, "latency_ms": latency_ms(lat), "disk_bytes": disk,
        "legs": leg_info, "query_threads": a.query_threads,
        "index": {"type": "hnsw + fts(bm25)", **HNSW, "ef_search": max(a.ef_search, DEPTH), "metric": "ip",
                  "fts": {"stemmer": "german", "stopwords": "none", "ignore": IGNORE, "strip_accents": 0}},
    })  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
