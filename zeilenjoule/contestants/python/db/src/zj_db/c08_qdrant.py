"""Track C contestants c08_qdrant_cpu / c08_qdrant_gpu: Qdrant 1.20 server, HNSW (M=16, efC=200), fp16.

- load: own container (pinned digest; the GPU image for both, `--gpu 1` enables Vulkan GPU indexing).
- ingest: bulk upload over gRPC with indexing disabled (Qdrant's documented bulk-load pattern).
- index: payload indexes (ort, bezirk keyword; day integer), then HNSW indexing switched on; the stage ends
  when the collection is green with every vector indexed. With `--gpu 1` the log must show GPU indexing
  (rule 6), otherwise the run fails.
- query: `query_points` with the filter; Qdrant's planner picks filtered HNSW or a payload-index scan.
"""

from __future__ import annotations

import datetime as dt
import re
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from typing import Any

import numpy as np

from zj_db.common import (
    EF_SEARCH,
    HNSW,
    K,
    Query,
    batches,
    c16_sidecar,
    latency_ms,
    parse_args,
    read_inputs,
    run_concurrent,
    run_queries,
    throughput,
    write_output,
)
from zj_db.server import Container, image_digest, logs, running

IMAGE = ("qdrant/qdrant:v1.20.0-gpu-nvidia"
         "@sha256:002d66bfe0d7115eb4bb60beaa90c21d649e7356756737f1904481caa72cec67")  # fmt: skip
HTTP, GRPC = 16333, 16334
COLL = "docs"
EPOCH = dt.date(1970, 1, 1)
BUILD_LOG = r"building HNSW for (\d+) vectors with \d+ CPUs( and GPU [^\n]+)?"


def container(gpu: bool, threads: int) -> Container:
    env = {"QDRANT__SERVICE__HOST": "127.0.0.1", "QDRANT__SERVICE__HTTP_PORT": str(HTTP),
           "QDRANT__SERVICE__GRPC_PORT": str(GRPC), "QDRANT__TELEMETRY_DISABLED": "true",
           "QDRANT__STORAGE__PERFORMANCE__MAX_SEARCH_THREADS": str(threads)}  # fmt: skip
    extra: list[str] = []
    env["QDRANT__LOG_LEVEL"] = "INFO,segment::index::hnsw_index=debug"  # build lines: CPU vs GPU (rule 6)
    if gpu:
        env |= {"QDRANT__GPU__INDEXING": "true", "QDRANT__GPU__DEVICE_FILTER": "nvidia",
                "NVIDIA_VISIBLE_DEVICES": "all",
                "NVIDIA_DRIVER_CAPABILITIES": "compute,graphics,utility"}  # fmt: skip
        extra = ["--runtime=nvidia"]
    return Container(name="zj-c08-qdrant", image=IMAGE, env=env, extra_docker=extra)


