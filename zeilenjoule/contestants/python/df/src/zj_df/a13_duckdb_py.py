"""Track A `a.full` contestant a13_duckdb_py: DuckDB from Python, the whole pipeline in SQL
(read_csv with windows-1252 → macros → COPY … TO parquet)."""

from __future__ import annotations

import sys

import duckdb

from zj_df.track_a import duckdb_impl as impl
from zj_df.track_a.common import load_lexicon, parse_args
from zjmark import result, stage


def main(argv: list[str] | None = None) -> int:
    a = parse_args(argv, "a13_duckdb_py")
    lex = load_lexicon(a.lexicon)
    con = duckdb.connect()
    if a.threads > 0:
        con.execute(f"SET threads = {a.threads}")
    con.execute("LOAD encodings")  # installed in the build step (no network during the measured run)
    for sql in impl.macros(lex) + impl.tables(lex):
        con.execute(sql)
    read_sql, transform_sql, komp_sql, copy_sql = impl.pipeline(str(a.input), str(a.output))
    with stage("read"):
        con.execute(read_sql)
    with stage("transform"):
        con.execute(transform_sql)
        con.execute(komp_sql)
    with stage("write"):
        con.execute(copy_sql)
    rows = con.execute("SELECT count(*) FROM zj_in").fetchone()
    threads = con.execute("SELECT current_setting('threads')").fetchone()
    result(rows=int(rows[0]) if rows else 0, notes="duckdb sql",
           extra={"threads": int(threads[0]) if threads else 0, "duckdb": duckdb.__version__})  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
