# ZeilenJoule – Betriebshandbuch (Produktions-Stack, Phase 9)

Architektur: `docs/ARCHITEKTUR.md`, Entscheidungen: ADR 0021 (Stack), 0022 (SQL-Prüfung), 0023 (Betrieb).

## 1. Voraussetzungen
- Docker + Compose ≥ 2.30, NVIDIA Container Toolkit (Profil `gpu`), ≈ 14 GB freier GPU-Speicher.
- Modelle unter `models/` (wie im Benchmark): `models/derived/bge-m3/hf512` (TEI), `models/vllm/Qwen3.5-9B-AWQ-4bit`
  (vLLM), beschreibbares `models/cache/vllm`. Beschaffung: `task trackB:serverprep` (bge-m3 hf512), `task trackD:models`.
- `.env` aus `.env.example`: `POSTGRES_PASSWORD`, `ZJ_READER_PASSWORD`, `ZJ_API_KEYS`, `GRAFANA_ADMIN_PASSWORD`,
  `ZJ_UID`/`ZJ_GID` (= `id -u`/`id -g`). Lange Zufallswerte (`openssl rand -hex 24`).
- **Nie während einer gemessenen Benchmark-Session starten** (CLAUDE.md Regel 7): der Stack belegt die GPU.

## 2. Starten, prüfen, stoppen
| Befehl | Wirkung |
|---|---|
| `task images` | Images bauen (gateway, worker inkl. Rust-Parser, energy-exporter); `VERSION=…` taggt |
| `task up` | Postgres → Migration → Gateway/Worker, TEI → vLLM, Prometheus/Grafana/Exporter; wartet auf „healthy“ |
| `task up PROFILES=gpu` | ohne Beobachtung; `PROFILES=` (leer) ohne Modellserver (externe per `ZJ_TEI_URL`/`ZJ_LLM_URL`) |
| `task smoke` | Ende-zu-Ende: Health, 401 ohne Schlüssel, Ingest S (10 000 Zeilen), Suche, Frage (JSON + SSE), Extraktion |
| `task logs S=worker` | Logs (JSON) folgen |
| `task down` | stoppen, Volumes bleiben; `task down:volumes` löscht Datenbank, Uploads, Prometheus, Grafana (fragt nach) |

Erreichbar (nur 127.0.0.1): API `:8080` (Spezifikation `GET /v1/openapi.json`), Grafana `:3000` (admin /
`GRAFANA_ADMIN_PASSWORD`, Dashboard „ZeilenJoule – Produktion“), Prometheus `:9091`.

## 3. API in Kürze (Header `X-API-Key`)
```bash
curl -H "X-API-Key: $KEY" -H 'Content-Type: text/csv' --data-binary @stoermeldungen.cp1252.csv localhost:8080/v1/ingest
curl -H "X-API-Key: $KEY" localhost:8080/v1/jobs/1
curl -H "X-API-Key: $KEY" -d '{"query":"Getriebe läuft heiß","filters":{"werk":"Dortmund"},"k":10}' localhost:8080/v1/search
curl -H "X-API-Key: $KEY" -H 'Accept: text/event-stream' -d '{"question":"Wie viele Leckagen gab es 2025 in Köln?"}' localhost:8080/v1/ask
```
`/v1/ask` liefert `route` (sql/search, `none` = Router-Antwort unbrauchbar), die ausgeführte SQL mit `sql_status`
(executed/rejected/failed – Grund im Gateway-Log), die Antwort (Text oder Tabelle: `columns` + Zeilen als Arrays),
zitierte `meldung_id`s und `estimated = true`, wenn die Antwort auf der geschätzten Spalte `schadensart` beruht.

## 4. Ingest-Ablauf und Schadensart
Upload → Datei im Volume `uploads` + Zeile in `ingest_jobs` + River-Job (eine Transaktion) → Worker: Rust-Parser →
Parquet → TEI (4 × 128 Texte parallel) → Schadensart → `COPY` in Staging → Upsert in `docs` (HNSW + GIN wachsen mit).
Schadensart: Beim **ersten** Import ohne Klassifikator etikettiert das LLM eine Stichprobe von 2 000 Zeilen und ein
Softmax-Klassifikator wird trainiert und in `classifier` gespeichert (Holdout-Genauigkeit im Log); spätere Importe
nutzen ihn. LLM-Etiketten haben Vorrang vor Klassifikator-Schätzungen (`schadensart_quelle`). Neu trainieren:
`DELETE FROM classifier;` und erneut importieren. Kleine Uploads (< 2 000 Zeilen) ohne Klassifikator: alle Zeilen per LLM.

