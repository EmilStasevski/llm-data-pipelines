"""Track E stores (S5): vector + full-text index + read-only SQL view `meldungen` (spec/track_e.md §3).

Every store gets the same table (VIEW_COLUMNS + doc_text) and the bge-m3 vectors and offers
`sql(text)` (already checked and transpiled by zj_e2e.sqlsafe; executed read-only with a 10 s limit) and
`search(text, qvec, filters, k)` (row ids). Hybrid stores fuse a dense and a lexical top-100 by RRF k = 60
(the Track C c.hybrid way of each engine); e1's FAISS store is dense-only with post-filtering, the way most
tutorials do it.
"""

from __future__ import annotations

import datetime as dt
import decimal
import sqlite3
import threading
from collections.abc import Callable, Sequence
from contextlib import ExitStack
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import pyarrow as pa

SQL_TIMEOUT_S = 10.0
DEPTH, RRF_K = 100, 60
COLUMNS = (
    "meldung_id",
    "werk",
    "regierungsbezirk",
    "anlage",
    "equipment_nr",
    "datum",
    "prioritaet",
    "stillstand_min",
    "kosten_eur",
    "komponente",
    "schadensart",
    "sicherheitsrelevant",
    "meldungstext",
)
Filters = dict[
    str, Any
]  # werk, regierungsbezirk, komponente, schadensart, datum_von/_bis (ISO) – None = none
KEYWORD_FILTERS = ("werk", "regierungsbezirk", "komponente", "schadensart")


class Store(Protocol):
    dialect: str

    def build(self, table: pa.Table, vecs: np.ndarray) -> dict[str, Any]: ...
    def sql(self, text: str) -> tuple[list[str], list[list[Any]]]: ...
    def search(self, text: str, qvec: np.ndarray, f: Filters, k: int) -> list[str]: ...
    def close(self) -> None: ...


def plain(v: Any) -> Any:
    """JSON-safe cell value."""
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    return v


def _date(s: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(s)) if s else None
    except ValueError:
        return None


def where(f: Filters, ph: str) -> tuple[str, list[Any]]:
    conds, params = ["TRUE"], []
    for col, key in (
        ("werk", "werk"),
        ("regierungsbezirk", "regierungsbezirk"),
        ("komponente", "komponente"),
        ("schadensart", "schadensart"),
    ):
        if f.get(key):
            conds.append(f"{col} = {ph}")
            params.append(str(f[key]))
    if d := _date(f.get("datum_von")):
        conds.append(f"datum >= {ph}")
        params.append(d)
    if d := _date(f.get("datum_bis")):
        conds.append(f"datum <= {ph}")
        params.append(d)
    return " AND ".join(conds), params


def rrf(*legs: Sequence[int], k: int) -> list[int]:
    score: dict[int, float] = {}
    for leg in legs:
        for r, i in enumerate(leg, 1):
            score[i] = score.get(i, 0.0) + 1.0 / (RRF_K + r)
    return [i for i, _ in sorted(score.items(), key=lambda kv: (-kv[1], kv[0]))][:k]


def with_timeout(interrupt: Callable[[], None], fn: Callable[[], Any]) -> Any:
    t = threading.Timer(SQL_TIMEOUT_S, interrupt)
    t.start()
    try:
        return fn()
    finally:
        t.cancel()


