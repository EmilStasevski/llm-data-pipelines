"""sentence-transformers runner shared by b01 (fp32), b02 (fp16) and the reference."""

from __future__ import annotations

import sys
from typing import Any

import numpy as np

from zj_embed.track_b.common import DIM, MAX_TOKENS, Args, read_texts, write_vectors


def load(args: Args, half: bool) -> Any:
    import torch  # noqa: PLC0415
    from sentence_transformers import SentenceTransformer  # noqa: PLC0415

    if args.threads > 0:
        torch.set_num_threads(args.threads)
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA not available")  # no silent CPU run (rule 6)
    m = SentenceTransformer(
        str(args.model), device="cuda", model_kwargs={"dtype": torch.float16 if half else torch.float32}
    )
    m.max_seq_length = MAX_TOKENS
    return m


def encode(model: Any, texts: list[str], batch_size: int) -> np.ndarray:
    v = model.encode(
        texts,
        batch_size=batch_size,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )
    return np.asarray(v)


def main(argv: list[str] | None, prog: str, half: bool, default_batch: int, notes: str) -> int:
    import torch  # noqa: PLC0415

    from zj_embed.track_b.common import parse_args  # noqa: PLC0415
    from zjmark import result, stage  # noqa: PLC0415

    a = parse_args(argv, prog)
    batch = a.batch_size or default_batch
    with stage("load"):
        model = load(a, half)
        torch.cuda.synchronize()
    with stage("read"):
        texts = read_texts(a.input)
    with stage("embed"):
        vecs = encode(model, texts, batch)
        torch.cuda.synchronize()
    with stage("write"):
        write_vectors(a.output, vecs, len(texts))
    dtype = str(next(model.parameters()).dtype)
    result(rows=len(texts), notes=notes, extra={
        "engine": "cuda", "dtype": dtype, "batch_size": batch, "dim": DIM, "max_tokens": MAX_TOKENS,
        "torch": torch.__version__, "gpu": torch.cuda.get_device_name(0),
        "vram_peak_gb": round(torch.cuda.max_memory_allocated() / 1e9, 3),
    })  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit("use b01_st_fp32 / b02_st_fp16")
