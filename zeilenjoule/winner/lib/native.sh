# shellcheck shell=bash disable=SC2153 # BASE/KEY come from load_env (common.sh)
# Native mode: everything lives in $ZJ_HOME (default winner/.run), no sudo, no Docker.
#   PostgreSQL 18 + pgvector from conda-forge (micromamba, a single static binary)
#   Go and Rust toolchains: the ones on PATH, else user-level downloads into $ZJ_HOME
#   zj-gateway, zj-worker (Go) and zj-parse (Rust, Track A parser a16) built from source
# Processes run in the background with pid files in $ZJ_HOME/run and logs in $ZJ_HOME/logs.

# Pinned downloads (checked against the publisher's sha256)
MICROMAMBA_VERSION=2.10.0-0
PG_SPEC="postgresql=18.7 pgvector=0.8.6"
OLLAMA_VERSION="${ZJ_OLLAMA_VERSION:-0.13.5}" # the version the kit was tested with

n_bin() { echo "$ZJ_HOME/bin"; }
n_pg() { "$ZJ_HOME/env/bin/$1" "${@:2}"; }

# fetch URL SHA256_URL DEST: download and verify (the .sha256 file's first field is the hash)
fetch() {
  local url="$1" sumurl="$2" dest="$3" want got
  log "download $url"
  curl -fL --retry 3 -o "$dest.part" "$url"
  # accepts "HASH", "HASH  name", "HASH *name", "HASH  ./name" and "HASH  dir/name"
  want=$(curl -fsSL --retry 3 "$sumurl" | awk -v f="$(basename "$url")" '
    NF == 1 { print $1; exit } { n = split($2, p, "/"); b = p[n]; sub(/^\*/, "", b); if (b == f) { print $1; exit } }')
  got=$(sha256sum "$dest.part" | cut -d' ' -f1)
  [ -n "$want" ] && [ "$want" = "$got" ] || { rm -f "$dest.part"; die "checksum mismatch for $url"; }
  mv "$dest.part" "$dest"
}

arch_check() {
  [ "$(uname -s)" = Linux ] || die "native mode supports Linux only"
  [ "$(uname -m)" = x86_64 ] || die "native mode is tested on x86_64 only (found $(uname -m))"
}

install_postgres() {
  local mm="$ZJ_HOME/bin/micromamba" extra=""
  if [ ! -x "$mm" ]; then
    fetch "https://github.com/mamba-org/micromamba-releases/releases/download/$MICROMAMBA_VERSION/micromamba-linux-64" \
      "https://github.com/mamba-org/micromamba-releases/releases/download/$MICROMAMBA_VERSION/micromamba-linux-64.sha256" "$mm"
    chmod +x "$mm"
  fi
  command -v cc >/dev/null 2>&1 || command -v gcc >/dev/null 2>&1 || extra="c-compiler"
  if [ ! -x "$ZJ_HOME/env/bin/postgres" ] || [ -n "$extra" ]; then
    log "PostgreSQL + pgvector (+ ${extra:-no compiler}) from conda-forge into $ZJ_HOME/env"
    # shellcheck disable=SC2086 # PG_SPEC is a list
    "$mm" create -y -q -r "$ZJ_HOME/mamba" -p "$ZJ_HOME/env" -c conda-forge --override-channels $PG_SPEC $extra
  fi
}

# go_env: PATH/GOTOOLCHAIN so that `go` builds services/ (an older go ≥ 1.21 fetches the go.mod version itself)
go_env() {
  local want have
  want=$(awk '/^go /{ print $2; exit }' "$ROOT/services/go.mod")
  if [ -x "$ZJ_HOME/go/bin/go" ]; then
    export PATH="$ZJ_HOME/go/bin:$PATH"
  elif command -v go >/dev/null 2>&1; then
    have=$(go env GOVERSION 2>/dev/null | sed 's/^go//')
    if [ "$(printf '%s\n%s\n' "1.21" "$have" | sort -V | head -1)" != 1.21 ]; then
      log "go $have is too old for automatic toolchains; installing go $want into $ZJ_HOME/go"
      install_go "$want"
    fi
  else
    install_go "$want"
  fi
  export GOTOOLCHAIN=auto GOPATH="$ZJ_HOME/gopath" GOCACHE="$ZJ_HOME/gocache" GOFLAGS=-modcacherw
}

install_go() {
  local v="$1" tgz="$ZJ_HOME/go$1.tgz"
  fetch "https://dl.google.com/go/go$v.linux-amd64.tar.gz" "https://dl.google.com/go/go$v.linux-amd64.tar.gz.sha256" "$tgz"
  rm -rf "$ZJ_HOME/go" && tar -C "$ZJ_HOME" -xzf "$tgz" && rm -f "$tgz"
  export PATH="$ZJ_HOME/go/bin:$PATH"
}

rust_env() {
  if [ -x "$ZJ_HOME/cargo/bin/cargo" ]; then
    export RUSTUP_HOME="$ZJ_HOME/rustup" CARGO_HOME="$ZJ_HOME/cargo" PATH="$ZJ_HOME/cargo/bin:$PATH"
  elif ! command -v cargo >/dev/null 2>&1 || ! cargo --version | awk '{ split($2, v, "."); exit !(v[1] > 1 || v[2] >= 85) }'; then
    log "installing Rust (stable, minimal) into $ZJ_HOME/cargo (edition 2024 needs ≥ 1.85)"
    local ri="$ZJ_HOME/rustup-init"
    fetch https://static.rust-lang.org/rustup/dist/x86_64-unknown-linux-gnu/rustup-init \
      https://static.rust-lang.org/rustup/dist/x86_64-unknown-linux-gnu/rustup-init.sha256 "$ri"
    chmod +x "$ri"
    RUSTUP_HOME="$ZJ_HOME/rustup" CARGO_HOME="$ZJ_HOME/cargo" "$ri" -y -q --no-modify-path --profile minimal --default-toolchain stable
    rm -f "$ri"
    export RUSTUP_HOME="$ZJ_HOME/rustup" CARGO_HOME="$ZJ_HOME/cargo" PATH="$ZJ_HOME/cargo/bin:$PATH"
  fi
}

build_binaries() {
  go_env
  rust_env
  if ! command -v cc >/dev/null 2>&1 && ! command -v gcc >/dev/null 2>&1; then
    export CC="$ZJ_HOME/env/bin/x86_64-conda-linux-gnu-cc" # conda-forge c-compiler (cgo: pg_query_go)
  fi
  log "building zj-gateway and zj-worker (Go, cgo)"
  (cd "$ROOT/services" && CGO_ENABLED=1 go build -trimpath -o "$ZJ_HOME/bin/" ./cmd/zj-gateway ./cmd/zj-worker)
  log "building zj-parse (Rust parser a16)"
  CARGO_TARGET_DIR="$ZJ_HOME/cargo-target" cargo build -q --release --locked \
    --manifest-path "$ROOT/contestants/rust/Cargo.toml" -p a16-rs-arrow-rayon
  install -m 755 "$ZJ_HOME/cargo-target/release/a16-rs-arrow-rayon" "$ZJ_HOME/bin/zj-parse"
}

install_ollama() {
  local d="$ZJ_HOME/ollama" tgz="$ZJ_HOME/ollama.tgz"
  [ -x "$d/bin/ollama" ] && return 0
  log "Ollama $OLLAMA_VERSION (≈ 2 GB, CUDA libraries included) into $d"
  local base="https://github.com/ollama/ollama/releases/download/v$OLLAMA_VERSION"
  fetch "$base/ollama-linux-amd64.tgz" "$base/sha256sum.txt" "$tgz"
  mkdir -p "$d" && tar -C "$d" -xzf "$tgz" && rm -f "$tgz"
}

pg_running() { n_pg pg_ctl -D "$ZJ_HOME/pgdata" status >/dev/null 2>&1; }

pg_start() {
  pg_running && return 0
  n_pg pg_ctl -D "$ZJ_HOME/pgdata" -l "$ZJ_HOME/logs/postgres.log" -w -t 60 start >/dev/null ||
    die "PostgreSQL did not start, see $ZJ_HOME/logs/postgres.log"
}

psql_owner() { PGPASSWORD="$POSTGRES_PASSWORD" n_pg psql -v ON_ERROR_STOP=1 -q -h 127.0.0.1 -p "$ZJ_PG_PORT" -U zeilenjoule "$@"; }

init_cluster() {
  [ -f "$ZJ_HOME/pgdata/PG_VERSION" ] && return 0
  local pw="$ZJ_HOME/run/pw"
  (umask 077 && printf '%s' "$POSTGRES_PASSWORD" >"$pw")
  # builtin C.UTF-8 provider: Unicode upper/lower (umlauts) without depending on the server's OS locales
  n_pg initdb -D "$ZJ_HOME/pgdata" -U zeilenjoule --pwfile="$pw" --auth=scram-sha-256 -E UTF8 \
    --locale-provider=builtin --builtin-locale=C.UTF-8 --locale=C >/dev/null
  rm -f "$pw"
  cat >>"$ZJ_HOME/pgdata/postgresql.conf" <<EOF

# ZeilenJoule winner kit (native)
listen_addresses = '127.0.0.1'
port = $ZJ_PG_PORT
unix_socket_directories = ''   # TCP on 127.0.0.1 only: socket paths are limited to 107 bytes
shared_buffers = $ZJ_PG_SHARED_BUFFERS
work_mem = 64MB
maintenance_work_mem = 1GB
max_wal_size = 4GB
EOF
  pg_start
  psql_owner -d postgres -c "CREATE DATABASE zeilenjoule"
  create_roles
}

# same roles as deploy/compose/postgres-init: the router's SQL runs as zj_ask (member of zj_reader: SELECT on
# the view meldungen only), read-only with a 10 s statement timeout
create_roles() {
  psql_owner -d zeilenjoule -v pw="$ZJ_READER_PASSWORD" <<'SQL'
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'zj_reader') THEN CREATE ROLE zj_reader NOLOGIN; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'zj_ask') THEN CREATE ROLE zj_ask LOGIN IN ROLE zj_reader; END IF;
END $$;
ALTER ROLE zj_ask PASSWORD :'pw';
ALTER ROLE zj_ask SET statement_timeout = '10s';
ALTER ROLE zj_ask SET default_transaction_read_only = on;
ALTER ROLE zj_ask SET search_path = public;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SQL
}

