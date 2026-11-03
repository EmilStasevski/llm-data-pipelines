"""Audit the XBRL-derived gold KPIs: coverage, cross-filing consistency, accounting sanity.

XBRL tags are written by filers and can be wrong, so gold is checked before anything is scored
against it. The strongest check needs no labels: a report's current-year value must match the
prior-year column of the following year's report.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

import polars as pl
from lxml import etree

from ..ingest import ixbrl
from ..report.blocks import markdown_table

KPI_NAMES = list(ixbrl.KPI_CONCEPTS.values())
_CONCEPT_BY_KPI = {kpi: concept for concept, kpi in ixbrl.KPI_CONCEPTS.items()}
_FILING = re.compile(
    r"^(?P<lei>[A-Z0-9]{20})-(?P<fy_end>\d{4}-\d{2}-\d{2})-ESEF-[A-Z]{2}-(?P<seq>\d+)$"
)

# Tolerance for "same number" across filings (rounding, unit presentation).
REL_TOL = 0.005
# Concept-name patterns that may be an alias when a KPI's exact concept is missing.
_ALIASES = {
    "revenue": r"(?i)revenue|sales|umsatz",
    "net_income": r"(?i)profitloss",
    "total_assets": r"(?i):assets$",
    "equity": r"(?i)equity$|equityattributable",
    "cash": r"(?i)cashandcash|cashandbank",
    "operating_income": r"(?i)operating|ebit",
}


def parse_filing(stem: str) -> dict[str, str | int]:
    m = _FILING.match(stem)
    if m is None:
        raise ValueError(f"not an ESEF filing id: {stem}")
    return {
        "filing": stem,
        "lei": m["lei"],
        "fy_end": m["fy_end"],
        "seq": int(m["seq"]),
    }


_GOLD_SCHEMA = {
    "filing": pl.Utf8,
    "lei": pl.Utf8,
    "fy_end": pl.Utf8,
    "seq": pl.Int64,
    "kpi": pl.Utf8,
    "value": pl.Float64,
}
_FACT_SCHEMA = {
    "filing": pl.Utf8,
    "lei": pl.Utf8,
    "fy_end": pl.Utf8,
    "concept": pl.Utf8,
    "period_start": pl.Utf8,
    "period_end": pl.Utf8,
    "value": pl.Float64,
    "raw": pl.Utf8,
}


def load_gold(gold_dir: Path) -> pl.DataFrame:
    """Gold KPI json files as long rows; a KPI absent from a filing has value null."""
    rows = []
    for path in sorted(gold_dir.glob("*.kpis.json")):
        meta = parse_filing(path.name.removesuffix(".kpis.json"))
        values = json.loads(path.read_text(encoding="utf-8"))
        rows += [{**meta, "kpi": kpi, "value": values.get(kpi)} for kpi in KPI_NAMES]
    return pl.DataFrame(rows, schema=_GOLD_SCHEMA)


def load_facts(interim_dir: Path) -> pl.DataFrame:
    """Dimension-free facts (consolidated totals) of all filings."""
    frames = []
    for path in sorted(interim_dir.glob("*.facts.parquet")):
        meta = parse_filing(path.name.removesuffix(".facts.parquet"))
        frames.append(
            pl.read_parquet(path)
            .filter(~pl.col("has_dims"))
            .select(
                pl.lit(meta["filing"]).alias("filing"),
                pl.lit(meta["lei"]).alias("lei"),
                pl.lit(meta["fy_end"]).alias("fy_end"),
                "concept",
                "period_start",
                "period_end",
                "value",
                "raw",
            )
        )
    return pl.concat(frames) if frames else pl.DataFrame(schema=_FACT_SCHEMA)


def coverage(gold: pl.DataFrame) -> pl.DataFrame:
    """Per KPI: how many filings, and how many distinct reports (company + year), have a value.

    A company can file the same report twice (language versions, re-filings), so filing counts
    overstate the amount of independent gold.
    """
    n_filings = gold["filing"].n_unique()
    n_reports = gold.select("lei", "fy_end").unique().height
    has = gold.filter(pl.col("value").is_not_null())
    rows = []
    for kpi in KPI_NAMES:
        sub = has.filter(pl.col("kpi") == kpi)
        n_rep = sub.select("lei", "fy_end").unique().height
        rows.append(
            {
                "kpi": kpi,
                "filings": f"{sub.height}/{n_filings}",
                "reports": f"{n_rep}/{n_reports}",
                "report_coverage": f"{n_rep / n_reports:.0%}" if n_reports else "n/a",
            }
        )
    return pl.DataFrame(rows)


def missing_with_aliases(gold: pl.DataFrame, facts: pl.DataFrame) -> pl.DataFrame:
    """One row per report and missing KPI, with similarly named concepts tagged in that report."""
    missing = (
        gold.filter(pl.col("value").is_null())
        .select("lei", "fy_end", "kpi")
        .unique()
        .sort("lei", "fy_end", "kpi")
    )
    rows = []
    for lei, fy_end, kpi in missing.rows():
        candidates = (
            facts.filter(
                (pl.col("lei") == lei)
                & (pl.col("fy_end") == fy_end)
                & (pl.col("period_end") == fy_end)
                & pl.col("concept").str.contains(_ALIASES[kpi])
            )["concept"]
            .unique()
            .sort()
            .to_list()
        )
        rows.append(
            {
                "lei": lei,
                "fy_end": fy_end,
                "kpi": kpi,
                "similar_concepts": ", ".join(candidates),
            }
        )
    return pl.DataFrame(
        rows,
        schema={
            "lei": pl.Utf8,
            "fy_end": pl.Utf8,
            "kpi": pl.Utf8,
            "similar_concepts": pl.Utf8,
        },
    )


def classify_pair(value: float, comparative: float) -> str:
    """Compare a filed value with the next filing's prior-year column."""
    if math.isclose(value, comparative, rel_tol=REL_TOL, abs_tol=1.0):
        return "ok"
    if value and comparative and (value > 0) == (comparative > 0):
        ratio = abs(comparative / value)
        power = 10.0 ** round(math.log10(ratio))
        if power != 1.0 and math.isclose(ratio, power, rel_tol=REL_TOL):
            return "scale_mismatch"
    if math.isclose(value, -comparative, rel_tol=REL_TOL):
        return "sign_flip"
    return "differs"


