"""pandas vectorised implementation of spec/track_a.md (shared by a05 / a06 / a07, ADR 0009).

Only pandas, numpy, pyarrow and the stdlib are imported, so the identical module runs under
`cudf.pandas` in the gpu_df env (a07 = "same code as a05"). Expert choices:
- every rule is a `.str` method over the whole column (extract / replace / contains / find), no
  per-row Python function; the only callbacks are per abbreviation *match* (str.replace with a callable)
- durations use exact integer arithmetic (round half up), like the reference
- longest component: one regex pass with a zero-width lookahead over the forms sorted longest first
  (yields the longest form starting at every position), then explode → longest, earliest
- doc_text: " | label: value" for present parts, concatenated, then the leading separator cut off
"""

from __future__ import annotations

import re
from typing import Any, cast

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute

from zj_df.track_a.common import COLUMNS, SCHEMA

WS_RUN = "[\t\n\x0b\x0c\r \xa0]+"
RE_EQUIPMENT = r"^([A-Z])[ -]?([0-9]{4})(-[A-Z])?$"
RE_DATE_DE = r"^([0-9]{1,2})\.([0-9]{1,2})\.([0-9]{2}|[0-9]{4})$"
RE_DATE_ISO = r"^([0-9]{4})-([0-9]{2})-([0-9]{2})$"
RE_NUMBER = r"^(-)?([0-9]{1,3}(?:[. ][0-9]{3})+|[0-9]+)(?:,([0-9]+))?(?: ?(?:€|EUR))?$"
RE_DURATION = re.compile(
    r"^(?:ca\. ?)?(?:[0-9]+(?:,[0-9]+)? ?- ?)?([0-9]+)(?:,([0-9]+))? ?(h|std\.|stunde|stunden|min|minuten)$",
    re.IGNORECASE,
)
RE_MATNR = r"(?:^|[^0-9])(10[0-9]{6})(?:[^0-9]|$)"
HOURS = ("h", "std.", "stunde", "stunden")
pc = cast("Any", pyarrow.compute)  # pyarrow-stubs: compute kernels are only partially typed


def _cls(chars: str) -> str:
    return "".join("\\" + c if c in "[]^-\\" else c for c in chars)


