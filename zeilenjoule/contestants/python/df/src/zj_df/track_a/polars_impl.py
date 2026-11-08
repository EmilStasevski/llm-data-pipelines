"""Polars expression implementation of spec/track_a.md (shared by a09 eager / a10 lazy / a11 streaming).

Written the way a Polars expert would: native string kernels only, no Python UDFs. Notable choices:
- durations use exact integer arithmetic (Expr.round defaults to half_to_even and floats drift at .5)
- abbreviations: split on " " → list.eval with a prefix/core/suffix regex → replace_strict → join
- longest component: extract_many(overlapping=True) → explode → (length desc, first position asc) → join
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, TypeVar

import polars as pl

Frame = TypeVar("Frame", pl.DataFrame, pl.LazyFrame)

WS_RUN = r"[\t\n\x0B\x0C\r \x{A0}]+"
RE_EQUIPMENT = r"^([A-Z])[ -]?([0-9]{4})(-[A-Z])?$"
RE_DATE_DE = r"^([0-9]{1,2})\.([0-9]{1,2})\.([0-9]{2}|[0-9]{4})$"
RE_DATE_ISO = r"^([0-9]{4})-([0-9]{2})-([0-9]{2})$"
RE_NUMBER = r"^(-)?([0-9]{1,3}(?:[. ][0-9]{3})+|[0-9]+)(?:,([0-9]+))?(?: ?(?:€|EUR))?$"
RE_DURATION = (
    r"(?i)^(?:ca\. ?)?(?:([0-9]+(?:,[0-9]+)?) ?- ?)?"  # optional "ca." and lower range bound
    r"([0-9]+(?:,[0-9]+)?) ?(h|std\.|stunde|stunden|min|minuten)$"  # value (upper bound) + unit
)
RE_MATNR = r"(?:^|[^0-9])(10[0-9]{6})(?:[^0-9]|$)"
HOURS = ["h", "std.", "stunde", "stunden"]


OUTPUT_COLUMNS = [
    "meldung_id",
    "werk",
    "anlage",
    "equipment_nr",
    "datum",
    "schicht",
    "prioritaet",
    "stillstand_min",
    "kosten_eur",
    "status",
    "text_norm",
    "text_lower",
    "text_fold",
    "materialnr",
    "sicherheitsrelevant_rule",
    "komponente_rule",
    "regierungsbezirk",
    "nuts2",
    "doc_text",
]


def _g(struct: pl.Expr, i: int) -> pl.Expr:
    return struct.struct.field(str(i))


class PolarsRules:
    def __init__(self, lex: dict[str, Any]) -> None:
        moji = sorted(lex["mojibake"].items(), key=lambda kv: -len(kv[0]))
        self.moji_keys, self.moji_vals = [k for k, _ in moji], [v for _, v in moji]
        self.abbr_keys, self.abbr_vals = list(lex["abbreviations"]), list(lex["abbreviations"].values())
        lead = "".join("\\" + c if c in "[]^-\\" else c for c in lex["strip_leading"])
        trail = "".join("\\" + c if c in "[]^-\\" else c for c in lex["strip_trailing"])
        self.token_re = f"^([{lead}]*)(.*?)([{trail}]*)$"
        self.fold_keys, self.fold_vals = list(lex["fold"]), list(lex["fold"].values())
        self.triggers: list[str] = lex["safety_triggers_fold"]
        self.forms: list[str] = list(lex["komponenten_fold"])
        self.form_names: list[str] = list(lex["komponenten_fold"].values())
        self.werke = list(lex["werke"])
        self.rb = [v["regierungsbezirk"] for v in lex["werke"].values()]
        self.nuts = [v["nuts2"] for v in lex["werke"].values()]

    # --- §1 ---------------------------------------------------------------------------------
    def norm(self, e: pl.Expr) -> pl.Expr:
        return (
            e.str.replace_many(self.moji_keys, self.moji_vals)
            .str.normalize("NFC")
            .str.replace_all(WS_RUN, " ")
            .str.strip_chars(" ")
        )

    # --- §2–5 (inputs are already normalised) ----------------------------------------------
    def equipment(self, e: pl.Expr) -> pl.Expr:
        g = e.str.extract_groups(RE_EQUIPMENT)
        return pl.concat_str([_g(g, 1), pl.lit("-"), _g(g, 2), _g(g, 3).fill_null("")])

    def date(self, e: pl.Expr) -> pl.Expr:
        de, iso = e.str.extract_groups(RE_DATE_DE), e.str.extract_groups(RE_DATE_ISO)
        y = pl.when(_g(de, 3).str.len_chars() == 2).then(pl.lit("20") + _g(de, 3)).otherwise(_g(de, 3))
        s_de = pl.concat_str([y, pl.lit("-"), _g(de, 2).str.zfill(2), pl.lit("-"), _g(de, 1).str.zfill(2)])
        s_iso = pl.concat_str([_g(iso, 1), pl.lit("-"), _g(iso, 2), pl.lit("-"), _g(iso, 3)])
        return pl.coalesce(s_de, s_iso).str.to_date("%Y-%m-%d", strict=False)  # impossible dates → null

    def prio(self, e: pl.Expr) -> pl.Expr:
        n = pl.when(e.str.contains(r"^[0-9]+$")).then(e.cast(pl.Int64, strict=False))
        return pl.when(n.is_between(1, 4)).then(n).cast(pl.Int8)

    def eur(self, e: pl.Expr) -> pl.Expr:
        g = e.str.extract_groups(RE_NUMBER)
        s = pl.concat_str(
            [
                _g(g, 1).fill_null(""),
                _g(g, 2).str.replace_all(r"[. ]", ""),
                pl.lit("."),
                _g(g, 3).fill_null("0"),
            ]
        )
        return s.cast(pl.Float64)

    def minutes(self, e: pl.Expr) -> pl.Expr:
        g = e.str.extract_groups(RE_DURATION)
        value, unit = _g(g, 2), _g(g, 3).str.to_lowercase()
        frac = value.str.extract(r",([0-9]+)$").fill_null("")
        num = pl.concat_str([value.str.extract(r"^([0-9]+)"), frac]).cast(pl.Int64, strict=False)
        den = pl.lit(10, pl.Int64).pow(frac.str.len_chars()).cast(pl.Int64)  # 10^k, exact for k ≤ 18
        factor = pl.when(unit.is_in(HOURS)).then(pl.lit(60, pl.Int64)).otherwise(pl.lit(1, pl.Int64))
        return ((2 * num * factor + den) // (2 * den)).cast(pl.Int32)  # round half up, exact

    # --- §6 ---------------------------------------------------------------------------------
    def expand(self, e: pl.Expr) -> pl.Expr:
        tok = pl.element()
        g = tok.str.extract_groups(self.token_re)
        rep = _g(g, 2).str.to_lowercase().replace_strict(self.abbr_keys, self.abbr_vals, default=None)
        new = pl.when(rep.is_not_null()).then(pl.concat_str([_g(g, 1), rep, _g(g, 3)])).otherwise(tok)
        return e.str.split(" ").list.eval(new).list.join(" ")

    def fold(self, lower: pl.Expr) -> pl.Expr:
        return lower.str.replace_many(self.fold_keys, self.fold_vals)

    def materialnr(self, text_norm: pl.Expr) -> pl.Expr:
        return text_norm.str.extract(RE_MATNR, 1)

    def safety(self, text_fold: pl.Expr) -> pl.Expr:
        return text_fold.str.contains_any(self.triggers)

    # --- §7: longest dictionary match, ties → earliest position ------------------------------
    def _best_form(self, lf: pl.LazyFrame) -> pl.LazyFrame:
        return (
            lf.select(
                "meldung_id",
                "text_fold",
                form=pl.col("text_fold").str.extract_many(self.forms, overlapping=True),
            )
            .explode("form")
            .drop_nulls("form")
            .with_columns(
                length=pl.col("form").str.len_chars(),
                pos=pl.col("text_fold").str.find(pl.col("form"), literal=True),
            )
            .group_by("meldung_id")
            .agg(pl.col("form").sort_by(["length", "pos"], descending=[True, False]).first())
            .select("meldung_id", komponente_rule=pl.col("form").replace_strict(self.forms, self.form_names))
        )

    def komponente(self, frame: Frame) -> Frame:
        if isinstance(frame, pl.LazyFrame):
            return frame.join(self._best_form(frame), on="meldung_id", how="left")
        # eager: Polars runs eager frame ops through the same engine; collect the small sub-result
        return frame.join(self._best_form(frame.lazy()).collect(), on="meldung_id", how="left")

    # --- whole pipeline ---------------------------------------------------------------------
    def transform(self, frame: Frame) -> Frame:
        n = self.norm
        text_norm = self.expand(n(pl.col("meldungstext")))
        frame = frame.with_columns(
            meldung_id=n(pl.col("meldung_id")),
            werk=n(pl.col("werk")),
            anlage=n(pl.col("anlage")),
            equipment_nr=self.equipment(n(pl.col("equipment_nr"))),
            datum=self.date(n(pl.col("meldungsdatum"))),
            schicht=n(pl.col("schicht")),
            prioritaet=self.prio(n(pl.col("prioritaet"))),
            stillstand_min=self.minutes(n(pl.col("stillstand"))),
            kosten_eur=self.eur(n(pl.col("kosten"))),
            status=n(pl.col("status")),
            text_norm=text_norm,
        ).with_columns(text_lower=pl.col("text_norm").str.to_lowercase())
        frame = frame.with_columns(text_fold=self.fold(pl.col("text_lower"))).with_columns(
            materialnr=self.materialnr(pl.col("text_norm")),
            sicherheitsrelevant_rule=self.safety(pl.col("text_fold")),
            regierungsbezirk=pl.col("werk").replace_strict(self.werke, self.rb, default=None),
            nuts2=pl.col("werk").replace_strict(self.werke, self.nuts, default=None),
        )
        frame = self.komponente(frame)
        return frame.with_columns(doc_text=self.doc_text()).select(OUTPUT_COLUMNS).sort("meldung_id")

    @staticmethod
    def doc_text() -> pl.Expr:
        """§8 doc_text from the output columns werk, anlage, equipment_nr, datum, prioritaet, text_norm."""

        def part(label: str, v: pl.Expr) -> pl.Expr:
            return pl.when(v.is_not_null() & (v != "")).then(pl.lit(f"{label}: ") + v)

        return pl.concat_str(
            [
                part("Werk", pl.col("werk")),
                part("Anlage", pl.col("anlage")),
                part("Equipment", pl.col("equipment_nr")),
                part("Datum", pl.col("datum").dt.to_string("%Y-%m-%d")),
                part("Priorität", pl.col("prioritaet").cast(pl.String)),
                part("Meldung", pl.col("text_norm")),
            ],
            separator=" | ",
            ignore_nulls=True,
        ).fill_null("")


CSV_OPTIONS: dict[str, Any] = {
    "separator": ";",
    "quote_char": '"',
    "encoding": "windows-1252",  # native in Polars 2.1 (latin1 would mis-decode € = 0x80)
    "infer_schema": False,  # all columns as strings, exactly as exported
    "empty_string_is_null": False,  # spec: empty field = empty string
}


def transcode_to_utf8(src: Path, dst: Path, chunk_chars: int = 1 << 20) -> None:
    """cp1252 → UTF-8, streamed in chunks. Needed because Polars 2.1 scan_csv only reads UTF-8
    (read_csv decodes windows-1252 natively, the lazy/streaming scanners do not)."""
    with src.open(encoding="cp1252", newline="") as fin, dst.open("w", encoding="utf-8", newline="") as fout:
        while chunk := fin.read(chunk_chars):
            fout.write(chunk)


SCAN_OPTIONS: dict[str, Any] = {
    k: v for k, v in CSV_OPTIONS.items() if k != "encoding"
}  # input is UTF-8 here
