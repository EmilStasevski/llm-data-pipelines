"""ruhrbench command line: fetch data, parse documents."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Annotated

import httpx
import polars as pl
import typer
from rich import print

from . import paths
from .eval import gold_quality
from .ingest import esef, hf_labeled, pdf_baseline, prices, process
from .report.blocks import update_blocks

app = typer.Typer(no_args_is_help=True, help="ruhrbench: German NRW industry RAG benchmark")


@app.command("fetch-prices")
def fetch_prices(start: str = "2024-01-01", end: str = "2024-12-31") -> None:
    """Day-ahead electricity prices DE-LU -> data/raw/prices/."""
    out = paths.RAW / "prices" / f"day_ahead_{start}_{end}.parquet"
    paths.ensure(out.parent)
    df = prices.fetch(date.fromisoformat(start), date.fromisoformat(end))
    df.write_parquet(out)
    print(f"[green]{df.height} rows[/green] -> {out}")


@app.command("fetch-hf")
def fetch_hf(name: str = "germanquad") -> None:
    """Ready-labeled German dataset from the HF Hub (needs the 'hf' extra)."""
    out = hf_labeled.fetch(name, paths.RAW / "hf")
    print(f"[green]{len(out)} files[/green] -> {paths.RAW / 'hf'}")


@app.command("fetch-esef")
def fetch_esef(country: str = "DE", limit: int = 20, raw: bool = False) -> None:
    """List German ESEF filings and download their xhtml reports -> data/raw/esef/."""
    with httpx.Client(timeout=120, follow_redirects=True) as client:
        rows = esef.list_filings(country, limit, client)
        if raw:
            print(rows[:1])
            return
        if not rows:
            print(f"[yellow]no filings with a report for country={country}[/yellow]")
            return
        out_dir = paths.RAW / "esef"
        n = 0
        for row in rows:
            n += len(esef.download_report(row, out_dir, client))
    print(f"[green]{n} reports[/green] -> {out_dir}")


@app.command("extract-ixbrl")
def extract_ixbrl(report: Path) -> None:
    """One xhtml report -> text file + facts parquet + gold KPI json in data/interim|gold."""
    print(process.process_report(report, paths.INTERIM, paths.GOLD))


@app.command("extract-all")
def extract_all(esef_dir: Annotated[Path | None, typer.Option()] = None) -> None:
    """Process every xhtml in data/raw/esef -> data/interim/esef_manifest.parquet (+ summary)."""
    esef_dir = esef_dir or paths.RAW / "esef"
    files = sorted(esef_dir.glob("*.xhtml"))
    if not files:
        print(f"[yellow]no .xhtml files in {esef_dir}[/yellow]")
        return
    rows, failed = [], []
    for f in files:
        try:
            rows.append(process.process_report(f, paths.INTERIM, paths.GOLD))
        except Exception as e:  # noqa: BLE001  # one broken report must not stop the batch
            failed.append((f.name, repr(e)))
    df = pl.DataFrame(rows, schema=process.manifest_schema())
    out = paths.INTERIM / "esef_manifest.parquet"
    df.write_parquet(out)
    print(f"[green]{df.height} reports[/green] -> {out}  ({len(failed)} failed)")
    print(
        df.group_by("lang").agg(
            pl.len().alias("reports"), (pl.col("n_kpis") > 0).sum().alias("with_kpis")
        )
    )
    for name, err in failed:
        print(f"[red]failed[/red] {name}: {err}")


@app.command("parse-pdfs")
def parse_pdfs(pdf_dir: Path, out: Annotated[Path | None, typer.Option()] = None) -> None:
    """Baseline PDF extraction (PyMuPDF) -> parquet, prints pages/s."""
    out = out or paths.INTERIM / "pdf_pages_pymupdf.parquet"
    stats = pdf_baseline.parse_dir(pdf_dir, out)
    print(stats)


@app.command("gold-report")
def gold_report(doc: Path = Path("docs/gold_quality.md")) -> None:
    """Audit gold KPIs and rewrite the generated blocks of docs/gold_quality.md."""
    blocks = gold_quality.build_blocks(paths.RAW / "esef", paths.INTERIM, paths.GOLD)
    update_blocks(doc, blocks)
    print(f"[green]{len(blocks)} blocks[/green] -> {doc}")


if __name__ == "__main__":
    app()