class PandasRules:
    def __init__(self, lex: dict[str, Any]) -> None:
        self.moji = sorted(lex["mojibake"].items(), key=lambda kv: -len(kv[0]))
        self.abbr: dict[str, str] = lex["abbreviations"]
        keys = "|".join(re.escape(k) for k in sorted(self.abbr, key=len, reverse=True))
        lead, trail = _cls(lex["strip_leading"]), _cls(lex["strip_trailing"])
        # one token = run between spaces (text is normalised); case-insensitive pre-match, exact
        # `.lower()` lookup in the callback (identical to the spec's token rule)
        self.abbr_re = re.compile(rf"(?<![^ ])([{lead}]*)({keys})([{trail}]*)(?![^ ])", re.IGNORECASE)
        self.fold = list(lex["fold"].items())
        self.safety_re = "|".join(re.escape(t) for t in lex["safety_triggers_fold"])
        forms: dict[str, str] = lex["komponenten_fold"]
        ordered = sorted(forms, key=len, reverse=True)  # stable: equal lengths keep lexicon order
        self.forms_re = re.compile("(?=(" + "|".join(re.escape(f) for f in ordered) + "))")
        self.form_names = forms
        self.rb = {w: v["regierungsbezirk"] for w, v in lex["werke"].items()}
        self.nuts = {w: v["nuts2"] for w, v in lex["werke"].items()}

    # --- §1 -----------------------------------------------------------------------------------
    def norm(self, s: pd.Series) -> pd.Series:
        for bad, good in self.moji:
            s = s.str.replace(bad, good, regex=False)
        return s.str.normalize("NFC").str.replace(WS_RUN, " ", regex=True).str.strip(" ")

    # --- §2–5 (inputs normalised) --------------------------------------------------------------
    @staticmethod
    def equipment(s: pd.Series) -> pd.Series:
        g = s.str.extract(RE_EQUIPMENT)
        return (g[0] + "-" + g[1] + g[2].fillna("")).astype(object).where(g[0].notna(), None)

    @staticmethod
    def date(s: pd.Series) -> Any:  # pa.Array[date32]
        de, iso = s.str.extract(RE_DATE_DE), s.str.extract(RE_DATE_ISO)
        y = de[2].where(de[2].str.len() != 2, "20" + de[2])
        s_de = y + "-" + de[1].str.zfill(2) + "-" + de[0].str.zfill(2)
        s_iso = iso[0] + "-" + iso[1] + "-" + iso[2]
        txt = s_de.fillna(s_iso)
        ts = pd.to_datetime(txt, format="%Y-%m-%d", errors="coerce")  # impossible dates → NaT
        return pa.array(ts).cast(pa.date32())

    @staticmethod
    def prio(s: pd.Series) -> pd.Series:
        n = pd.to_numeric(s.where(s.str.fullmatch(r"[0-9]+").fillna(False)), errors="coerce")
        return n.where(n.between(1, 4)).astype("Int8")

    @staticmethod
    def eur(s: pd.Series) -> pd.Series:
        g = s.str.extract(RE_NUMBER)
        txt = g[0].fillna("") + g[1].str.replace(r"[. ]", "", regex=True) + "." + g[2].fillna("0")
        return cast("pd.Series", pd.to_numeric(txt, errors="coerce")).astype("float64")

    @staticmethod
    def minutes(s: pd.Series) -> pd.Series:
        g = s.str.extract(RE_DURATION)  # (whole, frac, unit)
        frac = g[1].fillna("")
        num = pd.to_numeric(g[0] + frac, errors="raise").astype("Int64")  # exact decimal = num / den
        den = (10 ** frac.str.len().astype("Int64")).astype("Int64")
        factor = pd.Series(np.where(g[2].str.lower().isin(HOURS), 60, 1), index=s.index).astype("Int64")
        return ((2 * num * factor + den) // (2 * den)).astype("Int32")  # round half up, exact

    # --- §6 ------------------------------------------------------------------------------------
    def expand(self, s: pd.Series) -> pd.Series:
        abbr = self.abbr

        def rep(m: re.Match[str]) -> str:
            v = abbr.get(m[2].lower())
            return m[0] if v is None else m[1] + v + m[3]

        return s.str.replace(self.abbr_re, rep, regex=True)

    def to_fold(self, lower: pd.Series) -> pd.Series:
        for k, v in self.fold:
            lower = lower.str.replace(k, v, regex=False)
        return lower

    # --- §7 ------------------------------------------------------------------------------------
    def komponente(self, fold: pd.Series) -> pd.Series:
        hits = fold.str.findall(self.forms_re).explode().dropna()  # per row: in position order
        if hits.empty:
            return pd.Series(None, index=fold.index, dtype=object)
        df = pd.DataFrame({"row": hits.index, "form": hits.to_numpy(), "len": hits.str.len().to_numpy()})
        best = df.sort_values(["row", "len"], ascending=[True, False], kind="stable").drop_duplicates("row")
        names = best["form"].map(self.form_names)
        out = pd.Series(None, index=fold.index, dtype=object)
        out.loc[best["row"].to_numpy()] = names.to_numpy()
        return out

    # --- whole pipeline ------------------------------------------------------------------------
    def transform(self, df: pd.DataFrame) -> pa.Table:
        n = self.norm
        text_norm = self.expand(n(df["meldungstext"]))
        text_lower = text_norm.str.lower()
        text_fold = self.to_fold(text_lower)
        werk = n(df["werk"])
        equipment = self.equipment(n(df["equipment_nr"]))
        datum = self.date(n(df["meldungsdatum"]))
        prio = self.prio(n(df["prioritaet"]))
        out = pd.DataFrame(
            {
                "meldung_id": n(df["meldung_id"]),
                "werk": werk,
                "anlage": n(df["anlage"]),
                "equipment_nr": equipment,
                "schicht": n(df["schicht"]),
                "prioritaet": prio,
                "stillstand_min": self.minutes(n(df["stillstand"])),
                "kosten_eur": self.eur(n(df["kosten"])),
                "status": n(df["status"]),
                "text_norm": text_norm,
                "text_lower": text_lower,
                "text_fold": text_fold,
                "materialnr": text_norm.str.extract(RE_MATNR)[0],
                "sicherheitsrelevant_rule": text_fold.str.contains(self.safety_re, regex=True).astype(bool),
                "komponente_rule": self.komponente(text_fold),
                "regierungsbezirk": werk.map(self.rb),
                "nuts2": werk.map(self.nuts),
            }
        )
        datum_txt = pd.Series(
            pc.strftime(datum.cast(pa.timestamp("s")), format="%Y-%m-%d").to_pandas(),
            index=df.index,
        )

        def part(label: str, v: pd.Series) -> pd.Series:
            v = v.astype(object).where(v.notna(), None)
            present = v.notna() & (v != "")
            return (" | " + label + ": " + v.where(present, "").astype(str)).where(present, "")

        out["doc_text"] = (
            part("Werk", out["werk"])
            + part("Anlage", out["anlage"])
            + part("Equipment", out["equipment_nr"])
            + part("Datum", datum_txt)
            + part("Priorität", prio)
            + part("Meldung", out["text_norm"])
        ).str.slice(3)
        order: Any = pa.array(np.argsort(out["meldung_id"].to_numpy(dtype=object), kind="stable"))
        types: list[Any] = [SCHEMA.field(c).type for c in COLUMNS]  # pyright: ignore[reportUnknownMemberType]
        arrays: list[Any] = [
            datum if c == "datum" else _arrow(out[c], t) for c, t in zip(COLUMNS, types, strict=True)
        ]
        return pa.Table.from_arrays(arrays, schema=SCHEMA).take(order)


def _arrow(s: pd.Series, typ: Any) -> Any:
    return pa.array(s.astype(object).where(s.notna(), None).to_numpy(), type=typ)  # pyright: ignore[reportUnknownVariableType]
