"""Track C contestant c06_sqlite_hybrid (`c.hybrid`): SQLite + sqlite-vec `vec0` + FTS5 `bm25()`, on-disk.

Dense = c06_sqlite_vec's vec0 table (exact KNN with metadata columns, k = 100). Lexical = a contentless FTS5
table (`unicode61`, diacritics kept; FTS5 has no German stemmer, only English `porter`, so none is used),
ranked by FTS5's built-in `bm25()`; the filter joins a plain metadata table (B-tree indexes). The FTS5 query
is the OR of the quoted query words. One SQL statement per query: both CTEs fused by RRF.
"""

from __future__ import annotations

import shutil
import sqlite3
import sys
from collections.abc import Sequence

import numpy as np

from zj_db.c06_sqlite_vec import NO_DATE, iso, where
from zj_db.common import DIM, Query, batches, dir_bytes, latency_ms, parse_args, read_inputs, write_output
from zj_db.hybrid import DEPTH, collect_legs, read_texts, rrf_sql, run_hybrid, words, write_legs


def fts_query(text: str) -> str:
    return " OR ".join('"' + w.replace('"', '""') + '"' for w in words(text))


def meta_where(q: Query) -> tuple[str, list[object]]:
    parts: list[str] = []
    params: list[object] = []
    if q.ort is not None:
        parts.append("m.ort = ?")
        params.append(q.ort)
    if q.bezirk is not None:
        parts.append("m.bezirk = ?")
        params.append(q.bezirk)
    if q.date_from is not None:
        parts.append("m.datum >= ?")
        params.append(q.date_from.isoformat())
    if q.date_to is not None:
        parts.append("m.datum <= ?")
        params.append(q.date_to.isoformat())
    return (" AND ".join(parts) or "1"), params  # NULL datum never satisfies a comparison


def legs_sql(q: Query, qv: np.ndarray, text: str) -> tuple[str, list[object], str, list[object]]:
    w, wp = where(q)  # vec0 metadata filter ('' = missing, see c06_sqlite_vec)
    # MATERIALIZED: the KNN must run as its own vec0 query (vec0 rejects a window's ORDER BY pushed into it)
    dense = (f"WITH knn AS MATERIALIZED (SELECT i, distance FROM docs WHERE emb MATCH ? AND k = {DEPTH} {w}) "
             "SELECT i, row_number() OVER (ORDER BY distance, i) AS r FROM knn")  # fmt: skip
    match = fts_query(text)
    if not match:  # no word at all: FTS5 rejects an empty MATCH
        return dense, [qv.astype(np.float32).tobytes(), *wp], "SELECT 0 AS i, 0 AS r WHERE 0", []
    mw, mp = meta_where(q)
    lexical = (f"SELECT i, row_number() OVER (ORDER BY s, i) AS r FROM ("
               "SELECT fts.rowid AS i, bm25(fts) AS s "
               f"FROM fts JOIN meta m ON m.i = fts.rowid WHERE fts MATCH ? AND {mw} "
               f"ORDER BY s, i LIMIT {DEPTH})")  # fmt: skip
    return dense, [qv.astype(np.float32).tobytes(), *wp], lexical, [match, *mp]


