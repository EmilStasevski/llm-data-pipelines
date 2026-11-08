"""Verify a Track A output against the reference output (spec/track_a.md §9, CLAUDE.md rule 4).

`python -m zj_eval.verify_track_a --output OUT.parquet --reference REF.parquet [--schema SCHEMA.json]`

Checks: exact column names/order/types, sort order by meldung_id, same row set, then every column by
value (float64 |Δ| ≤ 1e-9, everything else exact, null = null). The last stdout line is JSON for the
harness: {"passed": bool, "detail": str, "mismatches": {col: n}, "examples": [...]}. Exit code 0/1.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import duckdb

REPO = Path(__file__).resolve().parents[3]
DUCK_TYPES = {
    "string": "VARCHAR",
    "int8": "TINYINT",
    "int32": "INTEGER",
    "float64": "DOUBLE",
    "bool": "BOOLEAN",
    "date32": "DATE",
}
FLOAT_TOL = 1e-9
MAX_EXAMPLES = 20


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def verify(output: Path, reference: Path, schema_path: Path) -> dict[str, Any]:
    spec = json.loads(schema_path.read_text(encoding="utf-8"))
    want = [(c["name"], DUCK_TYPES[c["type"]]) for c in spec["columns"]]
    con = duckdb.connect()  # all threads: every query below is deterministic (ORDER BY unique keys)
    if not output.exists():
        return {"passed": False, "detail": f"output missing: {output}", "mismatches": {}, "examples": []}
    con.read_parquet(str(output), file_row_number=True).create_view("o")
    con.read_parquet(str(reference)).create_view("r")
    got = [
        (str(n), str(t))
        for n, t, *_ in con.execute("DESCRIBE SELECT * EXCLUDE (file_row_number) FROM o").fetchall()
    ]
    if got != want:
        diff = [f"{i}: got {g} want {w}" for i, (g, w) in enumerate(zip(got, want, strict=False)) if g != w]
        if len(got) != len(want):
            diff.append(f"{len(got)} columns, want {len(want)}")
        return {
            "passed": False,
            "detail": "schema mismatch: " + "; ".join(diff[:10]),
            "mismatches": {},
            "examples": [],
        }

    def one(sql: str) -> Any:
        row = con.execute(sql).fetchone()
        assert row is not None
        return row[0]

    unsorted = one("""
        SELECT count(*) FROM (
            SELECT meldung_id < lag(meldung_id) OVER (ORDER BY file_row_number) AS bad FROM o)
        WHERE bad""")
    dup = one("SELECT count(*) - count(DISTINCT meldung_id) FROM o")
    missing = one("SELECT count(*) FROM r ANTI JOIN o USING (meldung_id)")
    extra = one("SELECT count(*) FROM o ANTI JOIN r USING (meldung_id)")
    problems = {k: v for k, v in {"unsorted": unsorted, "duplicate_ids": dup, "missing_rows": missing,
                                  "extra_rows": extra}.items() if v}  # fmt: skip
    conds: dict[str, str] = {}
    for name, typ in want[1:]:
        c = _q(name)
        if typ == "DOUBLE":
            conds[name] = f"(o.{c} IS NULL) <> (r.{c} IS NULL) OR abs(o.{c} - r.{c}) > {FLOAT_TOL}"
        else:
            conds[name] = f"o.{c} IS DISTINCT FROM r.{c}"
    # one join for all columns (XL: 10M rows; 18 separate joins took ~48 s single-threaded)
    counts_sql = ", ".join(f"count_if({cond})" for cond in conds.values())
    counts = con.execute(f"SELECT {counts_sql} FROM o JOIN r USING (meldung_id)").fetchone()
    assert counts is not None
    mismatches = {name: int(n) for name, n in zip(conds, counts, strict=True) if n}
    examples: list[dict[str, Any]] = []
    for name in mismatches:
        if len(examples) >= MAX_EXAMPLES:
            break
        c = _q(name)
        rows = con.execute(
            f"""SELECT meldung_id, o.{c}::VARCHAR, r.{c}::VARCHAR FROM o JOIN r USING (meldung_id)
                WHERE {conds[name]} ORDER BY meldung_id LIMIT {MAX_EXAMPLES - len(examples)}"""
        ).fetchall()
        examples += [{"meldung_id": m, "column": name, "got": g, "expected": e} for m, g, e in rows]
    passed = not problems and not mismatches
    rows_total = one("SELECT count(*) FROM r")
    if passed:
        detail = f"{rows_total} rows × {len(want)} columns match the reference"
    else:
        parts = [f"{k}={v}" for k, v in problems.items()] + [f"{k}: {v} rows" for k, v in mismatches.items()]
        detail = "mismatch: " + ", ".join(parts)
    return {"passed": passed, "detail": detail, "mismatches": mismatches | problems, "examples": examples}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m zj_eval.verify_track_a")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--reference", type=Path, required=True)
    p.add_argument("--schema", type=Path, default=REPO / "spec" / "track_a.schema.json")
    a = p.parse_args(argv)
    res: dict[str, Any]
    if not a.reference.exists():
        detail = f"reference missing: {a.reference} (task trackA:reference)"
        res = {"passed": False, "detail": detail, "mismatches": {}, "examples": []}
    else:
        res = verify(a.output, a.reference, a.schema)
    for ex in res["examples"][:5]:
        print(
            f"  {ex['meldung_id']} {ex['column']}: got {ex['got']!r} expected {ex['expected']!r}",
            file=sys.stderr,
        )
    print(json.dumps(res, ensure_ascii=False, default=str))
    return 0 if res["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