def cross_year(gold: pl.DataFrame, facts: pl.DataFrame) -> pl.DataFrame:
    """Gold value of year t vs the comparative for t in the report of year t+1."""
    current = (
        gold.filter(pl.col("value").is_not_null()).select("lei", "fy_end", "kpi", "value").unique()
    )
    concepts = pl.DataFrame(
        {"kpi": list(_CONCEPT_BY_KPI), "concept": list(_CONCEPT_BY_KPI.values())}
    )
    comparatives = (
        facts.join(concepts, on="concept")
        .filter((pl.col("period_end") < pl.col("fy_end")) & pl.col("value").is_not_null())
        .select(
            pl.col("lei"),
            pl.col("period_end").alias("fy_end"),
            "kpi",
            pl.col("value").alias("comparative"),
            pl.col("fy_end").alias("next_fy_end"),
        )
        .unique()
    )
    pairs = current.join(comparatives, on=["lei", "fy_end", "kpi"]).sort("lei", "fy_end", "kpi")
    status = [
        classify_pair(v, c) for v, c in zip(pairs["value"], pairs["comparative"], strict=True)
    ]
    return pairs.with_columns(pl.Series("status", status, dtype=pl.Utf8))


def cross_year_summary(pairs: pl.DataFrame) -> pl.DataFrame:
    return pairs.group_by("status").len("pairs").sort("status")


