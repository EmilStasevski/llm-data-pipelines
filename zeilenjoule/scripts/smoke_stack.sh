#!/usr/bin/env bash
# Smoke test of a running production stack (`task up`, then `task smoke`): health, auth, ingest of a small CSV,
# job polling, search, ask (JSON + SSE), extract. Unmeasured: proves the wiring, not speed or energy.
# Reads ZJ_API_KEYS from .env (first key). Usage: scripts/smoke_stack.sh <csv> [base-url]
set -euo pipefail
CSV=${1:?usage: smoke_stack.sh <csv> [base-url]}
BASE=${2:-http://127.0.0.1:8080}
ENV_FILE=${ZJ_ENV_FILE:-.env}
KEY=$(grep -E '^ZJ_API_KEYS=' "$ENV_FILE" | cut -d= -f2- | cut -d, -f1)
[ -n "$KEY" ] || { echo "no ZJ_API_KEYS in $ENV_FILE" >&2; exit 1; }
H=(-H "X-API-Key: $KEY")
fail() { echo "FAIL: $*" >&2; exit 1; }
step() { printf '\n== %s\n' "$*"; }

step "health"
curl -fsS "$BASE/healthz" >/dev/null || fail healthz
curl -fsS "$BASE/readyz" || fail "readyz (are TEI and vLLM up?)"
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/v1/ask" -d '{"question":"x"}')
[ "$code" = 401 ] || fail "request without API key returned $code, want 401"

step "ingest $(wc -l < "$CSV") lines from $CSV"
job=$(curl -fsS "${H[@]}" -H 'Content-Type: text/csv' --data-binary @"$CSV" "$BASE/v1/ingest")
id=$(echo "$job" | python3 -c 'import sys,json; print(json.load(sys.stdin)["id"])')
echo "job $id"
for _ in $(seq 1800); do
  j=$(curl -fsS "${H[@]}" "$BASE/v1/jobs/$id")
  state=$(echo "$j" | python3 -c 'import sys,json; print(json.load(sys.stdin)["state"])')
  [ "$state" = done ] && break
  [ "$state" = failed ] && fail "job failed: $j"
  sleep 2
done
echo "$j"

step "search"
curl -fsS "${H[@]}" -H 'Content-Type: application/json' "$BASE/v1/search" \
  -d '{"query":"Getriebe läuft heiß","k":3}' | python3 -m json.tool | head -30

step "ask (JSON)"
curl -fsS "${H[@]}" -H 'Content-Type: application/json' "$BASE/v1/ask" \
  -d '{"question":"Wie viele Störmeldungen gab es je Regierungsbezirk?"}' | python3 -m json.tool | head -30

step "ask (SSE)"
curl -fsSN "${H[@]}" -H 'Content-Type: application/json' -H 'Accept: text/event-stream' "$BASE/v1/ask" \
  -d '{"question":"Welche Meldungen gibt es zu Leckagen an Kreiselpumpen?"}' | head -c 1500; echo

step "extract"
curl -fsS "${H[@]}" -H 'Content-Type: application/json' "$BASE/v1/extract" \
  -d '{"rows":[{"anlage":"Walzstraße 2","equipment_nr":"P-4711","meldungstext":"Gleitringdichtung an Kreiselpumpe undicht, Dichtung getauscht","stillstand":"45 min"}]}' \
  | python3 -m json.tool
echo; echo "smoke test passed"
