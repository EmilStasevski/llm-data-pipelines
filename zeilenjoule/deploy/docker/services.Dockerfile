# syntax=docker/dockerfile:1.10
# zj-gateway and zj-worker (Phase 9, ADR 0021). Build from the repo root:
#   docker build -f deploy/docker/services.Dockerfile --target gateway -t zeilenjoule/gateway .
# cgo is required (pg_query_go embeds PostgreSQL's parser), hence distroless/cc (glibc) instead of static.

ARG GO_IMAGE=golang:1.27.4-trixie@sha256:b54b2639de8c1bfe538ec5ffc45f29b1932235ac5f6d63c059853646677112d9
ARG RUST_IMAGE=rust:1.99.1-trixie@sha256:8a4e90c7946b74626488b1446f712c0fe0ed1a596cbd3d871684bc9c5433ae07
ARG RUNTIME_IMAGE=gcr.io/distroless/cc-debian13:nonroot@sha256:cc66d222f0a7ec996b78a99fe76a969bc80b539a5016ad866c14d1949bc6d0ab

FROM ${GO_IMAGE} AS go-build
WORKDIR /src/services
COPY services/go.mod services/go.sum ./
RUN --mount=type=cache,target=/root/go/pkg/mod go mod download
COPY services/ ./
ARG VERSION=dev
RUN --mount=type=cache,target=/root/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    CGO_ENABLED=1 go build -trimpath -ldflags "-s -w -X main.version=${VERSION}" -o /out/ ./cmd/zj-gateway ./cmd/zj-worker \
 && mkdir -p /out/uploads

# Rust parser a16 (Track A winner family; ADR 0021: batch binary called by the worker)
FROM ${RUST_IMAGE} AS rust-build
WORKDIR /src
COPY libs/rust/ libs/rust/
COPY contestants/rust/ contestants/rust/
RUN --mount=type=cache,target=/usr/local/cargo/registry --mount=type=cache,target=/src/contestants/rust/target \
    cargo build --release --locked --manifest-path contestants/rust/Cargo.toml -p a16-rs-arrow-rayon \
 && install -D contestants/rust/target/release/a16-rs-arrow-rayon /out/zj-parse

FROM ${RUNTIME_IMAGE} AS gateway
COPY --from=go-build /out/zj-gateway /usr/local/bin/zj-gateway
# owned by nonroot so a fresh named volume mounted here inherits writable ownership
COPY --from=go-build --chown=65532:65532 /out/uploads /var/lib/zj/uploads
USER 65532:65532
EXPOSE 8080 9090
ENTRYPOINT ["/usr/local/bin/zj-gateway"]

FROM ${RUNTIME_IMAGE} AS worker
COPY --from=go-build /out/zj-worker /usr/local/bin/zj-worker
COPY --from=go-build --chown=65532:65532 /out/uploads /var/lib/zj/uploads
COPY --from=rust-build /out/zj-parse /usr/local/bin/zj-parse
COPY spec/track_a_lexicon.json /etc/zj/track_a_lexicon.json
USER 65532:65532
EXPOSE 9090
ENTRYPOINT ["/usr/local/bin/zj-worker"]
