"""ONNX Runtime runner shared by b04 (CUDA EP fp16) and b00 (CPU EP int8).

Same batching as b03 (token-length sorted, padded-token budget). Rule 6 for the CUDA run: ORT places 4 nodes
of the position-id subgraph on the CPU. `onnx_prep` proves they compute on shape metadata only (no tensor data
leaves the GPU) and records them; `session.disable_cpu_ep_fallback` cannot be used because it also forbids
these shape nodes. Every result carries that node list, and a run with another ORT version than the prepared
one fails.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import numpy as np

from zj_embed.b03_st_fp16_sorted import batches
from zj_embed.track_b.common import DIM, MAX_TOKENS, parse_args, read_texts, write_vectors


def session(model: Path, provider: str, threads: int) -> Any:
    import onnxruntime as ort  # noqa: PLC0415

    so = ort.SessionOptions()
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    if threads > 0:
        so.intra_op_num_threads = threads
    if provider == "CUDAExecutionProvider":
        # CUDA 13 / cuDNN 9 come from the nvidia-* wheels (torch's dependencies), which are not on the loader
        # path; without this the CUDA EP fails to load and the session falls back to CPU (COMPAT.md)
        ort.preload_dlls()
        providers: list[Any] = [("CUDAExecutionProvider", {"device_id": 0})]
    else:
        providers = ["CPUExecutionProvider"]
    s = ort.InferenceSession(str(model), so, providers=providers)
    if s.get_providers()[0] != provider:
        raise RuntimeError(f"requested {provider}, session uses {s.get_providers()}")  # rule 6
    return s


def embed(sess: Any, ids: list[list[int]], pad_id: int, budget: int) -> np.ndarray:
    lengths = np.fromiter((len(x) for x in ids), dtype=np.int64, count=len(ids))
    out = np.empty((len(ids), DIM), dtype=np.float32)
    for idx in batches(lengths, budget):
        width = int(lengths[idx[0]])
        tok = np.full((len(idx), width), pad_id, dtype=np.int64)
        mask = np.zeros((len(idx), width), dtype=np.int64)
        for row, i in enumerate(idx):
            seq = ids[int(i)]
            tok[row, : len(seq)] = seq
            mask[row, : len(seq)] = 1
        (h,) = sess.run(["token_embeddings"], {"input_ids": tok, "attention_mask": mask})
        cls = np.asarray(h[:, 0], dtype=np.float32)
        out[idx] = cls / np.linalg.norm(cls, axis=1, keepdims=True)
    return out


def main(  # noqa: PLR0913, PLR0917 - thin per-contestant wrappers pass their whole config
    argv: list[str] | None, prog: str, model: Path, provider: str, budget: int, notes: str
) -> int:
    import onnxruntime as ort  # noqa: PLC0415
    from transformers import AutoTokenizer  # noqa: PLC0415

    from zjmark import result, stage  # noqa: PLC0415

    a = parse_args(argv, prog)
    budget = a.batch_size or budget
    if not model.exists():
        raise FileNotFoundError(f"{model}: run `python -m zj_embed.track_b.onnx_prep` first")
    manifest = json.loads((model.parent / "manifest.json").read_text(encoding="utf-8"))
    if manifest["onnxruntime"] != ort.__version__:
        raise RuntimeError(
            f"model prepared with ORT {manifest['onnxruntime']}, running {ort.__version__}: re-run prep"
        )
    with stage("load"):
        tokenizer = AutoTokenizer.from_pretrained(a.model)
        sess = session(model, provider, a.threads)
    with stage("read"):
        texts = read_texts(a.input)
    with stage("embed"):
        ids = tokenizer(texts, truncation=True, max_length=MAX_TOKENS, add_special_tokens=True)["input_ids"]
        vecs = embed(sess, ids, int(tokenizer.pad_token_id), budget)
    with stage("write"):
        write_vectors(a.output, vecs, len(texts))
    result(rows=len(texts), notes=notes, extra={
        "engine": "cuda" if provider.startswith("CUDA") else "cpu", "providers": sess.get_providers(),
        "cpu_nodes": manifest.get("cpu_nodes_on_cuda_ep"),
        "cpu_nodes_kind": "shape-only (proved in onnx_prep)",
        "model_file": str(model.parent.name),
        "token_budget": budget, "dim": DIM, "max_tokens": MAX_TOKENS, "onnxruntime": ort.__version__,
        "intra_op_threads": a.threads or os.cpu_count(),
    })  # fmt: skip
    return 0
