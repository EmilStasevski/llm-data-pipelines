"""Track C contestant c08_qdrant_hybrid (`c.hybrid`): Qdrant 1.20 native hybrid query (dense + BM25, RRF).

Same container, upload pattern, HNSW and payload indexes as c08_qdrant_cpu (CPU indexing), plus a full-text
payload index on `doc_text` with BM25 scoring (`word` tokenizer, lower case, Snowball German stemmer, no
stop words). One `query_points` per query: two prefetches with the filter (dense HNSW, limit 100; BM25
`TextQuery`, limit 100) fused server-side by `RrfQuery(k=60)`.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Sequence
from typing import Any

import numpy as np

from zj_db.c08_qdrant import COLL, EPOCH, GRPC, HTTP, IMAGE, container
from zj_db.common import (
    EF_SEARCH,
    HNSW,
    K,
    Query,
    batches,
    latency_ms,
    parse_args,
    read_inputs,
    write_output,
)
from zj_db.hybrid import DEPTH, RRF_K, collect_legs, read_texts, run_hybrid, write_legs
from zj_db.server import image_digest, running

DENSE, TEXT = "dense", "doc_text"


def main(argv: list[str] | None = None) -> int:  # noqa: PLR0915
    from zjmark import result, stage  # noqa: PLC0415

    a = parse_args(argv, "c08_qdrant_hybrid", ef_search=EF_SEARCH, upload_batch=1024)
    threads = a.threads or os.cpu_count() or 1
    c = container(False, threads)
    with stage("load"):
        from qdrant_client import QdrantClient, models  # noqa: PLC0415

        client = QdrantClient(host="127.0.0.1", port=HTTP, grpc_port=GRPC, prefer_grpc=True, timeout=600)

        def ready() -> bool:
            client.get_collections()
            return True

        srv = running(c, ready)
        srv.__enter__()
    try:
        with stage("ingest"):
            inp = read_inputs(a.input)
            texts = read_texts(a.input, inp)
            client.create_collection(
                COLL,
                vectors_config={DENSE: models.VectorParams(size=inp.vectors.shape[1],
                                                           distance=models.Distance.DOT,
                                                           datatype=models.Datatype.FLOAT16)},
                hnsw_config=models.HnswConfigDiff(m=HNSW["m"], ef_construct=HNSW["ef_construction"]),
                optimizers_config=models.OptimizersConfigDiff(indexing_threshold=0),
            )  # fmt: skip
            ort = inp.docs.column("ort").to_pylist()
            bez = inp.docs.column("bezirk").to_pylist()
            dat = inp.docs.column("datum").to_pylist()
            txt = inp.docs.column("doc_text").to_pylist()
            for s, e in batches(inp.n, a.upload_batch):
                v = np.asarray(inp.vectors[s:e], dtype=np.float32)
                payload: list[dict[str, Any]] = []
                for i in range(s, e):
                    p: dict[str, Any] = {TEXT: txt[i] or ""}
                    if ort[i] is not None:
                        p["ort"] = ort[i]
                    if bez[i] is not None:
                        p["bezirk"] = bez[i]
                    if (di := dat[i]) is not None:
                        p["day"] = (di - EPOCH).days
                    payload.append(p)
                client.upload_collection(COLL, vectors={DENSE: v}, payload=payload, ids=list(range(s, e)),
                                         parallel=1, wait=True)  # fmt: skip
        with stage("index"):
            for field, schema in (("ort", models.PayloadSchemaType.KEYWORD),
                                  ("bezirk", models.PayloadSchemaType.KEYWORD),
                                  ("day", models.PayloadSchemaType.INTEGER)):  # fmt: skip
                client.create_payload_index(COLL, field, field_schema=schema, wait=True)
            client.create_payload_index(COLL, TEXT, wait=True, field_schema=models.TextIndexParams(
                type=models.TextIndexType.TEXT, tokenizer=models.TokenizerType.WORD, lowercase=True,
                ascii_folding=False, stopwords=models.StopwordsSet(languages=[], custom=[]),
                stemmer=models.SnowballParams(type=models.Snowball.SNOWBALL,
                                              language=models.SnowballLanguage.GERMAN),
                scoring=models.TextScoringParams(type=models.TextScoringType.BM25)))  # fmt: skip
            client.update_collection(
                COLL, optimizers_config=models.OptimizersConfigDiff(indexing_threshold=1)
            )
            while True:
                info = client.get_collection(COLL)
                if (
                    info.status == models.CollectionStatus.GREEN
                    and (info.indexed_vectors_count or 0) >= inp.n
                ):
                    break
                time.sleep(0.05)
        params = models.SearchParams(hnsw_ef=max(a.ef_search, DEPTH))

        def flt(q: Query) -> Any:
            must: list[Any] = []
            if q.ort is not None:
                must.append(models.FieldCondition(key="ort", match=models.MatchValue(value=q.ort)))
            if q.bezirk is not None:
                must.append(models.FieldCondition(key="bezirk", match=models.MatchValue(value=q.bezirk)))
            if q.has_date:
                must.append(models.FieldCondition(key="day", range=models.Range(
                    gte=(q.date_from - EPOCH).days if q.date_from else None,
                    lte=(q.date_to - EPOCH).days if q.date_to else None)))  # fmt: skip
            return models.Filter(must=must) if must else None

        def prefetches(q: Query, qv: np.ndarray, text: str) -> tuple[Any, Any]:
            f = flt(q)
            dense = models.Prefetch(query=qv.tolist(), using=DENSE, filter=f, limit=DEPTH, params=params)
            lexical = models.Prefetch(query=models.TextQuery(text=models.TextQueryInput(query=text)),
                                      using=TEXT, filter=f, limit=DEPTH)  # fmt: skip
            return dense, lexical

        def search(q: Query, qv: np.ndarray, text: str) -> Sequence[int]:
            r = client.query_points(COLL, prefetch=list(prefetches(q, qv, text)),
                                    query=models.RrfQuery(rrf=models.Rrf(k=RRF_K)), limit=K,
                                    with_payload=False)  # fmt: skip
            return [int(p.id) for p in r.points]

        def legs(q: Query, qv: np.ndarray, text: str) -> tuple[list[int], list[int]]:
            out: list[list[int]] = []
            for p in prefetches(q, qv, text):
                r = client.query_points(COLL, query=p.query, using=p.using, query_filter=p.filter,
                                        limit=DEPTH, search_params=p.params, with_payload=False)  # fmt: skip
                out.append([int(x.id) for x in r.points])
            return out[0], out[1]

        with stage("query"):
            top, lat = run_hybrid(inp, texts, search)
        with stage("write"):
            write_output(a.output, top)
        with stage("legs"):
            leg_info = write_legs(a.output, *collect_legs(inp, texts, legs))
        info = client.get_collection(COLL)
    finally:
        srv.__exit__(None, None, None)
    result(rows=inp.n, notes="Qdrant native hybrid: HNSW fp16 + BM25 text index (German stemmer), "
           "server-side RRF over two filtered prefetches", extra={
        "engine": "cpu (server)", "image": image_digest(IMAGE), "latency_ms": latency_ms(lat),
        "legs": leg_info, "segments": info.segments_count,
        "index": {"type": "hnsw + text(bm25)", **HNSW, "ef_search": max(a.ef_search, DEPTH),
                  "datatype": "float16", "text": {"tokenizer": "word", "stemmer": "snowball german"}},
    })  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
