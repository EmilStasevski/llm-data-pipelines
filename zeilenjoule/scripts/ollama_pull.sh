#!/usr/bin/env bash
# Pull Ollama registry models into models/ollama with the pinned Ollama image (no host install touched).
# Usage: scripts/ollama_pull.sh qwen3.5:9b [more tags…]. Prints `ollama list` (model IDs = manifest digests).
set -euo pipefail
cd "$(dirname "$0")/.."
IMAGE=$(sed -n 's/^OLLAMA_IMAGE = "\(.*\)".*/\1/p' contestants/python/llm-clients/src/zj_llm/extract.py)
mkdir -p models/ollama
docker rm -f zj-ollama-pull >/dev/null 2>&1 || true
docker run -d --name zj-ollama-pull --network host -e OLLAMA_HOST=127.0.0.1:18199 \
  -v "$PWD/models/ollama:/root/.ollama" "$IMAGE" >/dev/null
trap 'docker rm -f zj-ollama-pull >/dev/null' EXIT
for _ in $(seq 60); do docker exec zj-ollama-pull ollama list >/dev/null 2>&1 && break; sleep 1; done
for tag in "$@"; do docker exec zj-ollama-pull ollama pull "$tag"; done
docker exec zj-ollama-pull ollama list
