"""Shared CLI contract, lexicon loading and output schema for Track A contestants."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pyarrow as pa

SCHEMA = pa.schema(
    [
        pa.field("meldung_id", pa.string()),
        pa.field("werk", pa.string()),
        pa.field("anlage", pa.string()),
        pa.field("equipment_nr", pa.string()),
        pa.field("datum", pa.date32()),
        pa.field("schicht", pa.string()),
        pa.field("prioritaet", pa.int8()),
        pa.field("stillstand_min", pa.int32()),
        pa.field("kosten_eur", pa.float64()),
        pa.field("status", pa.string()),
        pa.field("text_norm", pa.string()),
        pa.field("text_lower", pa.string()),
        pa.field("text_fold", pa.string()),
        pa.field("materialnr", pa.string()),
        pa.field("sicherheitsrelevant_rule", pa.bool_()),
        pa.field("komponente_rule", pa.string()),
        pa.field("regierungsbezirk", pa.string()),
        pa.field("nuts2", pa.string()),
        pa.field("doc_text", pa.string()),
    ]
)
COLUMNS = SCHEMA.names
INPUT_COLUMNS = (
    "meldung_id", "werk", "anlage", "equipment_nr", "meldungsdatum", "schicht",
    "prioritaet", "meldungstext", "stillstand", "kosten", "status",
)  # fmt: skip


@dataclass(frozen=True)
class Args:
    input: Path
    output: Path
    threads: int
    lexicon: Path


def parse_args(argv: list[str] | None, prog: str) -> Args:
    p = argparse.ArgumentParser(prog=prog)
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--threads", type=int, default=0)
    p.add_argument("--lexicon", type=Path, default=Path("spec/track_a_lexicon.json"))
    a = p.parse_args(argv)
    return Args(a.input, a.output, a.threads, a.lexicon)


def load_lexicon(path: Path) -> dict[str, Any]:
    return cast("dict[str, Any]", json.loads(path.read_text(encoding="utf-8")))
