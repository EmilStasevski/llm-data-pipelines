"""Track C contestant c06_sqlite_vec: SQLite + sqlite-vec `vec0` (float32, exact KNN), on-disk.

sqlite-vec scans exactly (no ANN index); filters are vec0 metadata columns evaluated inside the KNN scan.
vec0 metadata columns cannot be NULL (sqlite-vec 0.1.9), so a missing ort/bezirk/date is stored as '' and
every date filter carries a lower bound ('' sorts before any ISO date → never matches, as the spec requires).
"""

from __future__ import annotations

import datetime as dt
import shutil
import sqlite3
import sys
from collections.abc import Callable, Sequence

import numpy as np

from zj_db.common import (
    DIM,
    K,
    Query,
    batches,
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

NO_DATE = "0001-01-01"


def iso(d: dt.date | None) -> str:
    return d.isoformat() if d is not None else ""


def where(q: Query) -> tuple[str, list[object]]:
    parts, params = [], []
    if q.ort is not None:
        parts.append("AND ort = ?")
        params.append(q.ort)
    if q.bezirk is not None:
        parts.append("AND bezirk = ?")
        params.append(q.bezirk)
    if q.has_date:
        parts.append("AND datum >= ?")
        params.append(q.date_from.isoformat() if q.date_from else NO_DATE)
        if q.date_to is not None:
            parts.append("AND datum <= ?")
            params.append(q.date_to.isoformat())
    return " ".join(parts), params


def main(argv: list[str] | None = None) -> int:
    from zjmark import result, stage  # noqa: PLC0415

    a = parse_args(argv, "c06_sqlite_vec")
    store = a.output.parent / "c06_store"
    shutil.rmtree(store, ignore_errors=True)
    store.mkdir(parents=True)
    with stage("load"):
        import sqlite_vec  # noqa: PLC0415

        def connect() -> sqlite3.Connection:
            c = sqlite3.connect(store / "zj.sqlite", isolation_level=None, check_same_thread=False)
            c.enable_load_extension(True)
            sqlite_vec.load(c)
            c.enable_load_extension(False)
            c.execute("PRAGMA cache_size = -1048576")  # 1 GiB page cache
            return c

        con = connect()
        con.execute("PRAGMA journal_mode = WAL")
        con.execute("PRAGMA synchronous = NORMAL")
        con.execute(f"CREATE VIRTUAL TABLE docs USING vec0(i INTEGER PRIMARY KEY, emb float[{DIM}], "
                    "ort TEXT, bezirk TEXT, datum TEXT)")  # fmt: skip
    with stage("ingest"):
        inp = read_inputs(a.input)
        ort = inp.docs.column("ort").to_pylist()
        bez = inp.docs.column("bezirk").to_pylist()
        dat = inp.docs.column("datum").to_pylist()
        con.execute("BEGIN")
        for s, e in batches(inp.n, 10_000):
            v = np.asarray(inp.vectors[s:e], dtype=np.float32)
            con.executemany("INSERT INTO docs(i, emb, ort, bezirk, datum) VALUES (?, ?, ?, ?, ?)", [
                (i, v[i - s].tobytes(), ort[i] or "", bez[i] or "", iso(dat[i]))
                for i in range(s, e)
            ])  # fmt: skip
        con.execute("COMMIT")
    with stage("index"):
        pass  # vec0 is an exact scan: there is no vector index to build (notes)

    def make_search(c: sqlite3.Connection) -> Callable[[Query, np.ndarray], Sequence[int]]:
        def search(q: Query, qv: np.ndarray) -> Sequence[int]:
            w, params = where(q)
            rows = c.execute(
                f"SELECT i FROM docs WHERE emb MATCH ? AND k = {K} {w} ORDER BY distance",
                [qv.astype(np.float32).tobytes(), *params],
            ).fetchall()
            return [int(r[0]) for r in rows]

        return search

    with stage("query"):
        top, lat = run_queries(inp, make_search(con))
    conns: list[sqlite3.Connection] = []

    def per_thread() -> Callable[[Query, np.ndarray], Sequence[int]]:
        conns.append(c := connect())  # one connection per client thread (WAL: concurrent readers)
        return make_search(c)

    with stage("query_c16"):
        top16, wall16 = run_concurrent(inp, per_thread)
    with stage("write"):
        write_output(a.output, top)
        write_output(c16_sidecar(a.output), top16)
    for c in conns:
        c.close()
    con.close()
    disk = dir_bytes(store)
    shutil.rmtree(store, ignore_errors=True)
    result(rows=inp.n, notes="sqlite-vec vec0 exact KNN (L2 on unit vectors), metadata filters in the scan; "
           "no index stage", extra={
        "engine": "cpu", "sqlite": sqlite3.sqlite_version, "sqlite_vec": sqlite_vec.__version__,
        "latency_ms": latency_ms(lat), "disk_bytes": disk, "index": {"type": "flat (exact)", "metric": "l2"},
        "throughput": throughput(lat, top, top16, wall16),
    })  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
