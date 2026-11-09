"""Version fingerprint of the `ner` env; with --gpu also checks that torch sees the sm_120 GPU."""

from __future__ import annotations

import sys

from zjmark import canonical_json
from zjmark.envinfo import Fingerprint, fingerprint

DISTS = ["torch", "transformers", "gliner", "nvidia-ml-py", "pyarrow", "zjmark"]


def check(gpu: bool = False) -> Fingerprint:
    extra: dict[str, object] = {"env": "ner"}
    if gpu:
        import torch  # noqa: PLC0415

        if not torch.cuda.is_available():
            raise RuntimeError("torch: CUDA not available")
        extra["cuda_capability"] = list(torch.cuda.get_device_capability(0))
    return fingerprint(DISTS, extra)


if __name__ == "__main__":
    print(canonical_json(check("--gpu" in sys.argv)))