# ------------------------------------------------------------------ DuckDB (e2, and SQL side of e5)
class DuckDBStore:
    """DuckDB vss HNSW + fts BM25 + SQL in one file (c05_duckdb_hybrid settings)."""

    dialect = "duckdb"

    def __init__(self, path: Path, vectors: bool = True) -> None:
        import duckdb  # noqa: PLC0415

        path.unlink(missing_ok=True)
        self.con = duckdb.connect(str(path))
        self.vectors = vectors
        self.ids: list[str] = []

    def build(self, table: pa.Table, vecs: np.ndarray) -> dict[str, Any]:
        from zj_db.c05_duckdb_hybrid import IGNORE  # noqa: PLC0415
        from zj_db.common import HNSW  # noqa: PLC0415

        self.ids = table.column("meldung_id").to_pylist()
        t = table.append_column("i", pa.array(np.arange(table.num_rows, dtype=np.int32)))
        if self.vectors:
            flat = pa.array(np.asarray(vecs, dtype=np.float32).ravel())
            t = t.append_column("vec", pa.FixedSizeListArray.from_arrays(flat, vecs.shape[1]))
        self.con.register("src", t)
        vec = f", vec::FLOAT[{vecs.shape[1]}] AS vec" if self.vectors else ""
        self.con.execute(f"CREATE TABLE docs AS SELECT i, {', '.join(COLUMNS)}, doc_text{vec} FROM src")
        self.con.unregister("src")
        self.con.execute(f"CREATE VIEW meldungen AS SELECT {', '.join(COLUMNS)} FROM docs")
        if self.vectors:
            self.con.execute("LOAD vss; LOAD fts; SET hnsw_enable_experimental_persistence = true")
            self.con.execute(
                f"CREATE INDEX docs_hnsw ON docs USING HNSW (vec) WITH (metric = 'ip', "
                f"M = {HNSW['m']}, ef_construction = {HNSW['ef_construction']})"
            )
            self.con.execute(
                "PRAGMA create_fts_index('docs', 'i', 'doc_text', stemmer = 'german', stopwords = "
                f"'none', ignore = '{IGNORE}', strip_accents = 0, lower = 1, overwrite = 1)"
            )
            self.con.execute(f"SET hnsw_ef_search = {DEPTH}")
        self.con.execute("CHECKPOINT")
        return {"store": "duckdb", "rows": table.num_rows}

    def sql(self, text: str) -> tuple[list[str], list[list[Any]]]:
        cur = self.con.cursor()
        cur.execute("SET threads = 4")

        def run() -> tuple[list[str], list[list[Any]]]:
            cur.execute(text)
            cols = [d[0] for d in cur.description or []]
            return cols, [[plain(v) for v in row] for row in cur.fetchmany(10_000)]

        try:
            return with_timeout(cur.interrupt, run)
        finally:
            cur.close()

    def search(self, text: str, qvec: np.ndarray, f: Filters, k: int) -> list[str]:
        w, params = where(f, "?")
        dim = qvec.shape[0]
        dense = self.con.execute(
            f"SELECT i FROM docs WHERE {w} ORDER BY array_negative_inner_product(vec, "
            f"?::FLOAT[{dim}]) LIMIT {DEPTH}",
            [*params, qvec.astype(np.float32)],
        ).fetchall()
        lex = self.con.execute(
            f"SELECT i FROM (SELECT i, fts_main_docs.match_bm25(i, ?) AS s FROM docs WHERE {w}) "
            f"WHERE s IS NOT NULL ORDER BY s DESC, i LIMIT {DEPTH}",
            [text, *params],
        ).fetchall()
        return [self.ids[i] for i in rrf([r[0] for r in dense], [r[0] for r in lex], k=k)]

    def close(self) -> None:
        self.con.close()


# ------------------------------------------------------------------ FAISS + sqlite3 (e1)
class FaissSqliteStore:
    """The tutorial stack: FAISS IndexFlatIP (exact, CPU) dense-only, filters applied afterwards in Python on
    an oversampled candidate list; pandas → sqlite3 (in memory) for SQL."""

    dialect = "sqlite"
    OVERSAMPLE = 5000

    def __init__(self) -> None:
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        self.meta: dict[str, list[Any]] = {}
        self.ids: list[str] = []

    def build(self, table: pa.Table, vecs: np.ndarray) -> dict[str, Any]:
        import faiss  # noqa: PLC0415

        df = table.select(list(COLUMNS)).to_pandas()
        df["datum"] = df["datum"].astype(str)
        df.to_sql("meldungen", self.db, index=False)
        self.ids = table.column("meldung_id").to_pylist()
        for c in (*KEYWORD_FILTERS, "datum"):
            self.meta[c] = table.column(c).to_pylist()
        self.index = faiss.IndexFlatIP(vecs.shape[1])
        self.index.add(np.asarray(vecs, dtype=np.float32))
        self.db.execute("PRAGMA query_only = ON")
        return {"store": "faiss-flat + sqlite3", "rows": table.num_rows}

    def sql(self, text: str) -> tuple[list[str], list[list[Any]]]:
        def run() -> tuple[list[str], list[list[Any]]]:
            cur = self.db.execute(text)
            return [d[0] for d in cur.description or []], [
                [plain(v) for v in r] for r in cur.fetchmany(10_000)
            ]

        return with_timeout(self.db.interrupt, run)

    def search(self, text: str, qvec: np.ndarray, f: Filters, k: int) -> list[str]:
        del text  # dense only
        _, idx = self.index.search(np.asarray(qvec, dtype=np.float32)[None, :], self.OVERSAMPLE)
        lo, hi = _date(f.get("datum_von")), _date(f.get("datum_bis"))
        out: list[str] = []
        for i in idx[0]:
            if i < 0:
                break
            d = self.meta["datum"][i]
            if any(f.get(c) and self.meta[c][i] != f[c] for c in KEYWORD_FILTERS):
                continue
            if (lo and (d is None or d < lo)) or (hi and (d is None or d > hi)):
                continue
            out.append(self.ids[i])
            if len(out) == k:
                break
        return out

    def close(self) -> None:
        self.db.close()


