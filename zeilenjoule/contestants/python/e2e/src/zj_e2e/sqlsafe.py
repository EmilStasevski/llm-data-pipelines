"""Router SQL safety (spec/track_e.md §3, PLAN §11): parse with sqlglot, allow only one read-only SELECT
on the view `meldungen`, then transpile from DuckDB SQL (what the router writes) to the store's dialect.

The check is structural: the statement must be a SELECT (UNION of SELECTs allowed), every table reference must
be `meldungen` (CTE names are allowed as references), and no node may be DDL/DML/command, a file/table
function or one of the side-effect or file functions below. The store executes the result read-only with a
time limit as a second line of defence.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp

VIEW = "meldungen"
FORBIDDEN = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.Command,
    exp.Copy,
    exp.Set,
    exp.Pragma,
    exp.Use,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
    exp.Into,
    exp.TruncateTable,
    exp.Describe,
    exp.LoadData,
    exp.Attach,
    exp.Detach,
    exp.Install,
)
FORBIDDEN_FUNCS = {
    "read_csv",
    "read_csv_auto",
    "read_parquet",
    "read_json",
    "read_json_auto",
    "read_text",
    "read_blob",
    "glob",
    "sniff_csv",
    "pg_read_file",
    "pg_read_binary_file",
    "pg_ls_dir",
    "pg_sleep",
    "lo_import",
    "lo_export",
    "dblink",
    "load_extension",
    "writefile",
    "readfile",
    "query",
    "query_table",
    "getenv",
    "current_setting",
    "set_config",
}


@dataclass(frozen=True)
class Checked:
    ok: bool
    sql: str  # transpiled to the target dialect when ok
    reason: str = ""


def check(sql: str, dialect: str = "duckdb") -> Checked:  # noqa: PLR0911  (one early return per rule)
    try:
        stmts = [s for s in sqlglot.parse(sql, read="duckdb") if s is not None]
    except sqlglot.errors.ParseError as e:
        return Checked(False, "", f"parse error: {str(e)[:200]}")
    if len(stmts) != 1:
        return Checked(False, "", f"{len(stmts)} statements")
    tree = stmts[0]
    if not isinstance(tree, (exp.Select, exp.Union)):
        return Checked(False, "", f"not a SELECT ({type(tree).__name__})")
    if bad := next(iter(tree.find_all(*FORBIDDEN)), None):
        return Checked(False, "", f"forbidden node {type(bad).__name__}")
    ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    for t in tree.find_all(exp.Table):
        if t.args.get("db") or t.args.get("catalog"):
            return Checked(False, "", f"qualified table {t.sql()}")
        if not isinstance(t.this, exp.Identifier) or t.name.lower() not in {VIEW, *ctes}:
            return Checked(False, "", f"table {t.sql()} is not the view {VIEW}")
    for f in tree.find_all(exp.Func):
        name = (f.name if isinstance(f, exp.Anonymous) else f.sql_name()).lower()
        if name in FORBIDDEN_FUNCS:
            return Checked(False, "", f"forbidden function {name}")
    try:
        out = tree.sql(dialect=dialect)
    except sqlglot.errors.UnsupportedError as e:
        return Checked(False, "", f"cannot transpile to {dialect}: {e}")
    return Checked(True, out)
