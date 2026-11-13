"""Prompts for the Phase 9 Go services: `python -m zj_eval.service_prompts`.

Writes services/internal/prompts/prompts.json from the same builders as the benchmark (Track E router/answer/
label, Track D 3-shot extraction), so production sends the bytes that were measured; only the router names
PostgreSQL instead of DuckDB (ADR 0022: no transpiler in Go).
The file is committed: the services build from a clean checkout without data/. The extraction shots come from
data/synth/d2k (dev split, same three rows as Track D) and are synthetic, no personal data.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from zj_eval import track_e
from zj_reference import track_d as td

REPO = track_e.REPO
OUT = REPO / "services" / "internal" / "prompts" / "prompts.json"


def extract_set(repo: Path) -> dict[str, Any]:
    src = repo / "data" / "synth" / "d2k"
    dev: list[dict[str, Any]] = pq.read_table(src / "dev.parquet").to_pylist()  # pyright: ignore[reportUnknownMemberType]
    gold_rows: list[dict[str, Any]] = pq.read_table(src / "dev_gold.parquet").to_pylist()  # pyright: ignore[reportUnknownMemberType]
    gold = {g["meldung_id"]: g for g in gold_rows}
    by_id = {r["meldung_id"]: r for r in dev}
    shots: list[dict[str, str]] = []
    for i in td.pick_shots(dev, gold):
        shots += [
            {"role": "user", "content": td.user_message(by_id[i])},
            {"role": "assistant", "content": td.gold_json(gold[i])},
        ]
    return {"system": td.system_prompt(), "shots": shots, "schema": td.schema(), "max_tokens": td.MAX_TOKENS}


def build(repo: Path = REPO) -> dict[str, Any]:
    return {**track_e.prompt_set(repo, "postgres"), "extract": extract_set(repo)}


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(build(), ensure_ascii=False, indent=1) + "\n", "utf-8")
    print(OUT.relative_to(REPO))
    return 0


if __name__ == "__main__":
    raise SystemExit(main() if len(sys.argv) == 1 else 2)