# app_env: the variables zj-gateway / zj-worker read (config.go), derived from winner.env
app_env() {
  export ZJ_DATABASE_URL="postgres://zeilenjoule:$POSTGRES_PASSWORD@127.0.0.1:$ZJ_PG_PORT/zeilenjoule"
  export ZJ_READER_URL="postgres://zj_ask:$ZJ_READER_PASSWORD@127.0.0.1:$ZJ_PG_PORT/zeilenjoule"
  export ZJ_UPLOAD_DIR="$ZJ_HOME/uploads" ZJ_PARSER_BIN="$ZJ_HOME/bin/zj-parse" ZJ_LEXICON="$ROOT/spec/track_a_lexicon.json"
  export ZJ_LISTEN="$ZJ_BIND:$ZJ_GATEWAY_PORT"
}

native_install() {
  local with_ollama="${1:-0}"
  arch_check
  need curl tar awk sha256sum
  mkdir -p "$ZJ_HOME"/{bin,run,logs,uploads}
  install_postgres
  build_binaries
  [ "$with_ollama" = 1 ] && install_ollama
  init_cluster
  pg_start
  (app_env && "$ZJ_HOME/bin/zj-worker" migrate >>"$ZJ_HOME/logs/migrate.log" 2>&1) ||
    die "migration failed, see $ZJ_HOME/logs/migrate.log"
  log "installed into $ZJ_HOME (PostgreSQL on 127.0.0.1:$ZJ_PG_PORT)"
}

