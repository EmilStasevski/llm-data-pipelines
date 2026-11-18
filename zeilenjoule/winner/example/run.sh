#!/usr/bin/env bash
# Example: load 100 synthetic Störmeldungen into the running stack, ask 9 German questions and check every answer
# against fragen.json (computed from the generator's gold data by derive_expected.py).
#   ./example/run.sh            (stack started with ./kit start)
#   ./example/run.sh --no-ingest   ask only
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KIT="$(cd "$HERE/.." && pwd)"
[ "${1:-}" = --no-ingest ] || "$KIT/kit" ingest "$HERE/stoermeldungen_100.cp1252.csv"
# BASE/KEY the same way ./kit computes them
export KIT
eval "$(
  ENV_FILE="${ZJ_ENV_FILE:-$KIT/winner.env}" bash -c '
    . "$KIT/lib/common.sh"; load_env; printf "export ZJ_BASE=%q ZJ_KEY=%q\n" "$BASE" "$KEY"'
)"
exec python3 "$HERE/check.py" "$HERE/fragen.json"