def main(argv: list[str] | None = None) -> int:  # noqa: PLR0915
    import os  # noqa: PLC0415

    from zjmark import result, stage  # noqa: PLC0415

    a = parse_args(argv, "c08_qdrant", ef_search=EF_SEARCH, gpu=0, upload_batch=1024)
    threads = a.threads or os.cpu_count() or 1
    c = container(bool(a.gpu), threads)
    with stage("load"):
        from qdrant_client import QdrantClient, models  # noqa: PLC0415

        client = QdrantClient(host="127.0.0.1", port=HTTP, grpc_port=GRPC, prefer_grpc=True, timeout=600)

        def ready() -> bool:
            client.get_collections()  # raises until the server answers
            return True

        srv = running(c, ready)
        srv.__enter__()
    try:
        with stage("ingest"):
            inp = read_inputs(a.input)
            client.create_collection(
                COLL,
                vectors_config=models.VectorParams(size=inp.vectors.shape[1], distance=models.Distance.DOT,
                                                   datatype=models.Datatype.FLOAT16),
                hnsw_config=models.HnswConfigDiff(m=HNSW["m"], ef_construct=HNSW["ef_construction"]),
                optimizers_config=models.OptimizersConfigDiff(indexing_threshold=0),  # bulk: index later
            )  # fmt: skip
            ort = inp.docs.column("ort").to_pylist()
            bez = inp.docs.column("bezirk").to_pylist()
            dat = inp.docs.column("datum").to_pylist()
            for s, e in batches(inp.n, a.upload_batch):
                v = np.asarray(inp.vectors[s:e], dtype=np.float32)
                payload: list[dict[str, Any]] = []
                for i in range(s, e):
                    p: dict[str, Any] = {}
                    if ort[i] is not None:
                        p["ort"] = ort[i]
                    if bez[i] is not None:
                        p["bezirk"] = bez[i]
                    if (di := dat[i]) is not None:
                        p["day"] = (di - EPOCH).days
                    payload.append(p)
                client.upload_collection(COLL, vectors=v, payload=payload, ids=list(range(s, e)),
                                         parallel=1, wait=True)  # fmt: skip
        with stage("index"):
            for field, schema in (("ort", models.PayloadSchemaType.KEYWORD),
                                  ("bezirk", models.PayloadSchemaType.KEYWORD),
                                  ("day", models.PayloadSchemaType.INTEGER)):  # fmt: skip
                client.create_payload_index(COLL, field, field_schema=schema, wait=True)
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
        builds = re.findall(BUILD_LOG, logs(c))
        gpu_builds = sum(1 for _, g in builds if g)
        if a.gpu and (not builds or gpu_builds != len(builds)):
            raise RuntimeError(f"--gpu 1 but only {gpu_builds}/{len(builds)} HNSW builds on the GPU "
                               "(rule 6):\n"
                               + logs(c)[-4000:])  # fmt: skip
        params = models.SearchParams(hnsw_ef=a.ef_search)

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

        def make_search(cl: Any) -> Callable[[Query, np.ndarray], Sequence[int]]:
            def search(q: Query, qv: np.ndarray) -> Sequence[int]:
                r = cl.query_points(COLL, query=qv.tolist(), query_filter=flt(q), limit=K,
                                    search_params=params, with_payload=False)  # fmt: skip
                return [int(p.id) for p in r.points]

            return search

        with stage("query"):
            top, lat = run_queries(inp, make_search(client))
        clients: list[Any] = []

        def per_thread() -> Callable[[Query, np.ndarray], Sequence[int]]:
            cl = QdrantClient(host="127.0.0.1", port=HTTP, grpc_port=GRPC, prefer_grpc=True, timeout=600)
            clients.append(cl)  # one gRPC channel per client thread
            return make_search(cl)

        with stage("query_c16"):
            top16, wall16 = run_concurrent(inp, per_thread)
        for cl in clients:
            cl.close()
        with stage("write"):
            write_output(a.output, top)
            write_output(c16_sidecar(a.output), top16)
        du = subprocess.run(["docker", "exec", c.name, "du", "-sb", "/qdrant/storage"], capture_output=True,
                            text=True, check=False).stdout.split()  # fmt: skip
        info = client.get_collection(COLL)
    finally:
        srv.__exit__(None, None, None)
    result(rows=inp.n, notes=f"Qdrant HNSW fp16 Dot, payload indexes, {'GPU' if a.gpu else 'CPU'} indexing",
           extra={
        "engine": "gpu indexing (vulkan)" if a.gpu else "cpu (server)", "image": image_digest(IMAGE),
        "hnsw_builds": len(builds), "hnsw_builds_gpu": gpu_builds, "latency_ms": latency_ms(lat),
        "throughput": throughput(lat, top, top16, wall16),
        "disk_bytes": int(du[0]) if du else None,
        "segments": info.segments_count,
        "index": {"type": "hnsw", **HNSW, "ef_search": a.ef_search, "datatype": "float16",
                  "gpu": bool(a.gpu)},
    })  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
