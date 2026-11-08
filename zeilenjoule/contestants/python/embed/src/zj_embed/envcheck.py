"""Version fingerprint of the `embed` env; with --gpu also checks torch sm_120 + ORT CUDA provider."""

from __future__ import annotations

import sys

from zjmark import canonical_json
from zjmark.envinfo import Fingerprint, fingerprint

DISTS = [
    "torch",
    "sentence-transformers",
    "transformers",
    "onnxruntime-gpu",
    "onnx",
    "nvidia-ml-py",
    "nvidia-cudnn-cu13",
    "numpy",
    "zjmark",
]


def _gpu_probe() -> dict[str, object]:
    import onnxruntime as ort  # noqa: PLC0415  (heavy, GPU-only imports)
    import torch  # noqa: PLC0415

    if not torch.cuda.is_available():
        raise RuntimeError("torch: CUDA not available")
    cap = torch.cuda.get_device_capability(0)
    providers = ort.get_available_providers()
    if "CUDAExecutionProvider" not in providers:
        raise RuntimeError(f"onnxruntime: CUDAExecutionProvider missing, have {providers}")
    return {
        "device": torch.cuda.get_device_name(0),
        "capability": f"{cap[0]}.{cap[1]}",
        "torch_cuda": torch.version.cuda,
        "sm_120_in_arch_list": "sm_120" in torch.cuda.get_arch_list(),
        "ort_providers": providers,
    }


def check(gpu: bool = False) -> Fingerprint:
    return fingerprint(DISTS, {"env": "embed", **({"gpu": _gpu_probe()} if gpu else {})})


if __name__ == "__main__":
    print(canonical_json(check("--gpu" in sys.argv)))
