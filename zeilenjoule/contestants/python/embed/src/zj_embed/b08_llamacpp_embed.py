"""Track B contestant b08_llamacpp_embed: llama.cpp `llama-server --embedding`, GGUF f16 conversion of bge-m3.

Image `server-cuda-b12169` (pinned by digest); GGUF converted from hf512 with the converter of the same commit
(server_prep gguf). llama-server rejects inputs longer than the slot context instead of truncating, so the
client tokenises with the model's own (HF fast) tokenizer, truncates at 512 like the reference, and sends
token ids. `-dev CUDA0 -ngl 99 --fit off`: all layers on the GPU or a start-up error (rule 6); llama.cpp
keeps only the token-embedding lookup table in host memory by design.
Tuning (ADR 0015, unmeasured probe on the real corpus): flash attention on, 16 slots × 512 tokens,
micro-batch 1024 (larger micro-batches pay for one masked attention over all packed sequences).
"""

from __future__ import annotations

import sys
from typing import Any

from zj_embed.track_b.common import MAX_TOKENS, REPO
from zj_embed.track_b.served import Client
from zj_embed.track_b.served import main as served_main
from zj_embed.track_b.server import Server, post, uid_gid

PORT = 18088
IMAGE = ("ghcr.io/ggml-org/llama.cpp:server-cuda-b12169"
         "@sha256:5bb779a449a18d8f4333e9814e887746342fe555c528f5eabc92b2093e32673a")  # fmt: skip
SLOTS, UBATCH = 16, 1024
HF512 = REPO / "models" / "derived" / "bge-m3" / "hf512"

SERVER = Server(
    name="zj-b08-llamacpp",
    image=IMAGE,
    args=["-m", "/model/bge-m3-f16.gguf", "--embedding", "--pooling", "cls", "-ngl", "99", "-dev", "CUDA0",
          "--fit", "off", "-fa", "on", "-np", str(SLOTS), "-c", str(MAX_TOKENS * SLOTS),
          "-b", str(MAX_TOKENS * SLOTS), "-ub", str(UBATCH), "--host", "127.0.0.1", "--port", str(PORT)],
    port=PORT,
    gpu_log=None,  # device lines need -lv 4, which also logs every request; GPU proof via VRAM
    min_vram_mb=500,  # GPU-resident f16 weights without the token-embedding table ≈ 0.6 GiB
    mounts=[(REPO / "models" / "derived" / "bge-m3" / "gguf", "/model")],
    extra_docker=["--user", uid_gid()],
)  # fmt: skip

_tok: list[Any] = []


def setup() -> None:
    from transformers import AutoTokenizer  # noqa: PLC0415

    _tok.append(AutoTokenizer.from_pretrained(HF512))


def prepare(texts: list[str]) -> list[list[int]]:
    return _tok[0](texts, truncation=True, max_length=MAX_TOKENS, add_special_tokens=True)["input_ids"]


def request(ids: list[list[int]]) -> list[list[float]]:
    r = post(PORT, "/v1/embeddings", {"input": ids})
    return [d["embedding"] for d in sorted(r["data"], key=lambda d: d["index"])]


def main(argv: list[str] | None = None) -> int:
    return served_main(
        argv, "b08_llamacpp_embed", SERVER,
        Client(request, chunk=32, in_flight=SLOTS, prepare=prepare, setup=setup),
        notes="llama.cpp b12169 GGUF f16, CLS, flash attention, client-side token ids (truncation at 512)",
        extra={"slots": SLOTS, "ubatch": UBATCH, "gguf": "models/derived/bge-m3/gguf/bge-m3-f16.gguf"},
    )  # fmt: skip


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
