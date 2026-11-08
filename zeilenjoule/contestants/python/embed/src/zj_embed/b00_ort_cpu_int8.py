"""Track B contestant b00_ort_cpu_int8: ONNX Runtime CPU EP, fused graph + int8 weights (CPU baseline)."""

from __future__ import annotations

import sys

from zj_embed.track_b import ort_runner
from zj_embed.track_b.onnx_prep import model_path


def main(argv: list[str] | None = None) -> int:
    return ort_runner.main(
        argv, "b00_ort_cpu_int8", model_path("int8"), "CPUExecutionProvider", 4096,
        "ORT CPU EP, fused + dynamic int8 (per-channel QInt8), token-budget batches",
    )  # fmt: skip


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