def main(argv: list[str] | None = None) -> int:  # noqa: PLR0915
    from zjmark import result, stage  # noqa: PLC0415

    a = parse_args(argv, "c06_sqlite_hybrid")
    store = a.output.parent / "c06h_store"
    shutil.rmtree(store, ignore_errors=True)
    store.mkdir(parents=True)
    with stage("load"):
        import sqlite_vec  # noqa: PLC0415

        con = sqlite3.connect(store / "zj.sqlite", isolation_level=None)
        con.enable_load_extension(True)
        sqlite_vec.load(con)
        con.enable_load_extension(False)
        con.execute("PRAGMA journal_mode = WAL")
        con.execute("PRAGMA synchronous = NORMAL")
        con.execute("PRAGMA cache_size = -1048576")  # 1 GiB page cache
        con.execute(f"CREATE VIRTUAL TABLE docs USING vec0(i INTEGER PRIMARY KEY, emb float[{DIM}], "
                    "ort TEXT, bezirk TEXT, datum TEXT)")  # fmt: skip
        con.execute("CREATE TABLE meta (i INTEGER PRIMARY KEY, ort TEXT, bezirk TEXT, datum TEXT)")
        con.execute("CREATE VIRTUAL TABLE fts USING fts5(doc_text, content='', "
                    "tokenize = 'unicode61 remove_diacritics 0')")  # fmt: skip
    with stage("ingest"):
        inp = read_inputs(a.input)
        texts = read_texts(a.input, inp)
        ort = inp.docs.column("ort").to_pylist()
        bez = inp.docs.column("bezirk").to_pylist()
        dat = inp.docs.column("datum").to_pylist()
        txt = inp.docs.column("doc_text").to_pylist()
        con.execute("BEGIN")
        for s, e in batches(inp.n, 10_000):
            v = np.asarray(inp.vectors[s:e], dtype=np.float32)
            con.executemany("INSERT INTO docs(i, emb, ort, bezirk, datum) VALUES (?, ?, ?, ?, ?)", [
                (i, v[i - s].tobytes(), ort[i] or "", bez[i] or "", iso(dat[i])) for i in range(s, e)
            ])  # fmt: skip
            con.executemany("INSERT INTO meta(i, ort, bezirk, datum) VALUES (?, ?, ?, ?)",
                            [(i, ort[i], bez[i], iso(dat[i]) or None)
                             for i in range(s, e)])  # fmt: skip
            con.executemany("INSERT INTO fts(rowid, doc_text) VALUES (?, ?)",
                            [(i, txt[i] or "") for i in range(s, e)])  # fmt: skip
        con.execute("COMMIT")
    with stage("index"):
        for col in ("ort", "bezirk", "datum"):
            con.execute(f"CREATE INDEX meta_{col} ON meta ({col})")
        con.execute("INSERT INTO fts(fts) VALUES ('optimize')")  # merge FTS5 segments into one b-tree
        con.execute("ANALYZE")

    def search(q: Query, qv: np.ndarray, text: str) -> Sequence[int]:
        dense, dp, lexical, lp = legs_sql(q, qv, text)
        rows = con.execute(f"WITH d AS ({dense}), l AS ({lexical}) {rrf_sql()}", [*dp, *lp]).fetchall()
        return [int(r[0]) for r in rows]

    def legs(q: Query, qv: np.ndarray, text: str) -> tuple[list[int], list[int]]:
        dense, dp, lexical, lp = legs_sql(q, qv, text)
        d = con.execute(f"{dense} ORDER BY r", dp).fetchall()
        lx = con.execute(f"{lexical} ORDER BY r", lp).fetchall()
        return [int(r[0]) for r in d], [int(r[0]) for r in lx]

    with stage("query"):
        top, lat = run_hybrid(inp, texts, search)
    with stage("write"):
        write_output(a.output, top)
    with stage("legs"):
        leg_info = write_legs(a.output, *collect_legs(inp, texts, legs))
    con.close()
    disk = dir_bytes(store)
    shutil.rmtree(store, ignore_errors=True)
    result(rows=inp.n, notes="sqlite-vec exact KNN + FTS5 bm25() (unicode61, no stemmer), RRF in one SQL "
           "statement", extra={
        "engine": "cpu", "sqlite": sqlite3.sqlite_version, "sqlite_vec": sqlite_vec.__version__,
        "latency_ms": latency_ms(lat), "disk_bytes": disk, "legs": leg_info, "no_date": NO_DATE,
        "index": {"type": "flat (exact) + fts5(bm25)", "tokenizer": "unicode61 remove_diacritics 0"},
    })  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
