"""Track B contestant b07_vllm_pooling: vLLM pooling runner (OpenAI-compatible `/v1/embeddings`), fp16.

vLLM v0.33.0 (pinned by digest), `--runner pooling`; pooling config comes from the sentence-transformers files
(vLLM logs `pooling_type=CLS(source=sentence_transformers)`). `--max-model-len 512` + per-request
`truncate_prompt_tokens: 512` reproduce the reference truncation. torch.compile / CUDA-graph caches persist
in `models/cache/vllm` (warm-up run fills them; the load stage of measured runs then reads them).
Client: 256 texts per request, 8 in flight.
"""

from __future__ import annotations

import sys

from zj_embed.track_b.common import MAX_TOKENS, REPO
from zj_embed.track_b.served import Client
from zj_embed.track_b.served import main as served_main
from zj_embed.track_b.server import Server, post, uid_gid

PORT = 18087
IMAGE = "vllm/vllm-openai:v0.33.0@sha256:d825baf9ce4b1b6bfc5ecc7b79880c7e6748c65a72d697ce3d47607aafa84c6e"

SERVER = Server(
    name="zj-b07-vllm",
    image=IMAGE,
    args=["--model", "/model", "--served-model-name", "bge-m3", "--runner", "pooling", "--dtype", "float16",
          "--max-model-len", str(MAX_TOKENS), "--host", "127.0.0.1", "--port", str(PORT),
          "--gpu-memory-utilization", "0.5"],
    port=PORT,
    gpu_log=r"pooling_type=CLS",  # the CLS pooler must be active (spec); GPU proof via VRAM
    min_vram_mb=1000,
    mounts=[(REPO / "models" / "derived" / "bge-m3" / "hf512", "/model")],
    rw_mounts=[(REPO / "models" / "cache" / "vllm", "/cache")],
    env={"HOME": "/cache", "HF_HUB_OFFLINE": "1", "VLLM_NO_USAGE_STATS": "1", "DO_NOT_TRACK": "1"},
    extra_docker=["--user", uid_gid(), "--ipc=host"],
)  # fmt: skip


def request(texts: list[str]) -> list[list[float]]:
    r = post(
        PORT, "/v1/embeddings", {"model": "bge-m3", "input": texts, "truncate_prompt_tokens": MAX_TOKENS}
    )
    return [d["embedding"] for d in sorted(r["data"], key=lambda d: d["index"])]


def main(argv: list[str] | None = None) -> int:
    return served_main(
        argv, "b07_vllm_pooling", SERVER, Client(request, chunk=256, in_flight=8),
        notes="vLLM 0.33.0 pooling runner fp16, CLS from sentence-transformers config, truncation at 512",
        extra={},
    )  # fmt: skip


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
