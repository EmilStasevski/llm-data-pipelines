# syntax=docker/dockerfile:1.10
# zj-energy-exporter = `zj exporter` from the harness (NVML GPU energy + RAPL CPU energy as Prometheus counters).
# NVML is loaded at run time from the NVIDIA container toolkit's injected libnvidia-ml.so (dlopen, no CUDA needed).
ARG GO_IMAGE=golang:1.27.4-trixie@sha256:b54b2639de8c1bfe538ec5ffc45f29b1932235ac5f6d63c059853646677112d9
ARG RUNTIME_IMAGE=gcr.io/distroless/cc-debian13:nonroot@sha256:cc66d222f0a7ec996b78a99fe76a969bc80b539a5016ad866c14d1949bc6d0ab

FROM ${GO_IMAGE} AS build
WORKDIR /src/harness
COPY harness/go.mod harness/go.sum ./
RUN --mount=type=cache,target=/root/go/pkg/mod go mod download
COPY harness/ ./
RUN --mount=type=cache,target=/root/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    CGO_ENABLED=1 CGO_CFLAGS="-O2 -g -Wno-deprecated-declarations" go build -trimpath -ldflags "-s -w" -o /out/zj ./cmd/zj

FROM ${RUNTIME_IMAGE}
COPY --from=build /out/zj /usr/local/bin/zj
# RAPL energy_uj is root-readable only (kernel ≥ 5.10); the container runs as root but read-only, no capabilities
# hadolint ignore=DL3002
USER 0:0
EXPOSE 9877
ENTRYPOINT ["/usr/local/bin/zj", "exporter", "--listen", "0.0.0.0:9877"]