# ------------------------------------------------------------------ Postgres + pgvector (e3)
class PostgresStore:
    """PostgreSQL 18 + pgvector halfvec HNSW + tsvector('german') GIN + SQL view, queried as a SELECT-only role
    with statement_timeout (c03_pgvector_hybrid settings)."""

    dialect = "postgres"
    PORT = 15442

    def __init__(self, threads: int) -> None:
        from zj_db import c03_pgvector  # noqa: PLC0415
        from zj_db.server import Container  # noqa: PLC0415

        base = c03_pgvector.container(threads)
        args = [a if not a.startswith("port=") else f"port={self.PORT}" for a in base.args]
        self.container = Container(
            name="zj-e3-postgres", image=base.image, args=args, env=base.env, extra_docker=base.extra_docker
        )
        self.stack = ExitStack()
        self.dsn = f"host=127.0.0.1 port={self.PORT} dbname=postgres"
        self.ids: list[str] = []

    def start(self) -> None:
        import psycopg  # noqa: PLC0415

        from zj_db.server import running  # noqa: PLC0415

        def ready() -> bool:
            with psycopg.connect(self.dsn + " user=postgres", connect_timeout=2):
                return True

        self.stack.enter_context(running(self.container, ready))

    def build(self, table: pa.Table, vecs: np.ndarray) -> dict[str, Any]:
        import psycopg  # noqa: PLC0415
        from pgvector import HalfVector  # noqa: PLC0415
        from pgvector.psycopg import register_vector  # noqa: PLC0415

        from zj_db.common import HNSW  # noqa: PLC0415

        self.ids = table.column("meldung_id").to_pylist()
        dim = vecs.shape[1]
        with psycopg.connect(self.dsn + " user=postgres", autocommit=True) as conn:
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            register_vector(conn)
            conn.execute(
                "CREATE TABLE docs (i int4 PRIMARY KEY, meldung_id text, werk text, regierungsbezirk text, "
                "anlage text, equipment_nr text, datum date, prioritaet int, stillstand_min int, kosten_eur float8, "
                "komponente text, schadensart text, sicherheitsrelevant bool, meldungstext text, doc_text text, "
                f"emb halfvec({dim}), tsv tsvector GENERATED ALWAYS AS (to_tsvector('german', doc_text)) STORED)"
            )
            cols = table.select([*COLUMNS, "doc_text"]).to_pylist()
            with conn.cursor().copy(
                "COPY docs (i, meldung_id, werk, regierungsbezirk, anlage, equipment_nr, datum, prioritaet, "
                "stillstand_min, kosten_eur, komponente, schadensart, sicherheitsrelevant, meldungstext, doc_text, "
                "emb) FROM STDIN"
            ) as cp:
                for i, r in enumerate(cols):
                    cp.write_row([i, *(r[c] for c in COLUMNS), r["doc_text"], HalfVector(vecs[i])])
            conn.execute(
                f"CREATE INDEX docs_hnsw ON docs USING hnsw (emb halfvec_ip_ops) WITH (m = {HNSW['m']}, "
                f"ef_construction = {HNSW['ef_construction']})"
            )
            conn.execute("CREATE INDEX docs_tsv ON docs USING gin (tsv)")
            for c in ("werk", "regierungsbezirk", "datum", "equipment_nr", "komponente", "schadensart"):
                conn.execute(f"CREATE INDEX docs_{c} ON docs ({c})")
            conn.execute(
                f"CREATE VIEW meldungen AS SELECT {', '.join(COLUMNS)} FROM docs".replace(
                    "kosten_eur", "kosten_eur::numeric AS kosten_eur"
                )
            )
            conn.execute("CREATE ROLE reader LOGIN")
            conn.execute("GRANT SELECT ON meldungen TO reader")
            conn.execute(f"ALTER ROLE reader SET statement_timeout = '{int(SQL_TIMEOUT_S * 1000)}ms'")
            conn.execute("ALTER ROLE reader SET default_transaction_read_only = on")
            conn.execute("ANALYZE docs")
        self.reader = psycopg.connect(self.dsn + " user=reader", autocommit=True)
        self.search_conn = psycopg.connect(self.dsn + " user=postgres", autocommit=True)
        register_vector(self.search_conn)
        self.search_conn.execute(f"SET hnsw.ef_search = {DEPTH}; SET hnsw.iterative_scan = strict_order")
        return {"store": "postgres + pgvector", "rows": table.num_rows}

    def sql(self, text: str) -> tuple[list[str], list[list[Any]]]:
        cur = self.reader.execute(text)  # pyright: ignore[reportArgumentType, reportCallIssue]
        return [d.name for d in cur.description or []], [[plain(v) for v in r] for r in cur.fetchmany(10_000)]

    def search(self, text: str, qvec: np.ndarray, f: Filters, k: int) -> list[str]:
        from pgvector import HalfVector  # noqa: PLC0415

        w, params = where(f, "%s")
        tsq = "replace(plainto_tsquery('german', %s)::text, '&', '|')::tsquery"
        dense = self.search_conn.execute(
            f"SELECT i FROM docs WHERE {w} ORDER BY emb <#> %s LIMIT {DEPTH}",  # pyright: ignore[reportArgumentType]
            [*params, HalfVector(qvec)],
        ).fetchall()
        lex = self.search_conn.execute(
            f"SELECT i FROM docs, (SELECT {tsq} AS q) t WHERE {w} AND tsv @@ t.q "  # pyright: ignore[reportArgumentType]
            f"ORDER BY ts_rank(tsv, t.q) DESC, i LIMIT {DEPTH}",
            [text, *params],
        ).fetchall()
        return [self.ids[i] for i in rrf([r[0] for r in dense], [r[0] for r in lex], k=k)]

    def close(self) -> None:
        for c in (getattr(self, "reader", None), getattr(self, "search_conn", None)):
            if c is not None:
                c.close()
        self.stack.close()


