"""Track B contestant b03_st_fp16_sorted: the hand-tuned PyTorch path an expert writes around the same model.

- tokenise everything once (fast Rust tokenizer, batched), truncation at 512 tokens like sentence-transformers
- sort by **token** length and pack batches by a padded-token budget (sentence-transformers sorts by character
  length and uses a fixed batch size), so short documents form large batches and padding is minimal
- fp16 weights, SDPA attention, `torch.inference_mode`, pinned host buffers with async copies
- CLS pooling + L2 normalisation in fp32 on the GPU, results restored to input order
"""

from __future__ import annotations

import sys
from typing import Any

import numpy as np

from zj_embed.track_b.common import DIM, MAX_TOKENS, parse_args, read_texts, write_vectors

TOKEN_BUDGET = 32768  # padded tokens per batch (batch_size × longest sequence in the batch)


def batches(lengths: np.ndarray, budget: int) -> list[np.ndarray]:
    """Index batches in descending token length; each batch's rows × max length stays within the budget."""
    order = np.argsort(-lengths, kind="stable")
    out: list[np.ndarray] = []
    start = 0
    while start < len(order):
        longest = int(lengths[order[start]])
        size = max(1, budget // max(longest, 1))
        out.append(order[start : start + size])
        start += size
    return out


def embed(model: Any, ids: list[list[int]], pad_id: int, budget: int) -> np.ndarray:
    import torch  # noqa: PLC0415

    lengths = np.fromiter((len(x) for x in ids), dtype=np.int64, count=len(ids))
    out = torch.empty((len(ids), DIM), dtype=torch.float16, device="cuda")
    with torch.inference_mode():
        for idx in batches(lengths, budget):
            width = int(lengths[idx[0]])
            tok = torch.full((len(idx), width), pad_id, dtype=torch.long).pin_memory()
            mask = torch.zeros((len(idx), width), dtype=torch.long).pin_memory()
            for row, i in enumerate(idx):
                seq = ids[int(i)]
                tok[row, : len(seq)] = torch.as_tensor(seq)
                mask[row, : len(seq)] = 1
            h = model(
                input_ids=tok.to("cuda", non_blocking=True), attention_mask=mask.to("cuda", non_blocking=True)
            ).last_hidden_state[:, 0]
            v = torch.nn.functional.normalize(h.float(), dim=-1)
            out[torch.as_tensor(idx, device="cuda")] = v.half()
    return out.cpu().numpy()


def main(argv: list[str] | None = None) -> int:
    import torch  # noqa: PLC0415
    from transformers import AutoModel, AutoTokenizer  # noqa: PLC0415

    from zjmark import result, stage  # noqa: PLC0415

    a = parse_args(argv, "b03_st_fp16_sorted")
    budget = a.batch_size or TOKEN_BUDGET
    if a.threads > 0:
        torch.set_num_threads(a.threads)
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA not available")  # rule 6
    with stage("load"):
        tokenizer = AutoTokenizer.from_pretrained(a.model)
        model = (
            AutoModel.from_pretrained(a.model, dtype=torch.float16, attn_implementation="sdpa").cuda().eval()
        )
        torch.cuda.synchronize()
    with stage("read"):
        texts = read_texts(a.input)
    with stage("embed"):
        ids = tokenizer(texts, truncation=True, max_length=MAX_TOKENS, add_special_tokens=True)["input_ids"]
        vecs = embed(model, ids, int(tokenizer.pad_token_id), budget)
        torch.cuda.synchronize()
    with stage("write"):
        write_vectors(a.output, vecs, len(texts))
    result(rows=len(texts), notes="token-length sorted, token-budget batches, fp16 SDPA", extra={
        "engine": "cuda", "dtype": "float16", "token_budget": budget, "dim": DIM, "max_tokens": MAX_TOKENS,
        "attn": "sdpa", "torch": torch.__version__, "gpu": torch.cuda.get_device_name(0),
        "vram_peak_gb": round(torch.cuda.max_memory_allocated() / 1e9, 3),
    })  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
