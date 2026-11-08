"""Track C contestant c07_lancedb_hybrid (`c.hybrid`): LanceDB native hybrid search (vector + FTS, RRF).

Same table and IVF_HNSW_SQ index as c07_lancedb, plus LanceDB's native full-text index on `doc_text`
(BM25; `simple` tokenizer, lower case, Snowball `German` stemmer, no stop-word removal, umlauts kept) and
the scalar indexes. Query = `search(query_type="hybrid")` with the filter as prefilter for both legs, each
leg `limit(100)`, fused by LanceDB's `RRFReranker` (k = 60, ranks from 1); the first 10 fused rows count.
"""

from __future__ import annotations

import shutil
import sys
from collections.abc import Sequence
from typing import Any

import numpy as np
import pyarrow as pa

from zj_db.c07_lancedb import where
from zj_db.common import (
    DIM,
    EF_SEARCH,
    HNSW,
    K,
    Query,
    dir_bytes,
    latency_ms,
    parse_args,
    read_inputs,
    write_output,
)
from zj_db.hybrid import DEPTH, RRF_K, collect_legs, read_texts, run_hybrid, write_legs


def main(argv: list[str] | None = None) -> int:
    from zjmark import result, stage  # noqa: PLC0415

    a = parse_args(argv, "c07_lancedb_hybrid", ef_search=EF_SEARCH, partitions=0)
    store = a.output.parent / "c07h_store"
    shutil.rmtree(store, ignore_errors=True)
    with stage("load"):
        import os  # noqa: PLC0415

        # the hybrid builder's inner legs select ["i"] → one scoring-autoprojection deprecation warning per
        # leg and query (not switchable from Python in 0.41); log errors only
        os.environ.setdefault("LANCEDB_LOG", "error")
        import lancedb  # noqa: PLC0415
        from lancedb.rerankers import RRFReranker  # noqa: PLC0415

        db = lancedb.connect(str(store))
    with stage("ingest"):
        inp = read_inputs(a.input)
        texts = read_texts(a.input, inp)
        vec = pa.FixedSizeListArray.from_arrays(pa.array(np.asarray(inp.vectors).ravel(), pa.float16()), DIM)
        t = inp.docs.drop_columns(["id"]).append_column("vector", vec)
        t = t.add_column(0, "i", pa.array(np.arange(inp.n, dtype=np.int32)))
        tbl: Any = db.create_table("docs", t)
        del vec, t
    parts = a.partitions or max(1, round(inp.n**0.5 / 4))  # every partition probed (c07_lancedb, ADR 0016)
    with stage("index"):
        tbl.create_index(metric="dot", vector_column_name="vector", index_type="IVF_HNSW_SQ",
                         num_partitions=parts, m=HNSW["m"],
                         ef_construction=HNSW["ef_construction"])  # fmt: skip
        tbl.create_fts_index("doc_text", use_tantivy=False, base_tokenizer="simple", language="German",
                             lower_case=True, stem=True, remove_stop_words=False, ascii_folding=False,
                             with_position=False, max_token_length=40)  # fmt: skip
        tbl.create_scalar_index("ort", index_type="BITMAP")
        tbl.create_scalar_index("bezirk", index_type="BITMAP")
        tbl.create_scalar_index("datum", index_type="BTREE")
    ef = max(a.ef_search, DEPTH)
    reranker = RRFReranker(K=RRF_K)

    def search(q: Query, qv: np.ndarray, text: str) -> Sequence[int]:
        s = (tbl.search(query_type="hybrid", vector_column_name="vector", fts_columns="doc_text")
             .vector(qv).text(text).distance_type("dot").nprobes(parts).ef(ef).limit(DEPTH)
             .rerank(reranker).select(["i"]))  # fmt: skip
        w = where(q)
        if w:
            s = s.where(w, prefilter=True)
        return s.to_arrow().column("i").to_pylist()[:K]

    def legs(q: Query, qv: np.ndarray, text: str) -> tuple[list[int], list[int]]:
        w = where(q)
        d = (tbl.search(qv, vector_column_name="vector").distance_type("dot").nprobes(parts).ef(ef)
             .limit(DEPTH).select(["i", "_distance"]))  # fmt: skip
        lx = tbl.search(text, query_type="fts", fts_columns="doc_text").limit(DEPTH).select(["i", "_score"])
        if w:
            d, lx = d.where(w, prefilter=True), lx.where(w, prefilter=True)
        return d.to_arrow().column("i").to_pylist(), lx.to_arrow().column("i").to_pylist()

    with stage("query"):
        top, lat = run_hybrid(inp, texts, search)
    with stage("write"):
        write_output(a.output, top)
    with stage("legs"):
        leg_info = write_legs(a.output, *collect_legs(inp, texts, legs))
    disk = dir_bytes(store)
    shutil.rmtree(store, ignore_errors=True)
    result(rows=inp.n, notes="LanceDB native hybrid: IVF_HNSW_SQ + native FTS (BM25, German stemmer), "
           "RRFReranker, prefilter", extra={
        "engine": "cpu", "lancedb": lancedb.__version__, "latency_ms": latency_ms(lat), "disk_bytes": disk,
        "legs": leg_info,
        "index": {"type": "ivf_hnsw_sq + fts(bm25)", **HNSW, "ef_search": ef, "partitions": parts,
                  "nprobes": parts, "fts": {"tokenizer": "simple", "language": "German", "stem": True}},
    })  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