# ------------------------------------------------------------------ Qdrant + DuckDB SQL (e5)
class QdrantDuckStore:
    """Track C hybrid winner (Qdrant native BM25 + HNSW, server-side RRF) for search; DuckDB (no vectors) holds
    the SQL view."""

    dialect = "duckdb"

    def __init__(self, path: Path, threads: int) -> None:
        from zj_db.c08_qdrant import container  # noqa: PLC0415

        self.duck = DuckDBStore(path, vectors=False)
        self.container = container(False, threads)
        self.stack = ExitStack()
        self.ids: list[str] = []

    def start(self) -> None:
        from qdrant_client import QdrantClient  # noqa: PLC0415

        from zj_db.c08_qdrant import GRPC, HTTP  # noqa: PLC0415
        from zj_db.server import running  # noqa: PLC0415

        self.client = QdrantClient(host="127.0.0.1", port=HTTP, grpc_port=GRPC, prefer_grpc=True, timeout=600)

        def ready() -> bool:
            self.client.get_collections()
            return True

        self.stack.enter_context(running(self.container, ready))

    def build(self, table: pa.Table, vecs: np.ndarray) -> dict[str, Any]:
        from qdrant_client import models  # noqa: PLC0415

        from zj_db.c08_qdrant import COLL, EPOCH  # noqa: PLC0415
        from zj_db.common import HNSW, batches  # noqa: PLC0415

        info = self.duck.build(table, vecs)
        self.ids = table.column("meldung_id").to_pylist()
        self.models = models
        self.client.create_collection(
            COLL,
            vectors_config={
                "dense": models.VectorParams(
                    size=vecs.shape[1], distance=models.Distance.DOT, datatype=models.Datatype.FLOAT16
                )
            },
            hnsw_config=models.HnswConfigDiff(m=HNSW["m"], ef_construct=HNSW["ef_construction"]),
            optimizers_config=models.OptimizersConfigDiff(indexing_threshold=0),
        )
        cols = {c: table.column(c).to_pylist() for c in (*KEYWORD_FILTERS, "datum", "doc_text")}
        for s, e in batches(table.num_rows, 1024):
            payload = [
                {
                    "doc_text": cols["doc_text"][i] or "",
                    **{c: cols[c][i] for c in KEYWORD_FILTERS},
                    "day": (cols["datum"][i] - EPOCH).days if cols["datum"][i] else None,
                }
                for i in range(s, e)
            ]
            self.client.upload_collection(
                COLL,
                vectors={"dense": np.asarray(vecs[s:e], dtype=np.float32)},
                payload=payload,
                ids=list(range(s, e)),
                parallel=1,
                wait=True,
            )
        for fld, sch in [
            *((c, models.PayloadSchemaType.KEYWORD) for c in KEYWORD_FILTERS),
            ("day", models.PayloadSchemaType.INTEGER),
        ]:
            self.client.create_payload_index(COLL, fld, field_schema=sch, wait=True)
        self.client.create_payload_index(
            COLL,
            "doc_text",
            wait=True,
            field_schema=models.TextIndexParams(
                type=models.TextIndexType.TEXT,
                tokenizer=models.TokenizerType.WORD,
                lowercase=True,
                ascii_folding=False,
                stopwords=models.StopwordsSet(languages=[], custom=[]),
                stemmer=models.SnowballParams(
                    type=models.Snowball.SNOWBALL, language=models.SnowballLanguage.GERMAN
                ),
                scoring=models.TextScoringParams(type=models.TextScoringType.BM25),
            ),
        )
        self.client.update_collection(
            COLL, optimizers_config=models.OptimizersConfigDiff(indexing_threshold=1)
        )
        import time  # noqa: PLC0415

        while True:
            ci = self.client.get_collection(COLL)
            if (
                ci.status == models.CollectionStatus.GREEN
                and (ci.indexed_vectors_count or 0) >= table.num_rows
            ):
                break
            time.sleep(0.2)
        return {**info, "store": "qdrant hybrid + duckdb sql"}

    def sql(self, text: str) -> tuple[list[str], list[list[Any]]]:
        return self.duck.sql(text)

    def search(self, text: str, qvec: np.ndarray, f: Filters, k: int) -> list[str]:
        from zj_db.c08_qdrant import COLL, EPOCH  # noqa: PLC0415

        m = self.models
        must: list[Any] = []
        for key in KEYWORD_FILTERS:
            if f.get(key):
                must.append(m.FieldCondition(key=key, match=m.MatchValue(value=str(f[key]))))
        lo, hi = _date(f.get("datum_von")), _date(f.get("datum_bis"))
        if lo or hi:
            must.append(
                m.FieldCondition(
                    key="day",
                    range=m.Range(
                        gte=(lo - EPOCH).days if lo else None, lte=(hi - EPOCH).days if hi else None
                    ),
                )
            )
        flt = m.Filter(must=must) if must else None
        params = m.SearchParams(hnsw_ef=DEPTH)
        r = self.client.query_points(
            COLL,
            prefetch=[
                m.Prefetch(
                    query=qvec.astype(np.float32).tolist(),
                    using="dense",
                    filter=flt,
                    limit=DEPTH,
                    params=params,
                ),
                m.Prefetch(
                    query=m.TextQuery(text=m.TextQueryInput(query=text)),
                    using="doc_text",
                    filter=flt,
                    limit=DEPTH,
                ),
            ],
            query=m.RrfQuery(rrf=m.Rrf(k=RRF_K)),
            limit=k,
            with_payload=False,
        )
        return [self.ids[int(p.id)] for p in r.points]

    def close(self) -> None:
        self.duck.close()
        self.stack.close()
