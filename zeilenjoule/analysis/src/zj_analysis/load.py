"""Load results/runs.jsonl (source of truth) into results/zj.duckdb (derived, rebuilt every time).

`uv run python -m zj_analysis.load [--repo DIR]`

Tables/views:
- runs     every record; nested fields (energy_j, stages, env …) kept as STRUCT/LIST/JSON
- idle     idle baselines from results/idle.jsonl
  derived: engine = result.extra.engine ("gpu", "gpu+cpu_fallback", …; NULL if not reported, rule 6)
  derived: uses_gpu = `needs_gpu` of the contestant in config/contestants.yaml (TRUE if unknown); the
           report attributes no GPU net energy to runs with uses_gpu = FALSE (ADR 0014)
- ok_runs  measured runs that count (rule 4): status ok, verify passed, not a warm-up, not track X
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, cast

import duckdb
import yaml

OPTIONAL_VARCHAR = (
    "status_detail",
    "signal",
    "output_path",
    "task",
)  # `omitempty` in harness/internal/results

OK_RUNS = """
CREATE OR REPLACE VIEW ok_runs AS
SELECT * FROM runs
WHERE status = 'ok' AND verify.passed AND NOT warmup AND track <> 'X'
"""


def cpu_only_contestants(repo: Path) -> list[str]:
    """Contestant ids declared `needs_gpu: false` in config/contestants.yaml."""
    path = repo / "config" / "contestants.yaml"
    if not path.exists():
        return []
    doc = cast("dict[str, list[dict[str, Any]]]", yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    return [str(c["id"]) for c in doc.get("contestants", []) if c.get("needs_gpu") is False]


def build(repo: Path) -> dict[str, int]:
    """(Re)build zj.duckdb atomically next to runs.jsonl. Returns row counts."""
    res = repo / "results"
    runs, idle = res / "runs.jsonl", res / "idle.jsonl"
    tmp, final = res / "zj.duckdb.tmp", res / "zj.duckdb"
    tmp.unlink(missing_ok=True)
    con = duckdb.connect(str(tmp))
    try:
        for name, path in (("runs", runs), ("idle", idle)):
            if path.exists() and path.stat().st_size > 0:
                # sample_size=-1: infer the schema from all lines, so rare fields are not dropped
                con.execute(
                    f"CREATE TABLE {name} AS SELECT * FROM read_json(?, format='newline_delimited', "
                    "sample_size=-1, union_by_name=true)",
                    [str(path)],
                )
            else:
                con.execute(f"CREATE TABLE {name} (run_id VARCHAR)")  # empty placeholder
        # Go omits empty optional fields (`omitempty`); if no line has them, read_json infers no column.
        # Guarantee a stable schema so reports never depend on whether some run happened to fill a field.
        cols = {
            str(r[0])
            for r in con.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name='runs'"
            ).fetchall()
        }
        if "status" in cols:
            for col in OPTIONAL_VARCHAR:
                con.execute(f"ALTER TABLE runs ADD COLUMN IF NOT EXISTS {col} VARCHAR")
            # result.extra differs per contestant (STRUCT/MAP after inference): read it through JSON
            con.execute("ALTER TABLE runs ADD COLUMN engine VARCHAR")
            if "result" in cols:
                con.execute("UPDATE runs SET engine = json_extract_string(to_json(result), '$.extra.engine')")
            con.execute("ALTER TABLE runs ADD COLUMN uses_gpu BOOLEAN DEFAULT TRUE")
            cpu_only = cpu_only_contestants(repo)
            if cpu_only:
                con.execute("UPDATE runs SET uses_gpu = FALSE WHERE list_contains(?, contestant)", [cpu_only])
            con.execute(OK_RUNS)
        counts = {t: int(con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]) for t in ("runs", "idle")}  # type: ignore[index]
    finally:
        con.close()
    tmp.replace(final)
    return counts


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m zj_analysis.load")
    p.add_argument("--repo", type=Path, default=Path.cwd())
    a = p.parse_args(argv)
    counts = build(a.repo)
    print(f"results/zj.duckdb: runs={counts['runs']} idle={counts['idle']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
