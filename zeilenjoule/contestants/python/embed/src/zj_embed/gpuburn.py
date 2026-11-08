"""Calibration load (x03_gpuburn_torch): fp16 matmul loop on the GPU for a fixed time.

PLAN §5.7: power should approach the 180 W limit; the harness compares the NVML energy counter
with integrated power samples. Writes "exit=0" for the x.calib verifier.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from zjmark import result, stage


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m zj_embed.gpuburn")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--seconds", type=float, default=60.0)
    p.add_argument("--n", type=int, default=8192, help="square matrix size")
    a = p.parse_args(argv)

    import torch  # noqa: PLC0415  (heavy import inside the measured process on purpose)

    if not torch.cuda.is_available():
        print("CUDA not available", file=sys.stderr)
        return 1
    with stage("setup"):
        x = torch.randn(a.n, a.n, device="cuda", dtype=torch.float16)
        y = torch.randn(a.n, a.n, device="cuda", dtype=torch.float16)
        torch.cuda.synchronize()
    iters = 0
    with stage("burn"):
        end = time.monotonic() + a.seconds
        while time.monotonic() < end:
            for _ in range(10):
                _ = x @ y  # the work is the point
            torch.cuda.synchronize()
            iters += 10
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(f"exit=0\niterations={iters}\n", encoding="utf-8")
    result(rows=0, notes="calibration GPU load", extra={"iterations": iters, "n": a.n, "seconds": a.seconds})
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
