"""TED (EU public procurement): NRW contract notices (place of performance NUTS DEA1–DEA5).

Reuse: notices may be reused freely; TED editorial content is CC BY 4.0 (credit + mark changes).
Change made here: e-mail addresses and phone numbers in texts are replaced by "[entfernt]".
Contact-point fields are never requested."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pyarrow as pa

from zj_datagen.fetch.http import PoliteClient
from zj_datagen.fetch.redact import redact
from zj_datagen.writer import write_table

SEARCH_URL = "https://api.ted.europa.eu/v3/notices/search"
FIELDS = [
    "publication-number", "publication-date", "notice-type", "title-proc", "description-proc",
    "buyer-name", "buyer-city", "place-of-performance", "classification-cpv",
    "deadline-receipt-tender-date-lot",
]  # fmt: skip
PAGE_SIZE = 250


def query(date_from: str, date_to: str) -> str:
    return (
        "place-of-performance IN (DEA1 DEA2 DEA3 DEA4 DEA5) AND notice-type=cn-standard "
        f"AND publication-date>={date_from} AND publication-date<={date_to}"
    )


def _lang(v: Any, prefer: tuple[str, ...] = ("deu", "mul", "eng")) -> str | None:
    """TED multilingual field {"deu": "..."} or {"deu": ["..."]} → one string (German preferred)."""
    if not isinstance(v, dict):
        return None
    for lang in prefer:
        x = v.get(lang)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        if isinstance(x, list) and x:
            return str(x[0])  # pyright: ignore[reportUnknownArgumentType]
        if isinstance(x, str):
            return x
    return None


def normalize(n: dict[str, Any]) -> tuple[dict[str, Any], int]:
    title, r1 = redact(_lang(n.get("title-proc")))
    desc, r2 = redact(_lang(n.get("description-proc")))
    pop = sorted({p for p in n.get("place-of-performance", []) if str(p).startswith("DEA")})
    deadline = n.get("deadline-receipt-tender-date-lot") or [None]
    row = {
        "publication_number": n.get("publication-number"),
        "publication_date": str(n.get("publication-date", ""))[:10] or None,
        "title": title,
        "description": desc,
        "buyer_name": _lang(n.get("buyer-name")),
        "buyer_city": _lang(n.get("buyer-city")),
        "nuts": ",".join(pop),
        "cpv": ",".join(sorted(set(n.get("classification-cpv", [])))),
        "deadline": str(deadline[0])[:10] if deadline[0] else None,
    }
    return row, r1 + r2


def fetch(out: Path, client: PoliteClient, date_from: str, date_to: str, max_notices: int) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    redactions = 0
    page = 1
    total = None
    while len(rows) < max_notices:
        body = {"query": query(date_from, date_to), "fields": FIELDS, "limit": PAGE_SIZE, "page": page,
                "paginationMode": "PAGE_NUMBER"}  # fmt: skip
        res = client.post_json(SEARCH_URL, body)
        total = res.get("totalNoticeCount", total)
        notices = res.get("notices", [])
        if not notices:
            break
        for n in notices:
            row, k = normalize(n)
            rows.append(row)
            redactions += k
        page += 1
    rows = rows[:max_notices]
    out.mkdir(parents=True, exist_ok=True)
    write_table(pa.Table.from_pylist(rows), out / "ted_nrw.parquet")
    return {"source": SEARCH_URL, "query": query(date_from, date_to), "total_available": total,
            "rows": len(rows), "redactions": redactions, "file": "ted_nrw.parquet"}  # fmt: skip
