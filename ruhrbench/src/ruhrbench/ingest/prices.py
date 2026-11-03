"""German day-ahead electricity prices (DE-LU) from Energy-Charts (Fraunhofer ISE).

Structured, tiny, and relevant for NRW industry (energy costs). Used for the
Polars/DuckDB side of the benchmark and as a source for question answering.
License of the data: CC BY 4.0 (credit Energy-Charts / Bundesnetzagentur | SMARD.de).
"""

from __future__ import annotations

from datetime import date

import httpx
import polars as pl

API = "https://api.energy-charts.info/price"


def parse_payload(payload: dict) -> pl.DataFrame:
    """Energy-Charts JSON -> DataFrame(ts[Europe/Berlin], eur_per_mwh)."""
    return (
        pl.DataFrame({"unix_seconds": payload["unix_seconds"], "eur_per_mwh": payload["price"]})
        .with_columns(
            pl.from_epoch("unix_seconds", time_unit="s")
            .dt.replace_time_zone("UTC")
            .dt.convert_time_zone("Europe/Berlin")
            .alias("ts")
        )
        .select("ts", pl.col("eur_per_mwh").cast(pl.Float64))
        .drop_nulls("eur_per_mwh")
    )


def fetch(
    start: date, end: date, bzn: str = "DE-LU", client: httpx.Client | None = None
) -> pl.DataFrame:
    own = client is None
    client = client or httpx.Client(timeout=60, follow_redirects=True)
    try:
        r = client.get(API, params={"bzn": bzn, "start": start.isoformat(), "end": end.isoformat()})
        r.raise_for_status()
        return parse_payload(r.json())
    finally:
        if own:
            client.close()