# ---------------------------------------------------------------- processes

pidfile() { echo "$ZJ_HOME/run/$1.pid"; }
running() { local p; p=$(cat "$(pidfile "$1")" 2>/dev/null) && kill -0 "$p" 2>/dev/null; }

# start_proc NAME CMD…: background process with pid file and log (no-op when already running)
start_proc() {
  local name="$1"
  shift
  running "$name" && return 0
  nohup "$@" >>"$ZJ_HOME/logs/$name.log" 2>&1 &
  echo $! >"$(pidfile "$name")"
  sleep 1
  running "$name" || die "$name exited at once, see $ZJ_HOME/logs/$name.log"
}

stop_proc() {
  local name="$1" p i
  p=$(cat "$(pidfile "$name")" 2>/dev/null) || return 0
  if kill -0 "$p" 2>/dev/null; then
    kill "$p"
    for ((i = 0; i < 30; i++)); do kill -0 "$p" 2>/dev/null || break; sleep 1; done
    kill -0 "$p" 2>/dev/null && kill -9 "$p"
    log "stopped $name"
  fi
  rm -f "$(pidfile "$name")"
}

hostport() { local u="${1#*://}"; echo "${u%%/*}"; }
port_of() { local hp; hp=$(hostport "$1"); echo "${hp##*:}"; }

start_ollama() {
  local bin="${ZJ_OLLAMA_BIN:-}" hp m
  [ -n "$bin" ] || bin=$(command -v ollama 2>/dev/null || echo "$ZJ_HOME/ollama/bin/ollama")
  [ -x "$bin" ] || die "ollama not found: install it, set ZJ_OLLAMA_BIN, or run ./kit setup native --with-ollama"
  hp=$(hostport "$ZJ_LLM_URL")
  if curl -fsS -o /dev/null "$ZJ_LLM_URL/api/version" 2>/dev/null; then
    running ollama || log "reusing the Ollama already running at $ZJ_LLM_URL"
  else
    mkdir -p "${ZJ_OLLAMA_MODELS:-$ZJ_HOME/ollama/models}"
    start_proc ollama env OLLAMA_HOST="$hp" OLLAMA_MODELS="${ZJ_OLLAMA_MODELS:-$ZJ_HOME/ollama/models}" "$bin" serve
    wait_url "$ZJ_LLM_URL/api/version" 60 "Ollama" || die "Ollama did not start, see $ZJ_HOME/logs/ollama.log"
  fi
  for m in "$ZJ_LLM_MODEL" "$ZJ_EMBED_MODEL"; do
    OLLAMA_HOST="$hp" "$bin" show "$m" >/dev/null 2>&1 || { log "ollama pull $m"; OLLAMA_HOST="$hp" "$bin" pull "$m"; }
  done
}

