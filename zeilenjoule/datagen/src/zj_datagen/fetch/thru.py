"""thru.de (UBA PRTR): NRW facilities with their main activity (German industrial descriptions).

Terms (https://thru.de/nutzungsbedingungen/): use allowed with thru.de named as source; content must not
be altered or falsified → values are kept verbatim (subset only, no noise). Personal data: facility
names and street addresses are dropped (agricultural facilities can be run by private persons)."""

from __future__ import annotations

import sqlite3
import zipfile
from pathlib import Path
from typing import Any

import pyarrow as pa

from zj_datagen.fetch.http import PoliteClient
from zj_datagen.writer import write_table

ZIP_URL = "https://thru.de/wp-content/uploads/2026/03/prtr_2024.zip"
DB_NAME = "prtr_2024.db"
QUERY = """
SELECT b.kennnummer, b.jahr, b.ort, b.postleitzahl, b.nace_code, b.nace_text, b.betriebsstunden,
       t.prtr_schluessel, t.prtr_beschreibung, t.ivu_schluessel, t.ivu_beschreibung, t.branchengruppe
FROM betriebe b
JOIN taetigkeiten t ON t.betriebe_id = b.id AND t.haupttaetigkeit = 1
WHERE b.bundesland = 'nw'
  AND b.jahr = (SELECT max(b2.jahr) FROM betriebe b2
                WHERE b2.kennnummer = b.kennnummer AND b2.bundesland = 'nw')
ORDER BY b.kennnummer
"""


def safe_extract(zip_path: Path, member: str, dest: Path) -> Path:
    """Extract one member, refusing absolute paths and '..' (archives are untrusted input)."""
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        bad = [n for n in names if n.startswith("/") or ".." in Path(n).parts]
        if bad:
            raise ValueError(f"unsafe paths in {zip_path.name}: {bad}")
        if member not in names:
            raise ValueError(f"{member} not in {zip_path.name}: {names}")
        dest.mkdir(parents=True, exist_ok=True)
        return Path(z.extract(member, dest))


def nrw_facilities(db: Path) -> list[dict[str, Any]]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)  # read-only
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(QUERY)]
    finally:
        con.close()


def fetch(raw: Path, out: Path, client: PoliteClient) -> dict[str, Any]:
    zip_path = raw / "prtr_2024.zip"
    if not zip_path.exists():
        zip_path.write_bytes(client.request("GET", ZIP_URL))
    db = safe_extract(zip_path, DB_NAME, raw / "extracted")
    rows = nrw_facilities(db)
    out.mkdir(parents=True, exist_ok=True)
    write_table(pa.Table.from_pylist(rows), out / "thru_betriebe_nrw.parquet")
    return {"source": ZIP_URL, "rows": len(rows), "file": "thru_betriebe_nrw.parquet"}
