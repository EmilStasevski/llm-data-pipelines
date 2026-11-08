"""Wrap a non-contract command (e.g. stress-ng) so it follows the contestant CLI contract.

`python -m zjmark.wrap --output OUT -- cmd args…` runs cmd inside stage "work", writes
"exit=<code>" to OUT (checked by the x.calib verifier) and returns cmd's exit code.
Used only for calibration load generators (track X), never for benchmark contestants.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from zjmark import result, stage


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m zjmark.wrap")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("cmd", nargs=argparse.REMAINDER, help="-- command [args…]")
    a = p.parse_args(argv)
    cmd = a.cmd[1:] if a.cmd[:1] == ["--"] else a.cmd
    if not cmd:
        p.error("missing command after --")
    with stage("work"):
        code = subprocess.run(cmd, check=False).returncode  # calibration helper, cmd from contestants.yaml
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(f"exit={code}\n", encoding="utf-8")
    result(rows=0, notes="calibration wrapper", extra={"cmd": cmd, "exit": code})
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
