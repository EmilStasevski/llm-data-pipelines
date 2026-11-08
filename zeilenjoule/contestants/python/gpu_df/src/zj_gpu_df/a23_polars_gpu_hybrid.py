"""Track A `a.full` contestant a23_polars_gpu_hybrid (Extra): an explicit, hand-split CPU/GPU plan.

a12 shows that a10's plan cannot run on the Polars GPU engine (NFC normalisation, `extract_many`,
`list.eval`, ... are unsupported). An expert who still wants the GPU splits the plan by hand:

1. CPU (Polars streaming engine): scan + §1 normalisation (NFC has no GPU kernel) + §6 abbreviation
   expansion (`list.eval`).
2. GPU (`GPUEngine(raise_on_fail=True)`): §2–5 field parsing, lower/fold, Materialnummer, safety
   triggers, Werk lookups, doc_text. Four expressions are rewritten into GPU-supported equivalents
   (`GpuRules`, same results, checked by the golden tests): per-group `extract` instead of
   `extract_groups` (cudf-polars 26.10 crashes on struct fields with nulls), `to_date(cache=False)`,
   literal `replace_many` instead of a regex `replace_all`, explicit case classes instead of `(?i)`.
3. CPU: §7 longest Komponente (`extract_many`) + join + sort.

Every stage boundary is a host↔device copy. The split is explicit, not a fallback: the GPU stage still
fails loudly on any unsupported node (rule 6), and the result records which stages ran where.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

DF_SRC = Path(__file__).resolve().parents[3] / "df" / "src"

# Python's re.IGNORECASE equivalents of the letters in the duration units (the spec reference is
# Python `re`); test_a23 asserts this table against re.IGNORECASE over all of Unicode.
CASE_CLASSES = {
    "a": "aA", "c": "cC", "d": "dD", "e": "eE", "h": "hH", "i": "iI\u0130\u0131",
    "m": "mM", "n": "nN", "s": "sS\u017f", "t": "tT", "u": "uU",
}  # fmt: skip


def ci(word: str) -> str:
    """Case-insensitive regex for an ASCII word without `(?i)` (unsupported by the GPU regex engine)."""
    out: list[str] = []
    for ch in word:
        if ch in CASE_CLASSES:
            out.append(f"[{CASE_CLASSES[ch]}]")
        elif ch == ".":
            out.append(r"\.")
        else:
            out.append(ch)
    return "".join(out)


UNITS = ["h", "std.", "stunde", "stunden", "min", "minuten"]  # same alternation order as RE_DURATION
RE_DURATION_GPU = (
    rf"^(?:{ci('ca.')} ?)?(?:([0-9]+(?:,[0-9]+)?) ?- ?)?"
    rf"([0-9]+(?:,[0-9]+)?) ?({'|'.join(ci(u) for u in UNITS)})$"
)


def gpu_rules_class() -> Any:
    """Build GpuRules lazily: polars/zj_df are imported only after DF_SRC is on sys.path."""
    import polars as pl  # noqa: PLC0415

    from zj_df.track_a import polars_impl as P  # noqa: N812, PLC0415

    def pad2(e: pl.Expr) -> pl.Expr:  # str.zfill raises on non-ASCII columns on the GPU
        return pl.when(e.str.len_chars() == 1).then(pl.lit("0") + e).otherwise(e)

    class GpuRules(P.PolarsRules):
        def equipment(self, e: pl.Expr) -> pl.Expr:
            g = [e.str.extract(P.RE_EQUIPMENT, i) for i in (1, 2, 3)]
            return pl.concat_str([g[0], pl.lit("-"), g[1], g[2].fill_null("")])

        def date(self, e: pl.Expr) -> pl.Expr:
            d, m, y = (e.str.extract(P.RE_DATE_DE, i) for i in (1, 2, 3))
            y = pl.when(y.str.len_chars() == 2).then(pl.lit("20") + y).otherwise(y)
            s_de = pl.concat_str([y, pl.lit("-"), pad2(m), pl.lit("-"), pad2(d)])
            iso = [e.str.extract(P.RE_DATE_ISO, i) for i in (1, 2, 3)]
            s_iso = pl.concat_str([iso[0], pl.lit("-"), iso[1], pl.lit("-"), iso[2]])
            return pl.coalesce(s_de, s_iso).str.to_date("%Y-%m-%d", strict=False, cache=False)

        def eur(self, e: pl.Expr) -> pl.Expr:
            sign, whole, frac = (e.str.extract(P.RE_NUMBER, i) for i in (1, 2, 3))
            whole = whole.str.replace_many([".", " "], ["", ""])
            parts = [sign.fill_null(""), whole, pl.lit("."), frac.fill_null("0")]
            return pl.concat_str(parts).cast(pl.Float64)

        def minutes(self, e: pl.Expr) -> pl.Expr:
            value = e.str.extract(RE_DURATION_GPU, 2)
            unit = e.str.extract(RE_DURATION_GPU, 3).str.to_lowercase()
            frac = value.str.extract(r",([0-9]+)$").fill_null("")
            num = pl.concat_str([value.str.extract(r"^([0-9]+)"), frac]).cast(pl.Int64, strict=False)
            den = pl.lit(10, pl.Int64).pow(frac.str.len_chars()).cast(pl.Int64)
            factor = pl.when(unit.is_in(P.HOURS)).then(pl.lit(60, pl.Int64)).otherwise(pl.lit(1, pl.Int64))
            return ((2 * num * factor + den) // (2 * den)).cast(pl.Int32)

        # --- the three stages ---------------------------------------------------------------
        def cpu_normalise(self, lf: pl.LazyFrame) -> pl.LazyFrame:
            n = self.norm
            raw = ["meldung_id", "werk", "anlage", "equipment_nr", "meldungsdatum", "schicht",
                   "prioritaet", "stillstand", "kosten", "status"]  # fmt: skip
            text_norm = self.expand(n(pl.col("meldungstext")))
            return lf.select(*[n(pl.col(c)).alias(c) for c in raw], text_norm=text_norm)

        def gpu_fields(self, lf: pl.LazyFrame) -> pl.LazyFrame:
            return (
                lf.with_columns(
                    equipment_nr=self.equipment(pl.col("equipment_nr")),
                    datum=self.date(pl.col("meldungsdatum")),
                    prioritaet=self.prio(pl.col("prioritaet")),
                    stillstand_min=self.minutes(pl.col("stillstand")),
                    kosten_eur=self.eur(pl.col("kosten")),
                    text_lower=pl.col("text_norm").str.to_lowercase(),
                )
                .with_columns(text_fold=self.fold(pl.col("text_lower")))
                .with_columns(
                    materialnr=self.materialnr(pl.col("text_norm")),
                    sicherheitsrelevant_rule=self.safety(pl.col("text_fold")),
                    regierungsbezirk=pl.col("werk").replace_strict(self.werke, self.rb, default=None),
                    nuts2=pl.col("werk").replace_strict(self.werke, self.nuts, default=None),
                    doc_text=self.doc_text(),
                )
                .drop("meldungsdatum", "stillstand", "kosten")
            )

        def cpu_komponente(self, lf: pl.LazyFrame) -> pl.LazyFrame:
            return self.komponente(lf).select(P.OUTPUT_COLUMNS).sort("meldung_id")

    return GpuRules


def main(argv: list[str] | None = None) -> int:
    sys.path.insert(0, str(DF_SRC))
    import polars as pl  # noqa: PLC0415

    from zj_df.track_a.common import load_lexicon, parse_args  # noqa: PLC0415
    from zj_df.track_a.polars_impl import SCAN_OPTIONS, transcode_to_utf8  # noqa: PLC0415
    from zjmark import result, stage  # noqa: PLC0415

    a = parse_args(argv, "a23_polars_gpu_hybrid")
    rules = gpu_rules_class()(load_lexicon(a.lexicon))
    gpu = pl.GPUEngine(device=0, raise_on_fail=True)
    utf8 = a.output.with_name(a.output.stem + ".input.utf8.csv")
    with stage("transcode"):  # scan_csv reads UTF-8 only
        transcode_to_utf8(a.input, utf8)
    with stage("cpu_normalise"):
        normed = rules.cpu_normalise(pl.scan_csv(utf8, **SCAN_OPTIONS)).collect(engine="streaming")
    utf8.unlink()
    with stage("gpu_fields"):
        fields = rules.gpu_fields(normed.lazy()).collect(engine=gpu)
    del normed
    with stage("cpu_komponente"):
        out = rules.cpu_komponente(fields.lazy()).collect(engine="streaming")
    del fields
    with stage("write"):
        out.write_parquet(a.output, compression="zstd")
    import cudf_polars  # noqa: PLC0415

    result(
        rows=out.height,
        notes="explicit CPU/GPU split: CPU normalise+expand → GPU fields → CPU komponente",
        extra={"engine": "gpu+cpu hybrid (explicit split, GPU stage raise_on_fail=True)",
               "cpu_stages": ["cpu_normalise", "cpu_komponente"], "gpu_stages": ["gpu_fields"],
               "fallback_ops": [], "polars": pl.__version__, "cudf_polars": cudf_polars.__version__,
               "based_on": "a10_polars_lazy"},
    )  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
