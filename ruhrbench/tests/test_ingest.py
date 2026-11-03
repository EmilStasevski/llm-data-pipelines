import polars as pl
import pymupdf as fitz

from ruhrbench.ingest import ixbrl, pdf_baseline, prices

XHTML = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"
      xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
      xmlns:xbrli="http://www.xbrl.org/2003/instance"
      xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
      xmlns:ixt="http://www.xbrl.org/inlineXBRL/transformation/2020-02-12">
<body>
<ix:header><ix:resources>
  <xbrli:context id="c1"><xbrli:entity/><xbrli:period>
    <xbrli:startDate>2024-01-01</xbrli:startDate><xbrli:endDate>2024-12-31</xbrli:endDate>
  </xbrli:period></xbrli:context>
  <xbrli:context id="c2"><xbrli:entity><xbrli:segment>
    <xbrldi:explicitMember dimension="x:Seg">x:A</xbrldi:explicitMember>
  </xbrli:segment></xbrli:entity><xbrli:period>
    <xbrli:startDate>2024-01-01</xbrli:startDate><xbrli:endDate>2024-12-31</xbrli:endDate>
  </xbrli:period></xbrli:context>
  <xbrli:context id="c3"><xbrli:entity/><xbrli:period>
    <xbrli:instant>2024-12-31</xbrli:instant></xbrli:period></xbrli:context>
</ix:resources></ix:header>
<h1>Konzernlagebericht</h1>
<p>Der Umsatz stieg auf
  <ix:nonFraction name="ifrs-full:Revenue" contextRef="c1" unitRef="EUR" decimals="-5"
     scale="6" format="ixt:num-comma-decimal">34.918,5</ix:nonFraction> Mio. EUR.</p>
<p>Segment: <ix:nonFraction name="ifrs-full:Revenue" contextRef="c2" unitRef="EUR"
     scale="6" format="ixt:num-comma-decimal">1.200,0</ix:nonFraction></p>
<p>Verlust: <ix:nonFraction name="ifrs-full:ProfitLoss" contextRef="c1" unitRef="EUR"
     scale="6" sign="-" format="ixt:num-comma-decimal">532,0</ix:nonFraction></p>
<p>Bilanzsumme: <ix:nonFraction name="ifrs-full:Assets" contextRef="c3" unitRef="EUR"
     scale="3" format="ixt:num-dot-decimal">1,000</ix:nonFraction></p>
</body></html>"""


def test_ixbrl_facts_and_gold():
    facts = ixbrl.parse_facts(XHTML.encode())
    assert facts.height == 4
    kpis = ixbrl.gold_kpis(facts)
    assert kpis["revenue"] == 34_918_500_000  # dimension-free fact wins, German decimals, scale 6
    assert kpis["net_income"] == -532_000_000  # sign="-"
    assert kpis["total_assets"] == 1_000_000  # dot-decimal, scale 3


def test_xhtml_to_text_skips_header_and_splits_blocks():
    text = ixbrl.xhtml_to_text(XHTML.encode())
    lines = text.splitlines()
    assert lines[0] == "Konzernlagebericht"
    assert lines[1] == "Der Umsatz stieg auf 34.918,5 Mio. EUR."
    assert "2024-12-31" not in text  # ix:header content excluded


def test_prices_parse_payload_berlin_tz():
    # 2024-06-01 00:00 UTC == 02:00 CEST
    df = prices.parse_payload({"unix_seconds": [1717200000, 1717203600], "price": [85.5, None]})
    assert df.height == 1  # null dropped
    assert df["ts"].dtype == pl.Datetime("us", "Europe/Berlin") or str(df["ts"].dtype).startswith(
        "Datetime"
    )
    assert df["ts"][0].hour == 2
    assert df["eur_per_mwh"][0] == 85.5


def test_dehyphenate_german():
    assert pdf_baseline.dehyphenate("Nachhal-\ntigkeitsbericht") == "Nachhaltigkeitsbericht"
    assert pdf_baseline.dehyphenate("E-\nMobilität") == "E-\nMobilität"  # uppercase: keep


def test_pdf_roundtrip(tmp_path):
    pdf = tmp_path / "doc.pdf"
    d = fitz.open()
    p = d.new_page()
    p.insert_text((72, 72), "Größe der Kreislaufwirtschaft in Nordrhein-Westfalen")
    d.new_page().insert_text((72, 72), "Seite zwei")
    d.save(pdf)
    d.close()
    out = tmp_path / "pages.parquet"
    stats = pdf_baseline.parse_dir(tmp_path, out)
    df = pl.read_parquet(out)
    assert stats["docs"] == 1 and stats["pages"] == 2
    assert "Kreislaufwirtschaft" in df["text"][0]
