import json

import polars as pl

from ruhrbench.eval import gold_quality as gq
from ruhrbench.report.blocks import markdown_table, update_blocks

A = "A" * 20
Y20 = f"{A}-2020-12-31-ESEF-AT-0"
Y20B = f"{A}-2020-12-31-ESEF-AT-1"
Y21 = f"{A}-2021-12-31-ESEF-AT-0"


def _gold(tmp_path, **filings):
    for stem, values in filings.items():
        (tmp_path / f"{stem}.kpis.json").write_text(json.dumps(values))
    return gq.load_gold(tmp_path)


def _facts(rows):
    return pl.DataFrame(
        [
            {
                "filing": stem,
                **{k: gq.parse_filing(stem)[k] for k in ("lei", "fy_end")},
                "concept": concept,
                "period_start": period,
                "period_end": period,
                "value": value,
                "raw": str(value),
            }
            for stem, concept, period, value in rows
        ],
        schema=gq._FACT_SCHEMA,
    )


def test_parse_filing_extracts_lei_year_and_sequence():
    assert gq.parse_filing(Y20B) == {
        "filing": Y20B,
        "lei": A,
        "fy_end": "2020-12-31",
        "seq": 1,
    }


def test_coverage_counts_language_twins_as_one_report(tmp_path):
    gold = _gold(
        tmp_path,
        **{Y20: {"revenue": 1.0}, Y20B: {"revenue": 1.0}, Y21: {"equity": 2.0}},
    )
    table = gq.coverage(gold).filter(pl.col("kpi") == "revenue").row(0, named=True)
    assert table == {
        "kpi": "revenue",
        "filings": "2/3",
        "reports": "1/2",
        "report_coverage": "50%",
    }


def test_classify_pair():
    assert gq.classify_pair(100.0, 100.2) == "ok"
    assert gq.classify_pair(118_510_000.0, 118_510_000_000.0) == "scale_mismatch"
    assert gq.classify_pair(1000.0, -1000.0) == "sign_flip"
    assert gq.classify_pair(100.0, 130.0) == "differs"
    assert gq.classify_pair(95_000.0, 944_000_000.0) == "differs"  # near 10^4, different digits
    assert gq.classify_pair(0.0, 0.0) == "ok"


def test_cross_year_flags_a_scale_error_against_next_years_comparative(tmp_path):
    gold = _gold(
        tmp_path,
        **{
            Y20: {"total_assets": 118_510_000.0},
            Y21: {"total_assets": 118_404_000_000.0},
        },
    )
    facts = _facts(
        [
            (Y21, "ifrs-full:Assets", "2021-12-31", 118_404_000_000.0),
            (Y21, "ifrs-full:Assets", "2020-12-31", 118_510_000_000.0),
        ]
    )
    pairs = gq.cross_year(gold, facts)
    assert pairs.select("fy_end", "next_fy_end", "status").rows() == [
        ("2020-12-31", "2021-12-31", "scale_mismatch")
    ]


def test_identity_violations(tmp_path):
    gold = _gold(tmp_path, **{Y20: {"total_assets": 10.0, "equity": 20.0, "cash": 1.0}})
    facts = _facts([(Y20, "ifrs-full:EquityAndLiabilities", "2020-12-31", 12.0)])
    assert gq.identity_violations(gold, facts)["check"].to_list() == [
        "equity_gt_assets",
        "unbalanced",
    ]


def test_conflicting_facts_reports_different_values_for_one_period():
    facts = _facts(
        [
            (Y20, "ifrs-full:Equity", "2020-12-31", 247.487),
            (Y20, "ifrs-full:Equity", "2020-12-31", 247.486),
            (Y20, "ifrs-full:Assets", "2020-12-31", 5.0),
            (Y20, "ifrs-full:Assets", "2020-12-31", 5.0),
        ]
    )
    assert gq.conflicting_facts(facts)["concept"].to_list() == ["ifrs-full:Equity"]


def test_missing_with_aliases_lists_similar_tagged_concepts(tmp_path):
    gold = _gold(tmp_path, **{Y20: {"equity": 1.0}})
    facts = _facts(
        [
            (Y20, "abc:TotalRevenueNet", "2020-12-31", 9.0),
            (Y20, "abc:Other", "2020-12-31", 9.0),
        ]
    )
    row = gq.missing_with_aliases(gold, facts).filter(pl.col("kpi") == "revenue").row(0, named=True)
    assert row["similar_concepts"] == "abc:TotalRevenueNet"


def test_fact_in_context_returns_printed_row_and_scale():
    xhtml = b"""<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
      xmlns:xbrli="http://www.xbrl.org/2003/instance"><body>
      <xbrli:context id="c"><xbrli:period><xbrli:instant>2020-12-31</xbrli:instant></xbrli:period></xbrli:context>
      <table><tr><td>Bilanzsumme</td><td><ix:nonFraction name="ifrs-full:Assets" contextRef="c" scale="3"
      decimals="-3" sign="-">1.234</ix:nonFraction></td></tr></table></body></html>"""
    assert gq.fact_in_context(xhtml, "ifrs-full:Assets", "2020-12-31") == {
        "printed": "-1.234",
        "tag_scale": 3,
        "printed_scale": None,
        "decimals": "-3",
        "around": "Bilanzsumme1.234",
    }


def test_printed_scale_reads_german_and_english_units():
    assert gq.printed_scale("Bilanz (Mio €) AKTIVA") == 6
    assert gq.printed_scale("in TEUR Erläuterungen") == 3
    assert gq.printed_scale("EUR million") == 6
    assert gq.printed_scale("EUR thousand") == 3
    assert gq.printed_scale("in EUR") == 0
    assert gq.printed_scale("Konzernbilanz in EUR Mio. Anhangsangabe") == 6
    assert gq.printed_scale("ASSETS EUR 1,000 EUR 1,000") == 3
    assert gq.printed_scale("Bilanzsumme") is None


def test_quarantine_blocks_every_filing_of_a_report_with_a_scale_error(tmp_path):
    gold = _gold(
        tmp_path,
        **{
            Y20: {"total_assets": 1.0},
            Y20B: {"total_assets": 1.0},
            Y21: {"total_assets": 5.0},
        },
    )
    pairs = pl.DataFrame({"lei": [A], "fy_end": ["2020-12-31"], "status": ["scale_mismatch"]})
    scales = pl.DataFrame({"filing": [Y21], "status": ["ok"]})
    assert gq.quarantine(pairs, scales, gold) == [Y20, Y20B]


def test_update_blocks_replaces_only_marked_text(tmp_path):
    doc = tmp_path / "d.md"
    doc.write_text("hand\n<!-- generated:t -->\nold\n<!-- /generated -->\nprose\n")
    update_blocks(doc, {"t": markdown_table(pl.DataFrame({"a": [1]}))})
    assert (
        doc.read_text()
        == "hand\n<!-- generated:t -->\n| a |\n|---|\n| 1 |\n<!-- /generated -->\nprose\n"
    )


def test_update_blocks_fails_on_unknown_block(tmp_path):
    doc = tmp_path / "d.md"
    doc.write_text("nothing here\n")
    try:
        update_blocks(doc, {"missing": "x"})
    except ValueError:
        return
    raise AssertionError("expected ValueError")
