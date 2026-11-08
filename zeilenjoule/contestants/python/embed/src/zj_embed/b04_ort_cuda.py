"""Track B contestant b04_ort_cuda: ONNX Runtime CUDA EP, fused fp16 graph, shape-only CPU nodes logged."""

from __future__ import annotations

import sys

from zj_embed.track_b import ort_runner
from zj_embed.track_b.onnx_prep import model_path


def main(argv: list[str] | None = None) -> int:
    return ort_runner.main(
        argv, "b04_ort_cuda", model_path("fp16"), "CUDAExecutionProvider", 32768,
        "ORT CUDA EP, fused fp16 (Attention/SkipLayerNorm/BiasGelu), token-budget batches",
    )  # fmt: skip


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
