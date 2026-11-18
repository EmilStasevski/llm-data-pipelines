# shellcheck shell=bash
# Docker mode: docker/compose.yaml with winner.env; gateway/worker images are built from this source tree.

dc() {
  local extra=()
  [ "${ZJ_DOCKER_GPU:-1}" = 0 ] && extra=(-f "$KIT/docker/no-gpu.yaml")
  docker compose --env-file "$ENV_FILE" -f "$KIT/docker/compose.yaml" "${extra[@]}" "$@"
}

# model URLs as seen from inside the compose network
docker_urls() {
  case "$ZJ_MODEL_SERVER" in
    vllm-tei) ZJ_LLM_URL_IN=http://vllm:8000 ZJ_EMBED_URL_IN=http://tei:8081 ;;
    ollama) ZJ_LLM_URL_IN=http://ollama:11434 ZJ_EMBED_URL_IN=http://ollama:11434 ;;
    *)
      ZJ_LLM_URL_IN=$(printf '%s' "$ZJ_LLM_URL" | sed -E 's#//(127\.0\.0\.1|localhost)([:/]|$)#//host.docker.internal\2#')
      ZJ_EMBED_URL_IN=$(printf '%s' "$ZJ_EMBED_URL" | sed -E 's#//(127\.0\.0\.1|localhost)([:/]|$)#//host.docker.internal\2#')
      ;;
  esac
  export ZJ_LLM_URL_IN ZJ_EMBED_URL_IN
}

docker_profiles() {
  case "$ZJ_MODEL_SERVER" in vllm-tei | ollama) echo "--profile $ZJ_MODEL_SERVER" ;; esac
}

docker_install() {
  need docker curl
  docker compose version >/dev/null 2>&1 || die "docker compose v2 is required"
  docker info >/dev/null 2>&1 || die "cannot talk to Docker (are you in the docker group?) — or use: ./kit setup native"
  mkdir -p "$ZJ_MODELS_DIR"
  docker_urls
  log "building gateway/worker images (Go + Rust, pinned base images)"
  dc build migrate gateway
}

docker_start() {
  docker_urls
  # shellcheck disable=SC2046 # profile flags
  dc $(docker_profiles) up -d
  if [ "$ZJ_MODEL_SERVER" = ollama ]; then
    local i m
    for ((i = 0; i < 60; i++)); do dc exec -T ollama ollama list >/dev/null 2>&1 && break; sleep 2; done
    for m in "$ZJ_LLM_MODEL" "$ZJ_EMBED_MODEL"; do
      dc exec -T ollama ollama show "$m" >/dev/null 2>&1 || { log "ollama pull $m"; dc exec -T ollama ollama pull "$m"; }
    done
  fi
  # first start of vllm-tei downloads ≈ 9 GB of models
  if wait_url "$BASE/readyz" 1800 "gateway $BASE/readyz"; then
    log "ready: API $BASE (X-API-Key from winner.env), demo page $BASE/demo"
  else
    readyz_report
    die "not ready; ./kit logs gateway (or vllm, tei, ollama)"
  fi
}

docker_stop() { docker_urls; dc --profile vllm-tei --profile ollama down; }

docker_status() {
  docker_urls
  dc --profile vllm-tei --profile ollama ps
  printf 'readyz  '
  readyz_report
}

docker_logs() { docker_urls; dc --profile vllm-tei --profile ollama logs -f --tail 100 "$@"; }

docker_reset_db() {
  docker_urls
  dc stop gateway worker
  dc exec -T postgres psql -v ON_ERROR_STOP=1 -q -U zeilenjoule -d postgres \
    -c "DROP DATABASE IF EXISTS zeilenjoule WITH (FORCE)" -c "CREATE DATABASE zeilenjoule"
  dc exec -T postgres psql -v ON_ERROR_STOP=1 -q -U zeilenjoule -d zeilenjoule -c "REVOKE CREATE ON SCHEMA public FROM PUBLIC"
  dc run --rm migrate
  log "database reset (embedding size $ZJ_EMBED_DIM)"
  docker_start
}