def conflicting_facts(facts: pl.DataFrame) -> pl.DataFrame:
    """The same KPI concept and period tagged with different numbers in one filing."""
    concepts = list(ixbrl.KPI_CONCEPTS)
    return (
        facts.filter(pl.col("concept").is_in(concepts))
        .group_by("filing", "concept", "period_start", "period_end")
        .agg(pl.col("raw").unique().sort(), pl.col("value").n_unique().alias("n"))
        .filter(pl.col("n") > 1)
        .with_columns(pl.col("raw").list.join(" vs "))
        .drop("n")
        .sort("filing", "concept", "period_end")
    )


def identity_violations(gold: pl.DataFrame, facts: pl.DataFrame) -> pl.DataFrame:
    """Accounting sanity per filing: equity and cash fit in assets; both balance-sheet sides agree."""
    wide = gold.pivot(on="kpi", index=["filing", "fy_end"], values="value")
    rows = []
    for row in wide.iter_rows(named=True):
        assets, equity, cash = (
            row.get("total_assets"),
            row.get("equity"),
            row.get("cash"),
        )
        if assets is None:
            rows.append({"filing": row["filing"], "check": "no_total_assets", "detail": ""})
            continue
        if equity is not None and equity > assets:
            rows.append(
                {
                    "filing": row["filing"],
                    "check": "equity_gt_assets",
                    "detail": f"{equity:g} > {assets:g}",
                }
            )
        if cash is not None and cash > assets:
            rows.append(
                {
                    "filing": row["filing"],
                    "check": "cash_gt_assets",
                    "detail": f"{cash:g} > {assets:g}",
                }
            )
        side = facts.filter(
            (pl.col("filing") == row["filing"])
            & (pl.col("concept") == "ifrs-full:EquityAndLiabilities")
            & (pl.col("period_end") == row["fy_end"])
        )["value"].drop_nulls()
        if side.len() and not math.isclose(assets, side[0], rel_tol=REL_TOL):
            rows.append(
                {
                    "filing": row["filing"],
                    "check": "unbalanced",
                    "detail": f"{assets:g} != {side[0]:g}",
                }
            )
    return pl.DataFrame(rows, schema={"filing": pl.Utf8, "check": pl.Utf8, "detail": pl.Utf8})


def twin_disagreements(gold: pl.DataFrame) -> pl.DataFrame:
    """Reports filed more than once (language versions) whose gold KPIs differ."""
    return (
        gold.group_by("lei", "fy_end", "kpi")
        .agg(
            pl.col("value").n_unique().alias("n_values"),
            pl.col("filing").n_unique().alias("n_filings"),
        )
        .filter((pl.col("n_filings") > 1) & (pl.col("n_values") > 1))
        .sort("lei", "fy_end", "kpi")
    )


_NAME_CONCEPTS = {
    "ifrs-full:NameOfReportingEntityOrOtherMeansOfIdentification",
    "ifrs-full:NameOfParentEntity",
}


def entity_names(raw_dir: Path) -> dict[str, str]:
    """LEI -> company name as tagged in the reports (a placeholder like '-' is skipped)."""
    names: dict[str, str] = {}
    for path in sorted(raw_dir.glob("*.xhtml")):
        lei = str(parse_filing(path.stem)["lei"])
        if lei in names:
            continue
        root = etree.fromstring(path.read_bytes(), ixbrl._PARSER)
        for el in root.iter(f"{{{ixbrl.IX}}}nonNumeric"):
            name = " ".join("".join(el.itertext()).split())
            if el.get("name") in _NAME_CONCEPTS and re.search(r"[^\W\d_]", name):
                names[lei] = name
                break
    return names


_EUR = r"(?:eur\b|€)"
_UNIT_PATTERNS = (
    (6, rf"(?i)\bmio\.?\s*{_EUR}|\bmillionen\s+euro\b|{_EUR}\s*(?:mio\b|m\b|million)"),
    (
        3,
        rf"(?i)\bteur\b|\bt€|\btsd\.?\s*{_EUR}|\bkeur\b|{_EUR}\s*(?:'000|1[.,]000\b|thousand)",
    ),
    (
        0,
        rf"(?i)\bin\s+(?:{_EUR}|euro\b)(?!\s*(?:mio|m\b|million|'000|1[.,]000|thousand))",
    ),
)


