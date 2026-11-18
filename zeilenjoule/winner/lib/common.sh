# shellcheck shell=bash
# Shared helpers of ./kit: configuration, setup, the small HTTP client, packing.

log() { printf '\033[1m[kit]\033[0m %s\n' "$*" >&2; }
die() { printf '\033[31m[kit] error:\033[0m %s\n' "$*" >&2; exit 1; }
need() { for c in "$@"; do command -v "$c" >/dev/null 2>&1 || die "'$c' is required but not installed"; done; }

# secret: 48 hex characters (safe inside URLs and env files)
secret() { od -An -tx1 -N24 /dev/urandom | tr -d ' \n'; }

# set_kv KEY VALUE FILE: replace the KEY= line (inline comment dropped) or append it
set_kv() {
  K="$1" V="$2" awk 'BEGIN { k = ENVIRON["K"]; v = ENVIRON["V"]; done = 0 }
    index($0, k "=") == 1 { print k "=" v; done = 1; next } { print }
    END { if (!done) print k "=" v }' "$3" >"$3.tmp" && mv "$3.tmp" "$3"
}

# get_kv KEY FILE: the raw value (as written, quotes included) or empty
get_kv() { awk -v k="$1" 'index($0, k "=") == 1 { sub(/^[^=]*=/, ""); sub(/[ \t]+#.*$/, ""); print; exit }' "$2"; }

