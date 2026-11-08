"""Stage markers (stderr) and result line (stdout) per spec/zjmark.md.

Usage::

    from zjmark import stage, result

    with stage("parse"):
        ...
    result(rows=n, notes="", extra={"threads": 16})
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from typing import Any, TextIO

__all__ = ["canonical_json", "end", "result", "stage", "start"]

_STAGE_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


def canonical_json(obj: Any) -> str:
    """Compact, key-sorted, UTF-8 (no ASCII/HTML escaping) JSON, identical across languages."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _check_stage(name: str) -> None:
    if not _STAGE_RE.fullmatch(name):
        raise ValueError(f"invalid stage name {name!r}: must match {_STAGE_RE.pattern}")


def _emit(line: str, stream: TextIO) -> None:
    stream.write(line + "\n")
    stream.flush()  # the harness timestamps on receipt → never buffer


def start(name: str, stream: TextIO | None = None) -> None:
    _check_stage(name)
    _emit("ZJMARK " + canonical_json({"ev": "start", "stage": name}), stream or sys.stderr)


def end(name: str, stream: TextIO | None = None) -> None:
    _check_stage(name)
    _emit("ZJMARK " + canonical_json({"ev": "end", "stage": name}), stream or sys.stderr)


@contextmanager
def stage(name: str, stream: TextIO | None = None) -> Generator[None]:
    """Emit start/end markers around a block. ``end`` is emitted even if the block raises."""
    start(name, stream)
    try:
        yield
    finally:
        end(name, stream)


def result(
    rows: int, notes: str = "", extra: Mapping[str, Any] | None = None, stream: TextIO | None = None
) -> None:
    """Print the single final ZJRESULT line on stdout."""
    if rows < 0:
        raise ValueError("rows must be >= 0")
    payload = {"extra": dict(extra or {}), "notes": notes, "rows": rows}
    _emit("ZJRESULT " + canonical_json(payload), stream or sys.stdout)
