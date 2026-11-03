import polars as pl
from test_ingest import XHTML

from ruhrbench.ingest import process
from ruhrbench.ingest.lang import guess_lang


def test_guess_lang():
    de = "Der Konzern wird auch im nächsten Jahr nicht auf die Ziele von dem Plan verzichten und ist bereit."
    en = "The group is on track and the results are for the year with that plan, which has been good."
    assert guess_lang(de) == "de"
    assert guess_lang(en) == "en"
    assert guess_lang("Konzern") == "unk"


def test_process_report_writes_outputs_and_manifest_row(tmp_path):
    rep = tmp_path / "ABC-2024-12-31-ESEF-AT-0.xhtml"
    rep.write_text(XHTML, encoding="utf-8")
    row = process.process_report(rep, tmp_path / "interim", tmp_path / "gold")
    assert row["n_facts"] == 4 and row["n_kpis"] == 3
    assert row["revenue"] == 34_918_500_000
    assert (tmp_path / "interim" / f"{rep.stem}.txt").exists()
    assert (tmp_path / "gold" / f"{rep.stem}.kpis.json").exists()
    df = pl.DataFrame([row], schema=process.manifest_schema())
    assert df["equity"][0] is None  # KPI absent -> null, schema still holds
