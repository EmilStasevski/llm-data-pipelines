"""Parse ESEF / inline-XBRL annual reports.

One file gives us two things:
  * plain text of the report  -> the *input* for extraction / RAG pipelines
  * tagged numeric facts      -> free *gold labels* (revenue, equity, ...)
"""

from __future__ import annotations

import polars as pl
from lxml import etree

IX = "http://www.xbrl.org/2013/inlineXBRL"
XBRLI = "http://www.xbrl.org/2003/instance"
XBRLDI = "http://xbrl.org/2006/xbrldi"

# KPI concepts that are tagged in nearly every IFRS consolidated report.
KPI_CONCEPTS = {
    "ifrs-full:Revenue": "revenue",
    "ifrs-full:ProfitLoss": "net_income",
    "ifrs-full:Assets": "total_assets",
    "ifrs-full:Equity": "equity",
    "ifrs-full:CashAndCashEquivalents": "cash",
    "ifrs-full:ProfitLossFromOperatingActivities": "operating_income",
}

FACT_SCHEMA = {
    "concept": pl.Utf8,
    "context": pl.Utf8,
    "value": pl.Float64,
    "unit": pl.Utf8,
    "decimals": pl.Utf8,
    "period_start": pl.Utf8,
    "period_end": pl.Utf8,
    "has_dims": pl.Boolean,
    "raw": pl.Utf8,
}

_PARSER = etree.XMLParser(recover=True, huge_tree=True, resolve_entities=False)


def _to_number(text: str, fmt: str | None, scale: str | None, sign: str | None) -> float | None:
    t = (text or "").replace("\xa0", "").replace(" ", "").replace(" ", "").strip()
    f = (fmt or "").lower()
    if "zerodash" in f or "fixed-zero" in f or t in {"-", "–", "—", ""}:
        val = 0.0
    else:
        if "comma-decimal" in f or "commadecimal" in f:  # 1.234,56
            t = t.replace(".", "").replace(",", ".")
        else:  # 1,234.56 (default)
            t = t.replace(",", "")
        try:
            val = float(t)
        except ValueError:
            return None
    val *= 10 ** int(scale or 0)
    return -val if sign == "-" else val


def _contexts(root: etree._Element) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for ctx in root.iter(f"{{{XBRLI}}}context"):
        period = ctx.find(f"{{{XBRLI}}}period")
        fields = {
            tag: period.findtext(f"{{{XBRLI}}}{tag}") if period is not None else None
            for tag in ("instant", "startDate", "endDate")
        }
        instant = fields["instant"]
        out[ctx.get("id")] = {
            "period_start": fields["startDate"] or instant,
            "period_end": fields["endDate"] or instant,
            "has_dims": bool(
                ctx.findall(f".//{{{XBRLDI}}}explicitMember")
                or ctx.findall(f".//{{{XBRLDI}}}typedMember")
            ),
        }
    return out


def parse_facts(xhtml: bytes) -> pl.DataFrame:
    """All numeric inline-XBRL facts of one report as a DataFrame."""
    root = etree.fromstring(xhtml, _PARSER)
    ctxs = _contexts(root)
    rows = []
    for el in root.iter(f"{{{IX}}}nonFraction"):
        raw = "".join(el.itertext())
        c = ctxs.get(el.get("contextRef"), {})
        rows.append(
            {
                "concept": el.get("name"),
                "context": el.get("contextRef"),
                "value": _to_number(raw, el.get("format"), el.get("scale"), el.get("sign")),
                "unit": el.get("unitRef"),
                "decimals": el.get("decimals"),
                "period_start": c.get("period_start"),
                "period_end": c.get("period_end"),
                "has_dims": c.get("has_dims", False),
                "raw": raw.strip(),
            }
        )
    return pl.DataFrame(rows, schema=FACT_SCHEMA)


def gold_kpis(facts: pl.DataFrame) -> dict[str, float]:
    """Latest dimension-free value per KPI concept (the consolidated total)."""
    df = (
        facts.filter(~pl.col("has_dims") & pl.col("concept").is_in(list(KPI_CONCEPTS)))
        .drop_nulls("value")
        .sort("period_end", descending=True)
        .unique(subset="concept", keep="first", maintain_order=True)
    )
    return {KPI_CONCEPTS[c]: v for c, v in zip(df["concept"], df["value"])}


_BLOCK = {
    "p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr", "table",
    "section", "article", "br",
}  # fmt: skip


def xhtml_to_text(xhtml: bytes) -> str:
    """Plain text, one block element per line (no ix:header noise)."""
    root = etree.fromstring(xhtml, _PARSER)
    out: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        text = " ".join("".join(buf).split())
        buf.clear()
        if text:
            out.append(text)

    def walk(el: etree._Element) -> None:
        if not isinstance(el.tag, str):  # comment / PI
            buf.append(el.tail or "")
            return
        q = etree.QName(el)
        if q.namespace == IX and q.localname == "header":
            buf.append(el.tail or "")
            return
        if q.localname.lower() in {"script", "style"}:
            buf.append(el.tail or "")
            return
        block = q.localname.lower() in _BLOCK
        if block:
            flush()
        buf.append(el.text or "")
        for child in el:
            walk(child)
        if block:
            flush()
        buf.append(el.tail or "")

    walk(root)
    flush()
    return "\n".join(out)