apply_preset() {
  local f="$KIT/presets/$1.env" line p names=""
  if [ ! -f "$f" ]; then
    for p in "$KIT"/presets/*.env; do names="$names $(basename "$p" .env)"; done
    die "unknown preset '$1' (available:$names)"
  fi
  while IFS= read -r line; do
    case "$line" in [A-Z]*=*) set_kv "${line%%=*}" "${line#*=}" "$ENV_FILE" ;; esac
  done <"$f"
}

setup() {
  local mode="${1:-}" preset="" with_ollama=0
  [ $# -gt 0 ] && shift
  case "$mode" in native | docker) ;; *) die "usage: ./kit setup native|docker [--preset NAME] [--with-ollama]" ;; esac
  while [ $# -gt 0 ]; do
    case "$1" in
      --preset) preset="${2:?--preset needs a name}"; shift 2 ;;
      --preset=*) preset="${1#*=}"; shift ;;
      --with-ollama) with_ollama=1; shift ;;
      *) die "unknown option $1" ;;
    esac
  done
  if [ -f "$ENV_FILE" ]; then
    log "keeping $ENV_FILE (delete it to start from the example again)"
  else
    [ -n "$preset" ] || preset=$([ "$mode" = docker ] && echo vllm-tei || echo ollama)
    cp "$KIT/winner.env.example" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    log "created $ENV_FILE"
  fi
  if [ -n "$preset" ]; then
    apply_preset "$preset"
    log "applied preset $preset"
  fi
  set_kv ZJ_MODE "$mode" "$ENV_FILE"
  local k
  for k in POSTGRES_PASSWORD ZJ_READER_PASSWORD ZJ_API_KEYS; do
    [ -n "$(get_kv "$k" "$ENV_FILE")" ] || set_kv "$k" "$(secret)" "$ENV_FILE"
  done
  load_env
  case "$mode/$ZJ_MODEL_SERVER" in
    native/vllm-tei) die "preset vllm-tei needs docker mode (TEI has no no-Docker build); use --preset ollama, llamacpp or external" ;;
    docker/llamacpp) die "llamacpp is managed in native mode only; in docker mode run llama-server yourself and use --preset external" ;;
  esac
  "${mode}_install" "$with_ollama"
  log "setup done. Next: ./kit start, then ./example/run.sh"
}

load_env() {
  [ -f "$ENV_FILE" ] || die "$ENV_FILE missing: run ./kit setup native|docker first"
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
  ZJ_HOME="${ZJ_HOME:-$KIT/.run}"
  ZJ_MODELS_DIR="${ZJ_MODELS_DIR:-$KIT/models}"
  export ZJ_HOME ZJ_MODELS_DIR
  case "${ZJ_MODE:-}" in native | docker) ;; *) die "ZJ_MODE must be native or docker in $ENV_FILE" ;; esac
  case "${ZJ_MODEL_SERVER:-}" in vllm-tei | ollama | llamacpp | external) ;;
    *) die "ZJ_MODEL_SERVER must be vllm-tei, ollama, llamacpp or external" ;; esac
  for k in POSTGRES_PASSWORD ZJ_READER_PASSWORD ZJ_API_KEYS ZJ_LLM_MODEL ZJ_EMBED_DIM; do
    [ -n "${!k:-}" ] || die "$k is empty in $ENV_FILE"
  done
  [ "$ZJ_EMBED_API" = tei ] || [ -n "${ZJ_EMBED_MODEL:-}" ] || die "ZJ_EMBED_MODEL is empty in $ENV_FILE"
  local host="$ZJ_BIND"
  [ "$host" = 0.0.0.0 ] && host=127.0.0.1
  BASE="http://$host:$ZJ_GATEWAY_PORT"
  KEY="${ZJ_API_KEYS%%,*}"
}

# wait_url URL SECONDS [WHAT]: until GET URL answers 2xx
wait_url() {
  local url="$1" n="$2" what="${3:-$1}" i
  printf '[kit] waiting for %s ' "$what" >&2
  for ((i = 0; i < n; i += 2)); do
    if curl -fsS -o /dev/null "$url" 2>/dev/null; then echo ok >&2; return 0; fi
    printf . >&2
    sleep 2
  done
  echo " timeout" >&2
  return 1
}

# readyz_report: why /readyz is not ready (database, llm, tei) — printed by start/status
readyz_report() { curl -sS "$BASE/readyz" 2>&1 | head -c 600; echo; }

reset_db() {
  if [ "${1:-}" != --yes ]; then
    printf 'Delete ALL ingested rows, jobs and the trained classifier? [y/N] ' >&2
    local a
    read -r a
    case "$a" in y | Y | yes) ;; *) die "aborted" ;; esac
  fi
  "${ZJ_MODE}_reset_db"
}

# ---------------------------------------------------------------- client (needs curl + python3)

api() { curl -fsS -H "X-API-Key: $KEY" "$@"; }

client_ingest() {
  local csv="${1:?usage: ./kit ingest FILE.csv}" job state
  [ -f "$csv" ] || die "$csv not found"
  need curl python3
  job=$(api -X POST -H 'Content-Type: text/csv' --data-binary "@$csv" "$BASE/v1/ingest" |
    python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
  log "ingest job $job: $csv"
  while :; do
    state=$(api "$BASE/v1/jobs/$job" | python3 -c 'import json,sys; j=json.load(sys.stdin); print(j["state"], j.get("rows", 0), j.get("error") or "")')
    case "$state" in
      done*) log "job $job done: ${state#done } rows"; return 0 ;;
      failed*) die "job $job failed: ${state#failed }" ;;
    esac
    sleep 2
  done
}

client_ask() {
  local q="${1:?usage: ./kit ask \"Frage …\"}"
  need curl python3
  python3 -c 'import json,sys; print(json.dumps({"question": sys.argv[1]}))' "$q" |
    api -N -X POST -H 'Content-Type: application/json' -H 'Accept: text/event-stream' --data-binary @- "$BASE/v1/ask" |
    python3 -u -c '
import json, sys, time
t0, event = time.time(), ""
for line in sys.stdin:
    line = line.rstrip("\n")
    if line.startswith("event: "):
        event = line[7:]
    elif line.startswith("data: "):
        d = json.loads(line[6:])
        ms = (time.time() - t0) * 1000
        if event == "route":
            print("%7.0f ms  route : %s" % (ms, d["route"]))
        elif event == "plan":
            print("%7.0f ms  plan  : %s" % (ms, d.get("sql") or d.get("suchtext")))
        elif event == "answer":
            print("%7.0f ms  answer: %s" % (ms, json.dumps(d.get("answer"), ensure_ascii=False)[:600]))
            for h in (d.get("cited_rows") or [])[:5]:
                print("            %s  %-12s %s" % (h["meldung_id"], h.get("werk") or "", (h.get("meldungstext") or "")[:70]))
        elif event == "error":
            print("error:", json.dumps(d, ensure_ascii=False))
'
}

client_search() {
  local q="${1:?usage: ./kit search \"Suchtext …\"}"
  need curl python3
  python3 -c 'import json,sys; print(json.dumps({"query": sys.argv[1], "k": 10}))' "$q" |
    api -X POST -H 'Content-Type: application/json' --data-binary @- "$BASE/v1/search" |
    python3 -c '
import json, sys
for h in json.load(sys.stdin).get("hits", []):
    print("%s  %-12s %s" % (h["meldung_id"], h.get("werk") or "", (h.get("meldungstext") or "")[:80]))'
}

# ---------------------------------------------------------------- pack

pack() {
  need tar
  local out="$KIT/dist/zeilenjoule-winner.tar.gz"
  mkdir -p "$KIT/dist"
  tar -C "$ROOT" -czf "$out" \
    --exclude='winner/.run' --exclude='winner/dist' --exclude='winner/models' --exclude='winner/winner*.env' \
    --exclude='services/bin' --exclude='target' --exclude='__pycache__' \
    --transform 's,^,zeilenjoule-winner/,' \
    winner services contestants/rust libs/rust spec/track_a_lexicon.json deploy/docker/services.Dockerfile .dockerignore
  log "wrote $out ($(du -h "$out" | cut -f1)). On the server: tar xzf $(basename "$out") && cd zeilenjoule-winner/winner && ./kit setup native"
}
