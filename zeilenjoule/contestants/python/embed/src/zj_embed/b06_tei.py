"""Track B contestant b06_tei: Hugging Face Text Embeddings Inference (Rust server, candle FlashBert, fp16).

Official Blackwell image (`120-1.9.4`, pinned by digest). Model: `models/derived/bge-m3/hf512` (safetensors,
512-token limit → TEI truncates where sentence-transformers does; server_prep). TEI uses GeLU-tanh instead
of exact GeLU (its own warning); measured effect on the cosine gate: negligible (ADR 0015).
Client: 64 texts per `/embed` request, 8 in flight; TEI's token-budget batching does the rest.
"""

from __future__ import annotations

import sys

from zj_embed.track_b.common import REPO
from zj_embed.track_b.served import Client
from zj_embed.track_b.served import main as served_main
from zj_embed.track_b.server import Server, post, uid_gid

PORT = 18086
IMAGE = ("ghcr.io/huggingface/text-embeddings-inference:120-1.9.4"
         "@sha256:bd8e5b1954146f7fe8590b64b959bc194433c6c38c036592a84d736841ca9400")  # fmt: skip
MAX_BATCH_TOKENS = 65536

SERVER = Server(
    name="zj-b06-tei",
    image=IMAGE,
    args=["--model-id", "/model", "--hostname", "127.0.0.1", "--port", str(PORT), "--dtype", "float16",
          "--max-batch-tokens", str(MAX_BATCH_TOKENS), "--max-client-batch-size", "256",
          "--auto-truncate", "true"],
    port=PORT,
    gpu_log=r"Starting \w+ model on Cuda",
    min_vram_mb=1000,  # fp16 weights ≈ 1.1 GiB
    mounts=[(REPO / "models" / "derived" / "bge-m3" / "hf512", "/model")],
    extra_docker=["--user", uid_gid()],
)  # fmt: skip


def request(texts: list[str]) -> list[list[float]]:
    return post(PORT, "/embed", {"inputs": texts, "truncate": True, "normalize": True})


def main(argv: list[str] | None = None) -> int:
    return served_main(
        argv, "b06_tei", SERVER, Client(request, chunk=64, in_flight=8),
        notes="TEI 1.9.4 fp16 (FlashBert, GeLU-tanh), server-side truncation at 512",
        extra={"max_batch_tokens": MAX_BATCH_TOKENS},
    )  # fmt: skip


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
