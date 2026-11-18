# ZeilenJoule winner kit

The benchmark winner (PostgreSQL + pgvector + German full text, Go gateway/worker, router LLM) as an installable
service. It runs with Docker or without Docker and sudo, and with any local model.

- Guide: [instruction.md](instruction.md) (English) · [instruction.de.md](instruction.de.md) (Deutsch)
- Design decisions: [ADR 0028](../docs/adr/0028-winner-kit-beliebige-modelle.md)

```bash
# A) without Docker (no sudo): Ollama + PostgreSQL/pgvector installed under .run/
./kit setup native --preset ollama && ./kit start

# B) with Docker: the measured models vLLM + TEI (NVIDIA GPU with 16 GB)
./kit setup docker --preset vllm-tei && ./kit start

# use it
./example/run.sh                                      # 100 rows, 9 checked questions
./kit ingest my_reports.csv
./kit ask "Wie viele Meldungen gab es 2025 im Werk Krefeld?"
# browser: http://127.0.0.1:8080/demo  (API key: ZJ_API_KEYS in winner.env)
```

| Folder / file | Purpose |
|---|---|
| `kit` | the single command: setup, start, stop, status, logs, ingest, ask, search, reset-db, pack |
| `winner.env.example` | all settings; `./kit setup` copies it to `winner.env` and generates the secrets |
| `presets/` | model setups: `vllm-tei` (measured), `ollama`, `llamacpp`, `external` |
| `lib/` | native (no-sudo) and Docker implementations |
| `docker/` | compose file for docker mode |
| `example/` | 100-row sample CSV, questions with expected answers, checker |
