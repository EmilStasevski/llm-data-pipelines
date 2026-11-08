"""Derive the ONNX Runtime models for b04 (CUDA fp16) and b00 (CPU int8) from the repo's fp32 ONNX
export.

`python -m zj_embed.track_b.onnx_prep fp16|int8` → `models/derived/bge-m3/<variant>/model.onnx`
(+ external data).
What an ORT expert does (ORT transformers optimizer docs):
- fp16: `optimize_model(model_type="bert")` fuses attention / LayerNorm / GELU for the CUDA EP, then
  converts to float16 with inputs/outputs kept as int64/float32 (`keep_io_types`).
- int8: the same fusions in fp32 (CPU EP), then dynamic int8 quantisation (`QInt8`, per channel) of the MatMul
  weights except the FFN down-projections, which stay fp32 (mixed precision; see the comment in `prepare`).
Offline preparation, not part of any measured run; the output is cached under models/ (gitignored).
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from zj_embed.track_b.common import MODEL_DIR, MODEL_REVISION, REPO

DERIVED = REPO / "models" / "derived" / "bge-m3"
NUM_HEADS, HIDDEN = 16, 1024


def model_path(variant: str) -> Path:
    return DERIVED / variant / "model.onnx"


def prepare(variant: str) -> dict[str, object]:
    from onnxruntime.transformers.optimizer import optimize_model  # noqa: PLC0415

    t0 = time.perf_counter()
    out = model_path(variant)
    out.parent.mkdir(parents=True, exist_ok=True)
    src = DERIVED / "src" / "model.onnx"
    if not src.exists():  # onnx rejects external data behind symlinks (HF cache) and hard links → plain copy
        shutil.copytree(MODEL_DIR / "onnx", src.parent, dirs_exist_ok=True)
    opt = optimize_model(
        str(src),
        model_type="bert",
        num_heads=NUM_HEADS,
        hidden_size=HIDDEN,
        opt_level=0,
        use_gpu=variant == "fp16",
    )
    fused = {k: v for k, v in opt.get_fused_operator_statistics().items() if v}
    # contestants read token_embeddings (CLS) only: drop the sentence_embedding (Normalize) branch
    keep = [o for o in opt.model.graph.output if o.name == "token_embeddings"]
    del opt.model.graph.output[:]
    opt.model.graph.output.extend(keep)
    opt.prune_graph()
    if variant == "fp16":
        opt.convert_float_to_float16(keep_io_types=True)
        opt.save_model_to_file(str(out), use_external_data_format=True)
    else:
        from onnxruntime.quantization import QuantType, quantize_dynamic  # noqa: PLC0415

        tmp = out.with_name("fp32_fused.onnx")
        opt.save_model_to_file(str(tmp), use_external_data_format=True)
        import onnx  # noqa: PLC0415

        # int8 for every MatMul except the 24 FFN down-projections (XLM-R activation outliers). Mean cosine
        # on 1,000 S documents: default dynamic quantisation (incl. Gather + fused Attention) 0.985, all
        # MatMuls 0.991, this 0.9966 (gate 0.995; ADR 0013)
        graph = onnx.load(str(tmp), load_external_data=False).graph
        ffn_down = [
            n.name
            for n in graph.node
            if n.op_type == "MatMul" and "/output/dense/" in n.name and "attention" not in n.name
        ]
        if len(ffn_down) != 24:
            raise RuntimeError(f"expected 24 FFN down-projection MatMuls, found {len(ffn_down)}")
        quantize_dynamic(
            tmp, out, weight_type=QuantType.QInt8, per_channel=True, use_external_data_format=True,
            op_types_to_quantize=["MatMul"], nodes_to_exclude=ffn_down,
            # fused ORT ops defeat shape inference → declare the default tensor type
            extra_options={"DefaultTensorType": onnx.TensorProto.FLOAT},
        )  # fmt: skip
        for f in out.parent.glob("fp32_fused.onnx*"):
            f.unlink()
    # the fused graph uses ORT ops (LayerNormalization at opset 11) that onnx.checker rejects → load it in ORT
    import onnxruntime as ort  # noqa: PLC0415

    ort.InferenceSession(str(out), providers=["CPUExecutionProvider"])
    info: dict[str, object] = {
        "variant": variant, "onnxruntime": ort.__version__,
        "cpu_nodes_on_cuda_ep": shape_only(out, cuda_cpu_nodes(out)) if variant == "fp16" else None,
        "source": f"BAAI/bge-m3@{MODEL_REVISION}/onnx/model.onnx", "fused_ops": fused,
        "seconds": round(time.perf_counter() - t0, 1),
        "size_gb": round(sum(f.stat().st_size for f in out.parent.iterdir()) / 1e9, 3),
    }  # fmt: skip
    (out.parent / "manifest.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    return info


def cuda_cpu_nodes(model: Path) -> list[str]:
    """Nodes ORT places on the CPU EP in a CUDA session (from ORT's own verbose placement report)."""
    code = (
        "import onnxruntime as ort, sys; ort.preload_dlls(); so = ort.SessionOptions(); "
        "so.log_severity_level = 0; so.log_verbosity_level = 1; "
        "ort.InferenceSession(sys.argv[1], so, providers=['CUDAExecutionProvider'])"
    )
    log = subprocess.run(
        [sys.executable, "-c", code, str(model)], capture_output=True, text=True, check=True
    ).stderr.splitlines()
    nodes: list[str] = []
    on_cpu = False
    for line in log:
        if "Node(s) placed on [" in line or "All nodes placed on [" in line:
            on_cpu = "[CPUExecutionProvider]" in line
            if "All nodes placed on [CPUExecutionProvider]" in line:
                raise RuntimeError("CUDA EP did not load: every node is on the CPU")
        elif on_cpu and "VerifyEachNodeIsAssignedToAnEp]   " in line:
            nodes.append(line.split("VerifyEachNodeIsAssignedToAnEp]", 1)[1].strip())
        elif on_cpu and "VerifyEachNodeIsAssignedToAnEp" not in line:
            on_cpu = False
    return nodes


SHAPE_SOURCES = {"Shape", "Constant", "ConstantOfShape", "Size"}


def shape_only(model: Path, cpu_nodes: list[str]) -> list[str]:
    """Fail unless every CPU-placed node computes on shape metadata only (inputs trace back to Shape or
    Constant).

    Then no tensor data leaves the GPU; ORT places such shape arithmetic on the CPU by design.
    """
    import onnx  # noqa: PLC0415

    g = onnx.load(str(model), load_external_data=False).graph
    producer = {o: n for n in g.node for o in n.output}
    inits = {i.name for i in g.initializer}

    def is_shape(tensor: str, seen: frozenset[str] = frozenset()) -> bool:
        if tensor in inits or tensor == "":
            return True
        n = producer.get(tensor)
        if n is None or n.name in seen:  # graph input (token data) or a cycle
            return False
        if n.op_type in SHAPE_SOURCES:
            return True
        return all(is_shape(i, seen | {n.name}) for i in n.input)

    by_name = {n.name: n for n in g.node}
    for entry in cpu_nodes:
        name = entry.split("(", 1)[1].rstrip(")")
        if not all(is_shape(i) for i in by_name[name].input):
            raise RuntimeError(f"CPU-placed node {entry} computes on tensor data, not only shapes (rule 6)")
    return cpu_nodes


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m zj_embed.track_b.onnx_prep")
    p.add_argument("variant", choices=["fp16", "int8"])
    a = p.parse_args(argv)
    print(json.dumps(prepare(a.variant)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
