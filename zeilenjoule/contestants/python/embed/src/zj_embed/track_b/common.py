"""CLI contract, input/output and the pinned model for Track B contestants (spec/track_b.md)."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

REPO = Path(__file__).resolve().parents[6]
MODEL_ID = "BAAI/bge-m3"
MODEL_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
MODEL_DIR = REPO / "models" / "hub" / "models--BAAI--bge-m3" / "snapshots" / MODEL_REVISION
MAX_TOKENS = 512
DIM = 1024


@dataclass(frozen=True)
class Args:
    input: Path
    output: Path
    threads: int
    model: Path
    batch_size: int | None


def parse_args(argv: list[str] | None, prog: str) -> Args:
    p = argparse.ArgumentParser(prog=prog)
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--threads", type=int, default=0)
    p.add_argument("--model", type=Path, default=MODEL_DIR)
    p.add_argument("--batch-size", type=int, default=None, help="override the contestant's batch size")
    a = p.parse_args(argv)
    if not (a.model / "config.json").exists():
        p.error(f"model not found at {a.model} (download {MODEL_ID}@{MODEL_REVISION} into models/hub)")
    return Args(a.input, a.output, a.threads, a.model, a.batch_size)


def read_texts(path: Path) -> list[str]:
    col = pq.read_table(path, columns=["doc_text"]).column("doc_text")
    if col.null_count:
        raise ValueError(f"{path}: {col.null_count} null doc_text values")
    return [str(x) for x in col.to_pylist()]


def write_vectors(path: Path, vecs: np.ndarray, n: int) -> None:
    if vecs.shape != (n, DIM):
        raise ValueError(f"expected shape ({n}, {DIM}), got {vecs.shape}")
    np.save(path, vecs.astype(np.float16, copy=False), allow_pickle=False)
