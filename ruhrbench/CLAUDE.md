# ruhrbench: project context

Read this and `PLAN.md` at the start of every session. This file says what is done. `PLAN.md` says what to do next and holds the working rules.

## Purpose

Compare the quality, speed and GPU energy use of RAG and table-extraction pipelines on German-language financial reports, from a Python baseline up to Rust, on one local machine.

## Current state (2026-11-03)

- Milestone: M1 in progress.
- Ingestion exists in `src/ruhrbench/ingest/`. `data/` holds 40 Austrian ESEF filings (705 MB raw), GermanQuAD and 2024 day-ahead prices. All data is gitignored.
- `uv run ruhrbench gold-report` audits gold KPIs and rewrites the generated blocks in `docs/gold_quality.md`.
- `report/blocks.py` is the generated-block writer. Reuse it for later reports.
- Work is on branch `m1-gold-quality`. No remote yet.

## Things that are easy to get wrong

- **The git root is the parent directory** `/home/emil/llm-data-pipelines`, on purpose: it will hold 2 or 3 projects. `.github/` and `.pre-commit-config.yaml` must go at that root, and CI jobs need `working-directory: ruhrbench`. GitHub setup comes at the end, not in M2 as the plan says.
- **The 40 filings are 34 distinct reports from 18 companies.** The rest are language versions and re-filings. Split and count by report (LEI + fiscal year), or twins leak between train and test and inflate scores.
- **XBRL gold can be wrong.** Skip quarantined filings when scoring. See `docs/gold_quality.md`.
- **`-0` / `-1` in a filing name does not indicate language.** Addiko has English as `-0` and German as `-1`, while Energie AG FY2021 has them the other way round. Use the manifest's `lang` column.
- **Many statements are positioned `<div>`s, not `<table>`s**, so row chunking cannot rely on `<tr>`. Some text layers also print numbers twice, such as `1.156,31.156,3` in Addiko FY2020 `-1`.
- **`nvidia-smi` always lists desktop graphics processes** (Xorg, gnome-shell, type `G`). The M2 busy check must only refuse on compute (`C`) processes or treat these as a known baseline, or it will never start.
- Power limit range is only 150 W to 216 W, so the M5 sweep has little room below the 180 W default.

## Commands

```
uv sync
just check        # ruff, mypy, pytest; run before every commit
just gold-report
```

The `pre-commit` tool is installed but no hooks are configured yet (M2).

## Next steps

1. Get the 4 hand checks in `docs/gold_quality.md` confirmed against the published PDFs.
2. Write `docs/data.md` with the GermanQuAD schema.
3. Add a test that checks `fetch-prices` output against the real file in `data/raw/prices/`, then correct the stale "not tested" section in `README.md`.
4. Make the gold loader exclude quarantined filings.
5. NRW gold: propose companies and sources to the user, and check site terms before downloading anything.
6. Extend gold to all tagged rows of the primary statements, as Parquet with a documented schema. Decide then whether to add concept aliases (see the missing-KPI table in `docs/gold_quality.md`).