def printed_scale(text: str) -> int | None:
    """Power of ten of the unit announced in a text ('in TEUR' -> 3); first mention wins."""
    hits = [(m.start(), exp) for exp, pat in _UNIT_PATTERNS if (m := re.search(pat, text))]
    return min(hits)[1] if hits else None


def fact_in_context(
    xhtml: bytes, concept: str, period_end: str
) -> dict[str, str | int | None] | None:
    """The dimension-free fact as printed: tag attributes, nearby text, unit announced around it."""
    root = etree.fromstring(xhtml, ixbrl._PARSER)
    contexts = ixbrl._contexts(root)
    for el in root.iter(f"{{{ixbrl.IX}}}nonFraction"):
        ctx = contexts.get(el.get("contextRef"), {})
        if el.get("name") != concept or ctx.get("has_dims") or ctx.get("period_end") != period_end:
            continue
        printed = "".join(el.itertext()).strip()
        around, unit = "", None
        for parent in el.iterancestors():
            if etree.QName(parent).localname in {"body", "html"}:
                break
            text = " ".join("".join(parent.itertext()).split())
            if not around and (etree.QName(parent).localname == "tr" or len(text) >= 40):
                at = max(text.find(printed), 0)
                around = text[max(at - 70, 0) : at + len(printed) + 40]
            unit = printed_scale(text)
            if unit is not None and around:
                break
        return {
            "printed": ("-" if el.get("sign") == "-" else "") + printed,
            "tag_scale": int(el.get("scale") or 0),
            "printed_scale": unit,
            "decimals": el.get("decimals") or "",
            "around": around,
        }
    return None


def scale_vs_print(raw_dir: Path) -> pl.DataFrame:
    """For every filing: scale tagged on total assets vs the unit printed around that number."""
    rows = []
    for path in sorted(raw_dir.glob("*.xhtml")):
        meta = parse_filing(path.stem)
        fact = fact_in_context(
            path.read_bytes(), _CONCEPT_BY_KPI["total_assets"], str(meta["fy_end"])
        )
        if fact is None:
            continue
        tag, shown = fact["tag_scale"], fact["printed_scale"]
        status = "unit_not_found" if shown is None else "ok" if tag == shown else "mismatch"
        rows.append(
            {
                "filing": path.stem,
                "tag_scale": tag,
                "printed_scale": shown,
                "status": status,
            }
        )
    return pl.DataFrame(
        rows,
        schema={
            "filing": pl.Utf8,
            "tag_scale": pl.Int64,
            "printed_scale": pl.Int64,
            "status": pl.Utf8,
        },
    )


def quarantine(pairs: pl.DataFrame, scales: pl.DataFrame, gold: pl.DataFrame) -> list[str]:
    """Filings whose gold must not be scored against: a proven scale error in any KPI."""
    keys = pairs.filter(pl.col("status") == "scale_mismatch").select("lei", "fy_end").unique()
    by_pair = gold.join(keys, on=["lei", "fy_end"])["filing"].to_list()
    by_print = scales.filter(pl.col("status") == "mismatch")["filing"].to_list()
    return sorted(set(by_pair) | set(by_print))


# Picks that stress one failure mode each; the person doing the check compares them with the PDF.
HAND_CHECKS = (
    ("scale", "D1HEB8VEU6D9M8ZUXG17-2020-12-31-ESEF-AT-0", "total_assets"),
    ("sign", "529900ARBU9SBJFNX419-2020-12-31-ESEF-AT-0", "net_income"),
    (
        "year (FY ends September)",
        "529900N8OA1GVSR5R946-2022-09-30-ESEF-AT-1",
        "revenue",
    ),
    ("restatement", "549300TAT2TADBW0WR70-2020-12-31-ESEF-AT-0", "revenue"),
)


