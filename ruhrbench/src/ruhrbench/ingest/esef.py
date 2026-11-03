"""Fetch ESEF annual reports from filings.xbrl.org (JSON:API).

Response shape verified against a real response: each filing has attributes
country, period_end, fxo_id, report_url (the xhtml itself), package_url (zip),
json_url (xBRL-JSON). Some filings have report_url = null; those are skipped.
Open question: whether the database contains German (DE) filings at all.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from urllib.parse import urljoin

import httpx

BASE = "https://filings.xbrl.org"
API = f"{BASE}/api/filings"


def _abs(url: str | None) -> str | None:
    return urljoin(BASE, url) if url else None


def _item_to_row(item: dict) -> dict:
    a = item.get("attributes", {})
    return {
        "id": item.get("id"),
        "country": a.get("country"),
        "period_end": a.get("period_end"),
        "fxo_id": a.get("fxo_id"),
        "report_url": _abs(a.get("report_url")),
        "package_url": _abs(a.get("package_url")),
        "json_url": _abs(a.get("json_url")),
        "error_count": a.get("error_count"),
    }


def list_filings(
    country: str = "DE",
    limit: int = 50,
    client: httpx.Client | None = None,
    max_pages: int = 20,
) -> list[dict]:
    """Up to `limit` filings of a country that have a downloadable xhtml report."""
    own = client is None
    client = client or httpx.Client(timeout=60, follow_redirects=True)
    rows: list[dict] = []
    try:
        for page in range(1, max_pages + 1):
            r = client.get(
                API,
                params={"filter[country]": country, "page[size]": 100, "page[number]": page},
            )
            r.raise_for_status()
            data = r.json().get("data", [])
            if not data:
                break
            rows += [row for row in map(_item_to_row, data) if row["report_url"]]
            if len(rows) >= limit:
                break
        return rows[:limit]
    finally:
        if own:
            client.close()


def download_report(row: dict, out_dir: Path, client: httpx.Client) -> list[Path]:
    """Download the xhtml report (directly, or out of the package zip as fallback)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    if row.get("report_url"):
        r = client.get(row["report_url"])
        r.raise_for_status()
        dest = out_dir / f"{row['fxo_id']}.xhtml"
        dest.write_bytes(r.content)
        return [dest]
    if row.get("package_url"):
        r = client.get(row["package_url"])
        r.raise_for_status()
        written = []
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            for name in z.namelist():
                if name.lower().endswith((".xhtml", ".html")) and "/reports/" in name.lower():
                    dest = out_dir / f"{row['fxo_id']}__{Path(name).name}"
                    dest.write_bytes(z.read(name))
                    written.append(dest)
        return written
    return []