start_llamacpp() {
  local bin="${ZJ_LLAMA_SERVER_BIN:-}"
  [ -x "$bin" ] || die "ZJ_LLAMA_SERVER_BIN must point to llama-server"
  [ -f "${ZJ_LLM_GGUF:-}" ] || die "ZJ_LLM_GGUF must point to the chat model .gguf"
  [ -f "${ZJ_EMBED_GGUF:-}" ] || die "ZJ_EMBED_GGUF must point to the embedding model .gguf"
  # shellcheck disable=SC2086 # *_ARGS are argument lists
  start_proc llama-chat "$bin" -m "$ZJ_LLM_GGUF" --host 127.0.0.1 --port "$(port_of "$ZJ_LLM_URL")" \
    --alias "$ZJ_LLM_MODEL" $ZJ_LLAMA_CHAT_ARGS
  # shellcheck disable=SC2086
  start_proc llama-embed "$bin" -m "$ZJ_EMBED_GGUF" --host 127.0.0.1 --port "$(port_of "$ZJ_EMBED_URL")" \
    --alias "$ZJ_EMBED_MODEL" --embedding $ZJ_LLAMA_EMBED_ARGS
  wait_url "$ZJ_LLM_URL/health" 600 "llama-server (chat)" || die "see $ZJ_HOME/logs/llama-chat.log"
  wait_url "$ZJ_EMBED_URL/health" 300 "llama-server (embeddings)" || die "see $ZJ_HOME/logs/llama-embed.log"
}

native_start() {
  [ -x "$ZJ_HOME/bin/zj-gateway" ] || die "not installed: ./kit setup native"
  mkdir -p "$ZJ_HOME"/{run,logs,uploads}
  pg_start
  case "$ZJ_MODEL_SERVER" in
    ollama) start_ollama ;;
    llamacpp) start_llamacpp ;;
    external | vllm-tei) log "model servers are external: chat $ZJ_LLM_URL, embeddings $ZJ_EMBED_URL" ;;
  esac
  app_env
  start_proc worker env ZJ_METRICS_LISTEN="$ZJ_WORKER_METRICS" "$ZJ_HOME/bin/zj-worker"
  start_proc gateway env ZJ_METRICS_LISTEN="$ZJ_GATEWAY_METRICS" "$ZJ_HOME/bin/zj-gateway"
  if wait_url "$BASE/readyz" 300 "gateway $BASE/readyz"; then
    log "ready: API $BASE (X-API-Key from winner.env), demo page $BASE/demo"
  else
    readyz_report
    die "not ready; ./kit logs gateway, ./kit logs worker"
  fi
}

native_stop() {
  local s
  for s in gateway worker llama-chat llama-embed ollama; do stop_proc "$s"; done
  if pg_running; then n_pg pg_ctl -D "$ZJ_HOME/pgdata" -m fast -w stop >/dev/null && log "stopped postgres"; fi
}

native_status() {
  local s
  for s in postgres ollama llama-chat llama-embed worker gateway; do
    if [ "$s" = postgres ]; then pg_running && echo "postgres     running (127.0.0.1:$ZJ_PG_PORT)" || echo "postgres     stopped"
    elif running "$s"; then printf '%-12s running (pid %s)\n' "$s" "$(cat "$(pidfile "$s")")"
    elif [ -f "$(pidfile "$s")" ]; then printf '%-12s dead (see %s)\n' "$s" "$ZJ_HOME/logs/$s.log"
    fi
  done
  printf 'readyz       '
  readyz_report
}

native_logs() {
  local name="${1:-gateway}"
  tail -n 100 -f "$ZJ_HOME/logs/$name.log"
}

native_reset_db() {
  local was=0
  running gateway && was=1
  stop_proc gateway
  stop_proc worker
  pg_start
  psql_owner -d postgres -c "DROP DATABASE IF EXISTS zeilenjoule WITH (FORCE)" -c "CREATE DATABASE zeilenjoule"
  create_roles
  rm -rf "${ZJ_HOME:?}/uploads" && mkdir -p "$ZJ_HOME/uploads"
  (app_env && "$ZJ_HOME/bin/zj-worker" migrate >>"$ZJ_HOME/logs/migrate.log" 2>&1) || die "migration failed"
  log "database reset (embedding size $ZJ_EMBED_DIM)"
  [ "$was" = 1 ] && native_start
  return 0
}
