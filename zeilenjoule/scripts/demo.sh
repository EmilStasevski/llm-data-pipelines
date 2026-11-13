#!/usr/bin/env bash
# task demo (PLAN §13): stack is up (task up) → ingest the S sample → ask three German questions over SSE →
# show J/question from GET /v1/energy and the demo page URL. Unmeasured; for a live walk-through.
set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="${DEMO_ENV:-.env}"   # DEMO_ENV: another env file (e.g. a test stack)
[ -f "$ENV_FILE" ] || { echo "demo: $ENV_FILE missing (cp .env.example .env)" >&2; exit 1; }
set -a; . "$ENV_FILE"; set +a
KEY="${ZJ_API_KEYS%%,*}"
BASE="http://127.0.0.1:${ZJ_GATEWAY_PORT:-8080}"
CSV="${DEMO_CSV:-data/synth/stoermeldungen/S/stoermeldungen.cp1252.csv}"
[ -f "$CSV" ] || { echo "demo: $CSV missing (task data:gen SCALE=S)" >&2; exit 1; }
api() { curl -fsS -H "X-API-Key: $KEY" "$@"; }

printf 'Warte auf %s/readyz ' "$BASE"
for _ in $(seq 1 300); do
  curl -fsS -o /dev/null "$BASE/readyz" 2>/dev/null && break
  printf '.'; sleep 2
done
curl -fsS -o /dev/null "$BASE/readyz" || { echo " nicht bereit" >&2; exit 1; }
echo " ok"

echo "Ingest $CSV …"
job=$(api -X POST -H 'Content-Type: text/csv' --data-binary "@$CSV" "$BASE/v1/ingest" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
while :; do
  state=$(api "$BASE/v1/jobs/$job" | python3 -c 'import json,sys; j=json.load(sys.stdin); print(j["state"], j.get("rows", 0))')
  case "$state" in done*) echo "  Job $job: $state Zeilen"; break ;; failed*) echo "  Job $job: $state" >&2; exit 1 ;; esac
  sleep 2
done

for q in "Wie hoch waren die Gesamtkosten in Euro für Störungen mit Schadensart Leckage in Köln im Jahr 2025?" \
         "Welche Meldungen gibt es zu „Lager läuft heiß“ in Dortmund im 2. Quartal 2025?" \
         "Welches Werk hatte 2024 die höchsten Stillstandskosten?"; do
  echo; echo "Frage: $q"
  body=$(python3 -c 'import json,sys; print(json.dumps({"question": sys.argv[1]}))' "$q")
  api -N -X POST -H 'Content-Type: application/json' -H 'Accept: text/event-stream' -d "$body" "$BASE/v1/ask" |
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
            print("  %6.0f ms  Route: %s" % (ms, d["route"]))
        elif event == "plan":
            print("  %6.0f ms  Plan: %s" % (ms, d.get("sql") or d.get("suchtext")))
        elif event == "answer":
            a = d.get("answer")
            if d.get("cited_rows"):
                a = "; ".join("%s %s: %s" % (h["meldung_id"], h.get("werk", ""), h.get("meldungstext", "")[:60]) for h in d["cited_rows"][:3])
            print("  %6.0f ms  Antwort: %s" % (ms, json.dumps(a, ensure_ascii=False)[:300]))
        elif event == "error":
            print("  Fehler:", d)
'
done

echo; sleep 20  # Prometheus scrapes every 15 s; the 1-min rates need two samples
api "$BASE/v1/energy" | python3 -c '
import json, sys
e = json.load(sys.stdin)
f = lambda v, u: "nicht gemessen" if v is None else f"{v:.0f} {u}"
print("Energie (Prometheus, 1-min-Mittel, brutto):", f(e["joules_per_ask"], "J/Frage"), "·", f(e["watts"], "W"), "(" + e["source"] + ")")'
echo "Demo-Seite: $BASE/demo   Grafana: http://127.0.0.1:3000"