## 5. Störungen
| Symptom | Prüfen / Abhilfe |
|---|---|
| `/readyz` 503 | Detail nennt den Dienst; `task logs S=vllm` – vLLM braucht 1–3 min zum Laden |
| vLLM startet nicht (KV-Cache/OOM) | anderer GPU-Prozess aktiv? `nvidia-smi`; Benchmark-Container (`zj-*`) beenden |
| TEI-429 in Worker-Logs | normal unter Last; der Client wiederholt mit Backoff. Dauerhaft: `ZJ_EMBED_IN_FLIGHT` senken |
| Job `failed` | `GET /v1/jobs/{id}` → `error`; Parserfehler sind endgültig (kaputte CSV), sonst 3 Versuche |
| Router-SQL abgelehnt/fehlgeschlagen | Gateway-Log `router sql not used` + Grund; Alarm `ZJRouterSQLFailing` (> 10 %) |
| CPU-Energie fehlt im Dashboard | Exporter-Log; Docker maskiert `/sys/devices/virtual/powercap` → Mount im Compose prüfen |

## 6. Sicherung
`docker compose … exec postgres pg_dump -U zeilenjoule -Fc zeilenjoule > backup.dump`; Wiederherstellung mit
`pg_restore`. Die Vektoren sind aus den Rohdaten reproduzierbar (Neu-Import), die LLM-Etiketten nur mit LLM-Kosten.

## 7. Kubernetes (k3s + Helm, ADR 0024)
Chart `deploy/helm/zeilenjoule` (Namespace `zeilenjoule`, PodSecurity „restricted“), Monitoring in
`zeilenjoule-monitoring`. Compose und k3s nicht gleichzeitig starten – beide wollen die GPU.

| Schritt | Befehl |
|---|---|
| einmalig, clusterweit: Namespaces + GPU-Time-Slicing (2 × `nvidia.com/gpu`) | `task k3s:cluster-setup` |
| Images bauen, in Registry `127.0.0.1:5000` schieben, Secrets aus `.env`, Helm-Install, warten | `task images && task k3s:up` (`MONITORING=true` für Prometheus/Grafana/Exporter) |
| prüfen | `task k3s:test` (helm test), `task k3s:smoke` (kompletter Smoke-Test, Port-Forward 18080) |
| API / Grafana lokal | `kubectl -n zeilenjoule port-forward svc/gateway 8080:8080`, `kubectl -n zeilenjoule-monitoring port-forward svc/grafana 3000:3000` |
| entfernen | `task k3s:down` (PVCs von Postgres/Backups und das Modell-PV bleiben) |

- **Sicherung:** CronJob `postgres-backup` (03:17 UTC, `pg_dump -Fc`, 7 Stände im PVC `backups`). Sofort:
  `kubectl -n zeilenjoule create job --from=cronjob/postgres-backup backup-now`. Wiederherstellung:
  `kubectl -n zeilenjoule exec -i postgres-0 -- pg_restore -U zeilenjoule -d zeilenjoule --clean < backup.dump`.
- **Schlüssel rotieren:** `.env` ändern → `task k3s:secrets` → `kubectl -n zeilenjoule rollout restart deploy/gateway`.
  Das Passwort von `zj_ask` steht zusätzlich in der Datenbank: vorher `ALTER ROLE zj_ask PASSWORD '…'` (als Eigentümer).
- **GPU-OOM / vLLM startet nicht:** läuft der Compose-Stack oder ein Benchmark-Container? `nvidia-smi`. Time-Slicing
  teilt keinen Speicher; `llm.gpuMemoryUtilization` (0.70) gilt wie in Compose.
- **Rücknahme des Time-Slicings:** `kubectl apply` des ursprünglichen statischen NVIDIA-Manifests v0.17.0
  (ohne `--config-file`) und `kubectl -n kube-system delete cm nvidia-device-plugin-config`.
- **Pods hängen in `Pending`:** `kubectl describe pod` – meist `nvidia.com/gpu` (cluster-setup fehlt) oder das
  Modell-PV (`models.hostPath` falsch; `task k3s:up` setzt `<repo>/models`).

## 8. Lastkurve (PLAN §12.3, ADR 0026)
Gemessen nur über das Harness (CLAUDE.md Regel 7), nie neben einem laufenden Stack:
`scripts/quiet_box.sh measure run --id e07_stack_load --scale L --reps 3 --warmup 0` (≈ 45 min je Lauf).
Ungemessener Kurztest: `services/bin/zj-load --input data/synth/stoermeldungen/M/stoermeldungen.cp1252.csv
--output /tmp/out.json --search-rates 1,20 --ask-rates 1 --step 20s --ask-step 20s` (Stack wird gestartet und
wieder abgebaut, Projekt `zeilenjoule-bench`). Auswertung (Knie, SLOs, J/Anfrage): `task report`.

## English summary
`task up` starts PostgreSQL + pgvector, migrations, the Go gateway and worker, TEI and vLLM on one GPU, and
Prometheus/Grafana with the NVML/RAPL energy exporter (all bound to 127.0.0.1). `task smoke` checks the whole path
(health, auth, ingest of 10 000 rows, search, ask as JSON and SSE, extraction). The first ingest trains the
schadensart classifier from 2 000 LLM labels; later ingests reuse it, LLM labels always win. Never run the stack during
a measured benchmark session. Kubernetes (§7): `task k3s:cluster-setup` once (namespaces with Pod Security labels,
GPU time-slicing), then `task images && task k3s:up`, `task k3s:test`, `task k3s:smoke`.
