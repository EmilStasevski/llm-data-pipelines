"""Harness smoke contestant (x00_smoke_py): follows the CLI contract, does (almost) nothing.

Used for harness tests and for measuring harness overhead (PLAN §5.7, `sleep 30` as a contestant).
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from zjmark import result, stage


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m zjmark.smoke")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--rows", type=int, default=0)
    p.add_argument("--sleep", type=float, default=0.0, help="seconds to sleep inside the 'work' stage")
    p.add_argument("--threads", type=int, default=1)
    a = p.parse_args(argv)
    with stage("work"):
        time.sleep(a.sleep)
    with stage("write"):
        a.output.parent.mkdir(parents=True, exist_ok=True)
        a.output.write_text("".join(f"{i}\n" for i in range(a.rows)), encoding="utf-8")
    result(rows=a.rows, notes="smoke", extra={"sleep_s": a.sleep, "threads": a.threads})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
