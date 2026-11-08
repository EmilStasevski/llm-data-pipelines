"""Offline model preparation for the server contestants (not part of any measured run).

`python -m zj_embed.track_b.server_prep hf512` → `models/derived/bge-m3/hf512/`:
the pinned bge-m3 snapshot with `pytorch_model.bin` converted to `model.safetensors` (TEI loads only
safetensors) and the length limit set to spec/track_b.md's 512 tokens (`max_seq_length` in
sentence_bert_config.json, `model_max_length` in tokenizer_config.json), so a server truncates exactly
where sentence-transformers does. Weights are unchanged (fp32); config.json stays as is.

`python -m zj_embed.track_b.server_prep gguf` → `models/derived/bge-m3/gguf/bge-m3-f16.gguf`: hf512
converted with `convert_hf_to_gguf.py` from the **same llama.cpp commit as the b08 server image**
(`LLAMA_CPP_COMMIT`), in a throw-away uv env with that commit's pinned converter requirements.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from zj_embed.track_b.common import MAX_TOKENS, MODEL_DIR, MODEL_REVISION, REPO

DERIVED = REPO / "models" / "derived" / "bge-m3"
HF512 = DERIVED / "hf512"
COPY = ["config.json", "config_sentence_transformers.json", "modules.json", "sentencepiece.bpe.model",
        "special_tokens_map.json", "tokenizer.json", "1_Pooling/config.json"]  # fmt: skip


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1 << 24), b""):
            h.update(b)
    return h.hexdigest()


def hf512() -> dict[str, object]:
    import torch  # noqa: PLC0415
    from safetensors.torch import save_file  # noqa: PLC0415

    t0 = time.perf_counter()
    HF512.mkdir(parents=True, exist_ok=True)
    for name in COPY:
        dst = HF512 / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(MODEL_DIR / name, dst)  # resolves the HF-cache symlinks (containers mount this dir)
    sbert = json.loads((MODEL_DIR / "sentence_bert_config.json").read_text(encoding="utf-8"))
    sbert["max_seq_length"] = MAX_TOKENS
    (HF512 / "sentence_bert_config.json").write_text(json.dumps(sbert, indent=2) + "\n", encoding="utf-8")
    tok = json.loads((MODEL_DIR / "tokenizer_config.json").read_text(encoding="utf-8"))
    tok["model_max_length"] = MAX_TOKENS
    (HF512 / "tokenizer_config.json").write_text(json.dumps(tok, indent=2) + "\n", encoding="utf-8")
    state = torch.load(MODEL_DIR / "pytorch_model.bin", map_location="cpu", weights_only=True)
    # safetensors refuses shared storage; clone every tensor so each owns its memory
    save_file({k: v.contiguous().clone() for k, v in state.items()}, str(HF512 / "model.safetensors"),
              metadata={"format": "pt"})  # fmt: skip
    (HF512 / "model.safetensors").chmod(0o644)  # containers run as the invoking user, but stay readable
    info: dict[str, object] = {
        "source": f"BAAI/bge-m3@{MODEL_REVISION}", "max_tokens": MAX_TOKENS, "tensors": len(state),
        "model_safetensors_sha256": _sha256(HF512 / "model.safetensors"),
        "seconds": round(time.perf_counter() - t0, 1),
    }  # fmt: skip
    (HF512 / "manifest.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    return info


LLAMA_CPP_COMMIT = "3ed7284f2d9e22b2d1a7d05c1416c00c5a9f0bc7"  # ghcr.io/ggml-org/llama.cpp:server-cuda-b12169
LLAMA_CPP_CLONE = Path.home() / ".cache" / "zeilenjoule" / "llama.cpp"
TORCH_CPU = "https://download.pytorch.org/whl/cpu"  # the converter needs torch for tensor I/O only
# the commit's requirements-convert_hf_to_gguf.txt, except protobuf: upstream pins <5, but the tokenizer
# code path imports google.protobuf.runtime_version (≥ 5.26) → ImportError (docs/COMPAT.md). gguf comes
# from the commit's gguf-py via PYTHONPATH.
CONVERT_PINS = ["torch==2.11.0", "numpy==2.2.6", "sentencepiece==0.2.1", "transformers==4.57.6",
                "protobuf==5.29.5"]  # fmt: skip
GGUF = DERIVED / "gguf" / "bge-m3-f16.gguf"


def gguf() -> dict[str, object]:
    t0 = time.perf_counter()
    if not (HF512 / "model.safetensors").exists():
        raise RuntimeError("run `server_prep hf512` first")
    conv = DERIVED / f"llama.cpp-{LLAMA_CPP_COMMIT[:9]}"
    if not (conv / "convert_hf_to_gguf.py").exists():
        conv.mkdir(parents=True, exist_ok=True)
        archive = subprocess.run(
            ["git", "-C", str(LLAMA_CPP_CLONE), "archive", LLAMA_CPP_COMMIT, "convert_hf_to_gguf.py",
             "conversion", "gguf-py", "requirements"], capture_output=True, check=True,
        ).stdout  # fmt: skip
        subprocess.run(["tar", "-x", "-C", str(conv)], input=archive, check=True)
    GGUF.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PYTHONPATH": str(conv / "gguf-py")}
    subprocess.run(
        ["uv", "run", "--no-project", "--python", "3.12",
         "--extra-index-url", TORCH_CPU,
         *[a for pin in CONVERT_PINS for a in ("--with", pin)], "python",
         str(conv / "convert_hf_to_gguf.py"), str(HF512), "--outtype", "f16", "--outfile", str(GGUF)],
        check=True, env=env,
    )  # fmt: skip
    GGUF.chmod(0o644)
    info: dict[str, object] = {
        "source": f"BAAI/bge-m3@{MODEL_REVISION} via hf512", "llama_cpp_commit": LLAMA_CPP_COMMIT,
        "converter_pins": CONVERT_PINS,
        "outtype": "f16", "sha256": _sha256(GGUF), "size_gb": round(GGUF.stat().st_size / 1e9, 3),
        "seconds": round(time.perf_counter() - t0, 1),
    }  # fmt: skip
    (GGUF.parent / "manifest.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    return info


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m zj_embed.track_b.server_prep")
    p.add_argument("target", choices=["hf512", "gguf"])
    a = p.parse_args(argv)
    print(json.dumps(hf512() if a.target == "hf512" else gguf()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
