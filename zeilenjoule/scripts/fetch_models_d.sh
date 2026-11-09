#!/usr/bin/env bash
# Track D models (PLAN §10, config/models.yaml): pinned repo revisions, only the files the benchmark uses.
# GGUF → models/gguf/<repo-name>/, HF-format (vLLM) → models/vllm/<repo-name>/. Each file < 20 GB (rule 2).
set -euo pipefail
cd "$(dirname "$0")/.."
HF=(uvx --from 'huggingface_hub[hf_xet]==1.1.4' --with 'typer==0.20.0' --with 'click==8.3.1' hf)
gguf() {  # repo revision file...
  local repo=$1 rev=$2; shift 2
  "${HF[@]}" download "$repo" "$@" --revision "$rev" --local-dir "models/gguf/${repo#*/}"
}
vllm() {  # repo revision [include patterns...]
  local repo=$1 rev=$2; shift 2
  local inc=(); for p in "$@"; do inc+=(--include "$p"); done
  "${HF[@]}" download "$repo" --revision "$rev" --local-dir "models/vllm/${repo#*/}" "${inc[@]}"
}
what=${1:-all}
if [[ $what == all || $what == gguf ]]; then
  gguf unsloth/Qwen3.5-9B-GGUF 3885219b6810 Qwen3.5-9B-Q4_K_M.gguf Qwen3.5-9B-Q8_0.gguf
  gguf unsloth/Qwen3.5-4B-GGUF e87f176479d0 Qwen3.5-4B-Q4_K_M.gguf
  gguf ggml-org/gpt-oss-20b-GGUF 15dcfc7e7c60 gpt-oss-20b-MXFP4.gguf
  gguf unsloth/gemma-4-12b-it-GGUF fc034cfff751 gemma-4-12b-it-Q4_K_M.gguf gemma-4-12b-it-Q8_0.gguf
  gguf google/gemma-4-E4B-it-qat-q4_0-gguf 4b4a2c1d584b gemma-4-E4B_q4_0-it.gguf
  gguf mradermacher/EuroLLM-9B-Instruct-2512-GGUF 5138603fccb9 EuroLLM-9B-Instruct-2512.Q8_0.gguf
  gguf mistralai/Ministral-3-14B-Instruct-2512-GGUF 74fac473c433 Ministral-3-14B-Instruct-2512-Q4_K_M.gguf
  gguf QuantFactory/Teuken-7B-instruct-commercial-v0.4-GGUF e9c717be5a56 Teuken-7B-instruct-commercial-v0.4.Q8_0.gguf
fi
if [[ $what == all || $what == vllm ]]; then
  vllm cyankiwi/Qwen3.5-9B-AWQ-4bit e4d2d39cede8
  vllm openai/gpt-oss-20b 6cee5e81ee83 '*.json' 'model-*.safetensors' '*.jinja'  # not original/, metal/
  vllm RedHatAI/Qwen3.5-9B-FP8-dynamic 790f0576d2d7
fi