def hand_check_rows(raw_dir: Path, gold: pl.DataFrame, names: dict[str, str]) -> pl.DataFrame:
    rows = []
    for risk, stem, kpi in HAND_CHECKS:
        meta = parse_filing(stem)
        value = gold.filter((pl.col("filing") == stem) & (pl.col("kpi") == kpi))["value"][0]
        fact = fact_in_context(
            (raw_dir / f"{stem}.xhtml").read_bytes(),
            _CONCEPT_BY_KPI[kpi],
            str(meta["fy_end"]),
        )
        fact = fact or {}
        rows.append(
            {
                "risk": risk,
                "company": names.get(str(meta["lei"]), str(meta["lei"])),
                "file": stem,
                "kpi": kpi,
                "gold_eur": f"{value:,.0f}",
                "printed": fact.get("printed"),
                "tag_scale": fact.get("tag_scale"),
                "printed_scale": fact.get("printed_scale"),
                "text_around": fact.get("around"),
                "matches_pdf": "[ ]",
            }
        )
    return pl.DataFrame(rows)


def build_blocks(raw_dir: Path, interim_dir: Path, gold_dir: Path) -> dict[str, str]:
    """All generated Markdown blocks of docs/gold_quality.md."""
    gold = load_gold(gold_dir)
    facts = load_facts(interim_dir)
    names = entity_names(raw_dir)
    pairs = cross_year(gold, facts)
    scales = scale_vs_print(raw_dir)
    blocked = quarantine(pairs, scales, gold)

    def named(df: pl.DataFrame) -> pl.DataFrame:
        company = [names.get(lei, lei) for lei in df["lei"]]
        return df.with_columns(pl.Series("company", company, dtype=pl.Utf8)).drop("lei")

    n_values = gold.filter(pl.col("value").is_not_null()).height
    checked = pairs.select("lei", "fy_end").unique().height
    n_reports = gold.select("lei", "fy_end").unique().height
    n_twins = gold["filing"].n_unique() - n_reports
    size = (
        f"- Filings: {gold['filing'].n_unique()}\n"
        f"- Distinct reports (company + year): {n_reports}\n"
        f"- Companies: {gold['lei'].n_unique()}\n"
        f"- Extra filings of a report already counted (language versions, re-filings): {n_twins}\n"
        f"- KPI values in gold: {n_values}\n"
        f"- Reports with a following-year filing to compare against: {checked}/{n_reports}\n"
        f"- Filings quarantined: {len(blocked)}"
    )
    non_ok = named(pairs.filter(pl.col("status") != "ok")).select(
        "company", "fy_end", "kpi", "value", "comparative", "next_fy_end", "status"
    )
    aliases = named(missing_with_aliases(gold, facts)).with_columns(
        pl.col("similar_concepts").str.split(", ").list.head(3).list.join(", ")
    )
    missing = (
        aliases.group_by("company", "kpi")
        .agg(
            pl.col("fy_end").sort().str.slice(0, 4).str.join(", ").alias("years"),
            pl.col("similar_concepts").last().alias("similar_concepts (first 3)"),
        )
        .sort("company", "kpi")
    )
    scale_summary = scales.group_by("status").len("filings").sort("status")
    problems = scales.filter(pl.col("status") != "ok")
    return {
        "size": size,
        "coverage": markdown_table(coverage(gold)),
        "missing": markdown_table(missing),
        "cross_summary": markdown_table(cross_year_summary(pairs)),
        "cross_detail": markdown_table(non_ok),
        "scale_summary": markdown_table(scale_summary),
        "scale_detail": markdown_table(problems) if problems.height else "None.",
        "conflicts": markdown_table(conflicting_facts(facts)),
        "identities": _table_or_none(identity_violations(gold, facts)),
        "twins": _table_or_none(twin_disagreements(gold)),
        "quarantine": "\n".join(f"- `{f}`" for f in blocked) or "None.",
        "hand_checks": markdown_table(hand_check_rows(raw_dir, gold, names)),
    }


def _table_or_none(df: pl.DataFrame) -> str:
    return markdown_table(df) if df.height else "None."
