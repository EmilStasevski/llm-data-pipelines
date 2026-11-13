#!/usr/bin/env bash
# Build the Phase 9 service images (task images, contestant e06 build step). No .env, no secrets needed.
# Usage: scripts/build_images.sh [VERSION]   (default dev)
set -euo pipefail
cd "$(dirname "$0")/.."
tag=${1:-dev}
rev=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)
labels=(--label "org.opencontainers.image.version=$tag" --label "org.opencontainers.image.revision=$rev"
        --label "org.opencontainers.image.source=zeilenjoule")
docker build -q -f deploy/docker/services.Dockerfile --target gateway --build-arg "VERSION=$tag" "${labels[@]}" -t "zeilenjoule/gateway:$tag" .
docker build -q -f deploy/docker/services.Dockerfile --target worker --build-arg "VERSION=$tag" "${labels[@]}" -t "zeilenjoule/worker:$tag" .
docker build -q -f deploy/docker/exporter.Dockerfile "${labels[@]}" -t "zeilenjoule/energy-exporter:$tag" .
docker images --filter "reference=zeilenjoule/*:$tag" --format '{{.Repository}}:{{.Tag}} {{.Size}}'
