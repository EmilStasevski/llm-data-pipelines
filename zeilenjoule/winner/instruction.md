# ZeilenJoule winner kit — instructions

*Deutsch: [instruction.de.md](instruction.de.md)*

This folder installs the **benchmark winner** as a working service. The winner is the fastest and most
energy-efficient pipeline on L, 1 M rows: `e03_postgres_only`, measured as the Go stack `e06_stack_go`.

- **Database:** one PostgreSQL with pgvector (HNSW) and German full-text search, fused with RRF.
  - No separate vector store.
- **Services:**
  - A Go gateway with the HTTP API, API keys and a demo page.
  - A Go worker for ingest: the Rust CSV parser, embeddings, and the damage-type classifier.
- **Router LLM:** for each question it decides between checked, read-only SQL and hybrid search.

It runs with **Docker** or **without Docker and without sudo**, and with **any local model**.

```
        CSV ──► worker ──► Rust parser ──► embeddings ──► PostgreSQL (pgvector + German full text)
                                                               ▲
question ──► gateway ──► router LLM ──► SQL (read-only role) ──┤
                                    └─► hybrid search ─────────┘
```

Contents:
1. [Quick start](#1-quick-start)
2. [Requirements](#2-requirements)
3. [Using the stack](#3-using-the-stack)
4. [Using any local model](#4-using-any-local-model)
5. [Server without sudo](#5-server-without-sudo)
6. [Docker mode details](#6-docker-mode-details)
7. [Operations](#7-operations)
8. [Example (100 rows)](#8-example-100-rows)
9. [Troubleshooting](#9-troubleshooting)
10. [Limits](#10-limits)

## 1. Quick start

**Without Docker, using Ollama** (GPU or CPU):
```bash
cd winner
./kit setup native --preset ollama   # PostgreSQL + pgvector, builds Go/Rust binaries (~3 min), no sudo
./kit start                          # starts Ollama if it is not running, pulls qwen3:8b + bge-m3
./example/run.sh                     # loads 100 rows, asks 9 questions, checks the answers
```

**With Docker, using the measured models** (vLLM + TEI; NVIDIA GPU with 16 GB):
```bash
cd winner
./kit setup docker --preset vllm-tei # builds the gateway/worker images
./kit start                          # first start downloads ≈ 9 GB of models
./example/run.sh
```

Then open `http://127.0.0.1:8080/demo` in a browser. The API key is `ZJ_API_KEYS` in `winner/winner.env`.

## 2. Requirements

| | Native (no Docker) | Docker |
|---|---|---|
| OS | Linux x86_64 | Linux with Docker Engine + compose v2 |
| Rights | normal user, **no sudo** | membership in the `docker` group |
| Tools | `curl`, `tar`, `sha256sum`, `python3` (client commands only) | `docker`, `curl`, `python3` |
| Toolchains | Go and Rust are used when present; otherwise downloaded into `winner/.run` | built inside images |
| Disk | ≈ 2 GB plus models | ≈ 6 GB of images plus models |
| GPU | optional; whatever the model server uses | `vllm-tei`: NVIDIA ≥ 16 GB and the NVIDIA Container Toolkit |

Every download is pinned to a version and checked against the publisher's sha256:
- micromamba 2.10.0-0, which installs PostgreSQL 18.7 and pgvector 0.8.6 from conda-forge
- Go (the version in `services/go.mod`) and rustup, only when they are missing
- Ollama 0.13.5, only with `--with-ollama`

## 3. Using the stack

```bash
./kit ingest my_reports.csv                 # upload, wait for the job (re-uploading a meldung_id updates it)
./kit ask "Wie viele Meldungen gab es 2025 im Werk Krefeld?"
./kit search "Lager läuft heiß"             # hybrid search only (top 10)
./kit status                                # processes + /readyz (database, llm, tei)
```

**Input format:** an SAP-PM-like CSV with `;` as separator, encoded as cp1252 or UTF-8. These columns are
expected:

```
meldung_id;werk;anlage;equipment_nr;meldungsdatum;schicht;prioritaet;meldungstext;stillstand;kosten;status
```

The parser handles German number and date formats (`1.223,93 €`, `6.9.24`), times such as `4-5h`, typos and
spelling variants. For an example, see `example/stoermeldungen_100.cp1252.csv`.

**HTTP API:** the OpenAPI spec is at `GET /v1/openapi.json`; `docs/RUNBOOK.md` §3 has the details.
```bash
KEY=$(grep ^ZJ_API_KEYS winner.env | cut -d= -f2 | cut -d, -f1)
curl -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
     -d '{"question":"Welches Werk hatte 2024 die höchsten Stillstandskosten?"}' http://127.0.0.1:8080/v1/ask
```

- With `Accept: text/event-stream`, the answer streams as `route` → `plan` → `answer` → `done`.
- The route comes first, as soon as the router has decided.
- SQL answers carry the executed SQL; search answers carry the cited rows.

## 4. Using any local model

All model settings are in `winner/winner.env`. A **preset** fills them for a common server:

| Preset | Chat server | Embedding server | Modes |
|---|---|---|---|
| `vllm-tei` | vLLM, Qwen3.5-9B AWQ (measured) | TEI, bge-m3 (measured) | docker |
| `ollama` | Ollama, `qwen3:8b` | Ollama, `bge-m3` | native, docker |
| `llamacpp` | `llama-server` + chat GGUF | `llama-server --embedding` + GGUF | native |
| `external` | any OpenAI-compatible server | any `/v1/embeddings` or TEI | native, docker |

To switch presets: `./kit setup native --preset llamacpp`, or edit `winner.env` and run `./kit stop && ./kit start`.

### Chat model (`ZJ_LLM_*`)

The chat model routes each question, writes the SQL, and labels damage types during ingest.

- **Server:** must offer OpenAI `/v1/chat/completions` with `response_format: json_schema`. vLLM, SGLang,
  llama.cpp, Ollama ≥ 0.5 and LM Studio qualify.
- **`ZJ_LLM_URL`** is the base URL **without** `/v1`. **`ZJ_LLM_MODEL`** is the served model name.
- **`ZJ_LLM_EXTRA_JSON`** holds extra request fields. Switch "thinking" off here; otherwise reasoning models spend
  the whole token budget before the JSON and the answer comes back empty.
  - vLLM, llama.cpp, SGLang with Qwen3.x: `'{"chat_template_kwargs":{"enable_thinking":false}}'`
  - Ollama: `'{"reasoning_effort":"none"}'`
  - models without thinking: `'{}'`
- **Size:** prompts are German, and the SQL must use the right columns. Instruction-tuned models from about 7–9 B
  parameters work.
  - In the example, Qwen3.5-9B AWQ, qwen3:8b via Ollama and qwen3:8b via llama.cpp each answered 7–8 of 9 questions
    correctly. Each missed different ones.
  - Smaller models make more SQL mistakes.
- **Optional:** `ZJ_LLM_API_KEY` (sent as a Bearer token) and `ZJ_LLM_IN_FLIGHT` (parallel requests; at most the
  server's parallel slots).

### Embedding model (`ZJ_EMBED_*`)

- **`ZJ_EMBED_API`:** `openai` for `/v1/embeddings` (Ollama, llama.cpp, vLLM, LM Studio) or `tei` for
  Hugging Face TEI's `/embed`.
- **`ZJ_EMBED_DIM`** must equal the model's vector size. It is fixed when the database is created.
  - **After changing the embedding model:** `./kit reset-db`, then ingest again.
  - The services refuse to start on a mismatch, with a message that says so.
- **Prefixes:** some models expect them.

| Model (examples) | `ZJ_EMBED_DIM` | Prefixes (`ZJ_EMBED_QUERY_PREFIX` / `ZJ_EMBED_DOC_PREFIX`) | Note |
|---|---|---|---|
| `bge-m3` (measured) | 1024 | none | multilingual, good German |
| `multilingual-e5-large` | 1024 | `'query: '` / `'passage: '` | multilingual |
| `multilingual-e5-small` | 384 | `'query: '` / `'passage: '` | small and fast; weaker |
| `nomic-embed-text` | 768 | `'search_query: '` / `'search_document: '` | mainly English |

Vectors are L2-normalised by the stack, so cosine and inner product agree.

### Recipes

**Another Ollama model:** set `ZJ_LLM_MODEL=mistral-small3.2` (or any tag from `ollama list`) and run
`./kit stop && ./kit start`. The kit pulls missing models.

**llama.cpp without sudo:** build `llama-server` yourself; prebuilt Linux CUDA binaries do not exist.
```bash
git clone https://github.com/ggml-org/llama.cpp && cd llama.cpp
cmake -B build -DGGML_CUDA=ON && cmake --build build -j --target llama-server   # CPU only: drop -DGGML_CUDA=ON
```
Then set `ZJ_LLAMA_SERVER_BIN=…/build/bin/llama-server`, `ZJ_LLM_GGUF=…` and `ZJ_EMBED_GGUF=…`.
- Extra server arguments go in `ZJ_LLAMA_CHAT_ARGS` and `ZJ_LLAMA_EMBED_ARGS`.
- bge-m3 needs `--pooling cls`; most other embedding models need `--pooling mean`.

**vLLM without Docker:** `uv venv && uv pip install vllm`, run `vllm serve <model> --port 8000`, and use the
`external` preset. The embedding server can be Ollama or a second vLLM started with `--runner pooling`.

## 5. Server without sudo

```bash
# on your machine (repository checkout)
./winner/kit pack                                    # → winner/dist/zeilenjoule-winner.tar.gz (≈ 200 KB)
scp winner/dist/zeilenjoule-winner.tar.gz server:
# on the server
tar xzf zeilenjoule-winner.tar.gz && cd zeilenjoule-winner/winner
./kit setup native --preset ollama --with-ollama     # --with-ollama: user-level Ollama (≈ 2 GB) if none exists
./kit start
```

- **Where things live:** everything is under `winner/.run`: PostgreSQL data, binaries, logs and pid files.
  - Set `ZJ_HOME` to put it elsewhere, for example on a larger disk.
- **Ports:**
  - PostgreSQL listens only on `127.0.0.1:55432` (`ZJ_PG_PORT`), with no Unix socket.
  - The gateway listens on `ZJ_BIND:ZJ_GATEWAY_PORT`, by default `127.0.0.1:8080`.
- **Access from other machines:** set `ZJ_BIND=0.0.0.0` and put a TLS reverse proxy in front, or use an SSH tunnel:
  `ssh -L 8080:127.0.0.1:8080 server`.
- **Start after a reboot without sudo:** add a user crontab entry with `crontab -e`:
  `@reboot cd /path/to/zeilenjoule-winner/winner && ./kit start >> .run/logs/boot.log 2>&1`

## 6. Docker mode details

- **Images:**
  - PostgreSQL is `pgvector/pgvector:0.8.7-pg18`, the measured image.
  - Gateway and worker are built from `deploy/docker/services.Dockerfile`.
  - All base images are pinned by digest.
- **Profiles** follow `ZJ_MODEL_SERVER`:
  - `vllm-tei` adds `vllm` and `tei`.
  - `ollama` adds an Ollama container; models go into a volume.
  - `external` adds nothing.
- **Model location (vLLM/TEI):** `ZJ_VLLM_MODEL` / `ZJ_TEI_MODEL` take a Hugging Face id (downloaded into a volume)
  or `/models/<folder>` for a local copy under `ZJ_MODELS_DIR`.
  - Example with this repository's model folders: `ZJ_MODELS_DIR=/path/to/zeilenjoule/models`,
    `ZJ_VLLM_MODEL=/models/vllm/Qwen3.5-9B-AWQ-4bit`, `ZJ_TEI_MODEL=/models/derived/bge-m3/hf512`.
  - With a local path, remove `--revision …` from `ZJ_VLLM_ARGS` / `ZJ_TEI_ARGS`.
- **TEI image per GPU:** `ZJ_TEI_IMAGE` defaults to the build for RTX 50xx (`120-1.9.4`). Other GPUs need the
  matching tag of `ghcr.io/huggingface/text-embeddings-inference`:
  - `86-1.9.4` for RTX 30xx / A10
  - `89-1.9.4` for RTX 40xx / L4
  - `1.9.4` for A100
  - `hopper-1.9.4` for H100
  - `cpu-1.9.4` for no GPU
- **GPU memory:** vLLM takes 70 % (`--gpu-memory-utilization 0.70`), and TEI uses the rest. On a 24 GB GPU you can
  raise `--max-num-seqs`.
- **CPU-only host with Ollama:** set `ZJ_DOCKER_GPU=0`.
- **Model servers on the host (`external`):** `127.0.0.1` URLs are rewritten to `host.docker.internal`, so the host
  server must listen on an address containers can reach, for example `OLLAMA_HOST=0.0.0.0`.

## 7. Operations

| Task | Command |
|---|---|
| Status, logs | `./kit status`, `./kit logs gateway` (`worker`, `ollama`, `postgres`; docker: also `vllm`, `tei`) |
| Stop / start | `./kit stop`, `./kit start` (data is kept) |
| Delete all data | `./kit reset-db` (asks; `--yes` skips the question) |
| Update | new checkout or bundle, then `./kit setup native` again (keeps `winner.env` and the data, rebuilds) |
| Backup (native) | `PGPASSWORD=… .run/env/bin/pg_dump -h 127.0.0.1 -p 55432 -U zeilenjoule zeilenjoule > backup.sql` |
| Backup (docker) | `docker exec zj-winner-postgres-1 pg_dump -U zeilenjoule zeilenjoule > backup.sql` |
| Uninstall | `./kit stop`, then delete `winner/.run`; docker: `docker compose -p zj-winner down -v` |

**Security defaults:**
- Secrets are generated at setup into `winner.env` (mode 600; it is never packed).
- Every API call needs `X-API-Key` and is rate-limited per key.
- The router's SQL runs as `zj_ask`. That role is read-only, can see only the view `meldungen`, has a 10 s
  timeout, and every statement is parsed and checked before it runs.

## 8. Example (100 rows)

`example/` shows the whole flow on data small enough to check by hand:
- **`stoermeldungen_100.cp1252.csv`:** the first 100 rows of the synthetic data set S (NRW plants, typos, German
  formats; no personal data).
- **`fragen.json`:** 9 German questions with the right answers. 7 are counts, sums, or "which plant / which report";
  2 are text-search questions.
  - The answers were computed by `derive_expected.py` from the generator's gold data, not typed by hand.
- **`run.sh`:** loads the rows, asks each question, and prints OK/FAIL with the route, the SQL and the answer.

Results seen while testing the kit (not measured):

| Setup | Correct |
|---|---|
| vLLM + TEI (measured models) | 8 of 9 |
| Ollama qwen3:8b | 8 of 9 |
| llama.cpp qwen3:8b | 7 of 9 |

The misses are model mistakes, and the printed SQL shows them:
- `sum()` over a boolean column
- an invented damage-type filter
- `ORDER BY … DESC` without `NULLS LAST`

## 9. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `./kit start` waits on `/readyz` | `./kit status` shows which part is down (`database`, `llm`, `tei`); then check `./kit logs …` |
| Empty answers, `answer: null` with route `invalid` | a reasoning model is thinking: set `ZJ_LLM_EXTRA_JSON` (§4) |
| `database stores 1024-dimensional embeddings but ZJ_EMBED_DIM is …` | the embedding model changed: `./kit reset-db`, then ingest again |
| `model returns N dimensions, ZJ_EMBED_DIM is M` | set `ZJ_EMBED_DIM=N` |
| Address already in use | change `ZJ_GATEWAY_PORT`, `ZJ_PG_PORT`, `ZJ_WORKER_METRICS` / `ZJ_GATEWAY_METRICS` |
| vLLM out of memory | lower `--max-model-len` / `--max-num-seqs`, or raise `--gpu-memory-utilization` if TEI is not used |
| Slow answers on CPU | expected with CPU-only models; raise `ZJ_REQUEST_TIMEOUT` |

## 10. Limits

- **Measured setup:** only vLLM + TEI was measured (`e06_stack_go`, `e07_stack_load`; see `reports/`). Other
  models work but are **not measured**.
- **Input:** the stack is built for SAP-PM-like Störmeldungen with the columns above. The plant catalog, the
  damage-type classes and the prompts are German. Other tables need changes to:
  - the Rust parser (`contestants/rust/a16_rs_arrow_rayon`, `spec/track_a_lexicon.json`)
  - the schema (`services/internal/db/migrations`)
  - the prompts (`services/internal/prompts`)
- **Tested combinations:** native mode was tested on Linux x86_64, with Ollama and with llama.cpp. Docker mode was
  tested with vLLM + TEI. Docker + Ollama and Docker + external are configured the same way but were not run end
  to end.
- **What is not included:** Prometheus/Grafana (energy per question) and Kubernetes. Use the full stack in
  `deploy/` for those (`docs/RUNBOOK.md`).

Why it is built this way: ADR 0021 (architecture), ADR 0027 (search and streaming), ADR 0028 (this kit).
