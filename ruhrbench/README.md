# ruhrbench

Benchmark of RAG / text-extraction pipelines on German NRW industry documents,
from a pandas baseline up to Rust (Polars, Tantivy, Qdrant, candle/mistral.rs).
Target hardware: RTX 5060 Ti, 16 GB VRAM, Ubuntu server.

## Data sources (step 1: ingestion)

| Source | What | Labels | Command |
|---|---|---|---|
| ESEF / inline-XBRL annual reports (filings.xbrl.org) | German company reports as xhtml | **Free gold KPIs** from the XBRL tags (revenue, net income, assets, equity, ...) | `ruhrbench fetch-esef`, `ruhrbench extract-ixbrl <file>` |
| GermanQuAD (HF Hub) | QA over German Wikipedia | gold answers + contexts (retrieval/RAG eval) | `ruhrbench fetch-hf` (needs `uv sync --extra hf`; uses the Hub's parquet export) |
| Energy-Charts day-ahead prices DE-LU | hourly electricity price, CC BY 4.0 | structured data for the Polars/DuckDB side | `ruhrbench fetch-prices` |
| Your own PDFs (any company report) | PDF text extraction baseline | none (compare against ESEF text where the same report exists) | `ruhrbench parse-pdfs <dir>` |

Why ESEF: one file gives the text (pipeline input) *and* the tagged numbers (gold labels),
so extraction models can be scored automatically with no manual labeling.

## Quick start

    uv sync
    uv run pytest                     # offline tests
    uv run ruhrbench fetch-prices --start 2024-01-01 --end 2024-12-31
    uv run ruhrbench fetch-esef --limit 20
    uv run ruhrbench extract-ixbrl data/raw/esef/<file>.xhtml

## Status

- Tested (offline, synthetic data): iXBRL fact + gold KPI parsing incl. German number formats,
  xhtml-to-text, price payload parsing, PDF extraction, German de-hyphenation.
- NOT yet tested against live services: `fetch-esef` (API field names from memory; use `--raw`
  to inspect one item), `fetch-prices` (Energy-Charts response shape from memory), `fetch-hf`.
