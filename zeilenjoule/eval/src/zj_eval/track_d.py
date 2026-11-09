"""Track D inputs + reference (spec/track_d.md): `python -m zj_eval.track_d prepare`.

Writes (all derived from data/synth/d2k + spec/lexicon, deterministic):
- spec/schema_stoermeldung.json                 JSON schema every engine constrains to
- data/track_d/D2k/rows.parquet                  the 2 000 input rows (copy of d2k.parquet)
- data/track_d/D2k/prompts.0shot.jsonl / .3shot.jsonl   {"meldung_id", "messages"} per row, same order
- data/reference/track_d/D2k/gold.jsonl          gold fields per row, same order
- data/track_d/dev/…, data/reference/track_d/dev/gold.jsonl   the 300-row dev set (0-shot only)
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from zj_reference import track_d as ref

REPO = ref.REPO
SCALE = "D2k"


def _rows(path: Path) -> list[dict[str, Any]]:
    return pq.read_table(path).to_pylist()  # pyright: ignore[reportUnknownMemberType]


Shots = list[tuple[dict[str, Any], dict[str, Any]]]
Variant = tuple[Shots, tuple[str, ...]]  # (few-shot examples, fields the model answers)


def _write_scale(repo: Path, scale: str, src: tuple[Path, list[dict[str, Any]], list[dict[str, Any]]],
                 variants: dict[str, Variant]) -> None:  # fmt: skip
    rows_path, rows, gold = src
    if [r["meldung_id"] for r in rows] != [g["meldung_id"] for g in gold]:
        raise ValueError(f"{scale}: rows and gold disagree on row order")
    out = repo / "data" / "track_d" / scale
    out.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(rows_path, out / "rows.parquet")
    for name, (s, fields) in variants.items():
        with (out / f"prompts.{name}.jsonl").open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps({"meldung_id": r["meldung_id"], "messages": ref.messages(r, s, fields)},
                                   ensure_ascii=False) + "\n")  # fmt: skip
    refdir = repo / "data" / "reference" / "track_d" / scale
    refdir.mkdir(parents=True, exist_ok=True)
    with (refdir / "gold.jsonl").open("w", encoding="utf-8") as f:
        for g in gold:
            f.write(json.dumps({"meldung_id": g["meldung_id"], **{k: g[k] for k in ref.FIELDS}},
                               ensure_ascii=False) + "\n")  # fmt: skip


def prepare(repo: Path = REPO) -> dict[str, Any]:
    src = repo / "data" / "synth" / "d2k"
    rows, gold = _rows(src / "d2k.parquet"), _rows(src / "d2k_gold.parquet")
    dev, dev_gold_rows = _rows(src / "dev.parquet"), _rows(src / "dev_gold.parquet")
    dev_gold = {g["meldung_id"]: g for g in dev_gold_rows}
    shot_ids = ref.pick_shots(dev, dev_gold)
    dev_by_id = {r["meldung_id"]: r for r in dev}
    shots = [(dev_by_id[i], dev_gold[i]) for i in shot_ids]
    for name, fields in (("schema_stoermeldung", ref.FIELDS),
                         ("schema_stoermeldung_semantic", ref.SEMANTIC_FIELDS)):  # fmt: skip
        (repo / "spec" / f"{name}.json").write_text(
            json.dumps(ref.schema(fields), ensure_ascii=False, indent=2) + "\n", "utf-8"
        )
    _write_scale(repo, SCALE, (src / "d2k.parquet", rows, gold), {
        "0shot": ([], ref.FIELDS), "3shot": (shots, ref.FIELDS),
        # cascade d11: the LLM answers only the semantic fields
        "semantic3shot": (shots, ref.SEMANTIC_FIELDS),
    })  # fmt: skip
    # dev = concurrency sweep (PLAN §10 step 1); 0-shot only: the three shots come from dev
    _write_scale(repo, "dev", (src / "dev.parquet", dev, dev_gold_rows), {"0shot": ([], ref.FIELDS)})
    return {
        "rows": len(rows),
        "dev_rows": len(dev),
        "shots": shot_ids,
        "system_prompt_chars": len(ref.system_prompt()),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m zj_eval.track_d")
    p.add_argument("cmd", choices=["prepare"])
    p.add_argument("--repo", type=Path, default=REPO)
    a = p.parse_args(argv)
    print(json.dumps(prepare(a.repo), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
