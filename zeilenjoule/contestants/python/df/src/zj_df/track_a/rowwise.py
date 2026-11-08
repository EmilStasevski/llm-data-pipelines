"""Plain-Python per-row implementation of spec/track_a.md (used by the row-loop contestants a01–a04,
which differ only in how they iterate). Independent of the reference module; golden-vector tested."""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from typing import Any, Protocol

WS_RUN = re.compile("[\t\n\v\f\r  ]+")
RE_EQUIPMENT = re.compile(r"^([A-Z])[ -]?([0-9]{4})(-[A-Z])?$")
RE_DATE_DE = re.compile(r"^([0-9]{1,2})\.([0-9]{1,2})\.([0-9]{2}|[0-9]{4})$")
RE_DATE_ISO = re.compile(r"^([0-9]{4})-([0-9]{2})-([0-9]{2})$")
RE_PRIO = re.compile(r"^[0-9]+$")
RE_NUMBER = re.compile(r"^(-)?([0-9]{1,3}(?:[. ][0-9]{3})+|[0-9]+)(?:,([0-9]+))?(?: ?(?:€|EUR))?$")
RE_DURATION = re.compile(
    r"^(?:ca\. ?)?(?:([0-9]+(?:,[0-9]+)?) ?- ?)?([0-9]+(?:,[0-9]+)?) ?(h|std\.|stunde|stunden|min|minuten)$",
    re.IGNORECASE,
)
RE_MATNR = re.compile(r"(?:^|[^0-9])(10[0-9]{6})(?:[^0-9]|$)")


class RowLike(Protocol):
    """Anything indexable by column name: dict (csv.DictReader) or pandas Series (iterrows/apply)."""

    def __getitem__(self, key: str, /) -> Any: ...


class Rules:
    """Per-row functions bound to one compiled lexicon."""

    def __init__(self, lex: dict[str, Any]) -> None:
        self.moji = sorted(lex["mojibake"].items(), key=lambda kv: -len(kv[0]))
        self.abbr: dict[str, str] = lex["abbreviations"]
        self.lead, self.trail = lex["strip_leading"], lex["strip_trailing"]
        self.fold_map = str.maketrans({k: v for k, v in lex["fold"].items()})
        self.triggers: list[str] = lex["safety_triggers_fold"]
        # longest form first, so the first found form of each length class is decided by position below
        self.forms = sorted(lex["komponenten_fold"].items(), key=lambda kv: -len(kv[0]))
        self.werke: dict[str, dict[str, str]] = lex["werke"]

    def norm(self, s: str) -> str:
        for bad, good in self.moji:
            if bad in s:
                s = s.replace(bad, good)
        return WS_RUN.sub(" ", unicodedata.normalize("NFC", s)).strip(" ")

    def equipment(self, s: str) -> str | None:
        m = RE_EQUIPMENT.match(self.norm(s))
        return f"{m[1]}-{m[2]}{m[3] or ''}" if m else None

    def date(self, s: str) -> dt.date | None:
        s = self.norm(s)
        if m := RE_DATE_DE.match(s):
            y = int(m[3]) + (2000 if len(m[3]) == 2 else 0)
            parts = (y, int(m[2]), int(m[1]))
        elif m := RE_DATE_ISO.match(s):
            parts = (int(m[1]), int(m[2]), int(m[3]))
        else:
            return None
        try:
            return dt.date(*parts)
        except ValueError:
            return None

    def prio(self, s: str) -> int | None:
        s = self.norm(s)
        return int(s) if RE_PRIO.match(s) and 1 <= int(s) <= 4 else None

    def eur(self, s: str) -> float | None:
        m = RE_NUMBER.match(self.norm(s))
        if not m:
            return None
        return float((m[1] or "") + m[2].replace(".", "").replace(" ", "") + "." + (m[3] or "0"))

    def minutes(self, s: str) -> int | None:
        m = RE_DURATION.match(self.norm(s))
        if not m:
            return None
        whole, _, frac = m[2].partition(",")
        num, den = int(whole + frac), 10 ** len(frac)  # exact decimal value = num / den
        factor = 60 if m[3].lower() in ("h", "std.", "stunde", "stunden") else 1
        return (2 * num * factor + den) // (2 * den)  # round half up (values are non-negative)

    def expand(self, text: str) -> str:
        toks = text.split(" ")
        for k, tok in enumerate(toks):
            i, j = 0, len(tok)
            while i < j and tok[i] in self.lead:
                i += 1
            while j > i and tok[j - 1] in self.trail:
                j -= 1
            rep = self.abbr.get(tok[i:j].lower())
            if rep is not None:
                toks[k] = tok[:i] + rep + tok[j:]
        return " ".join(toks)

    def fold(self, lower: str) -> str:
        return lower.translate(self.fold_map)

    def komponente(self, fold: str) -> str | None:
        best_len, best_pos, best = 0, 0, None
        for form, name in self.forms:  # sorted by length desc
            if len(form) < best_len:
                break
            pos = fold.find(form)
            if pos >= 0 and (best is None or pos < best_pos):
                best_len, best_pos, best = len(form), pos, name
        return best

    def row(self, r: RowLike) -> dict[str, Any]:
        text_norm = self.expand(self.norm(r["meldungstext"]))
        text_lower = text_norm.lower()
        text_fold = self.fold(text_lower)
        werk = self.norm(r["werk"])
        region = self.werke.get(werk)
        m = RE_MATNR.search(text_norm)
        out: dict[str, Any] = {
            "meldung_id": self.norm(r["meldung_id"]),
            "werk": werk,
            "anlage": self.norm(r["anlage"]),
            "equipment_nr": self.equipment(r["equipment_nr"]),
            "datum": self.date(r["meldungsdatum"]),
            "schicht": self.norm(r["schicht"]),
            "prioritaet": self.prio(r["prioritaet"]),
            "stillstand_min": self.minutes(r["stillstand"]),
            "kosten_eur": self.eur(r["kosten"]),
            "status": self.norm(r["status"]),
            "text_norm": text_norm,
            "text_lower": text_lower,
            "text_fold": text_fold,
            "materialnr": m[1] if m else None,
            "sicherheitsrelevant_rule": any(t in text_fold for t in self.triggers),
            "komponente_rule": self.komponente(text_fold),
            "regierungsbezirk": region["regierungsbezirk"] if region else None,
            "nuts2": region["nuts2"] if region else None,
        }
        out["doc_text"] = self.doc(out)
        return out

    @staticmethod
    def doc(out: dict[str, Any]) -> str:
        parts = [
            ("Werk", out["werk"]), ("Anlage", out["anlage"]), ("Equipment", out["equipment_nr"]),
            ("Datum", out["datum"].isoformat() if out["datum"] else None), ("Priorität", out["prioritaet"]),
            ("Meldung", out["text_norm"]),
        ]  # fmt: skip
        return " | ".join(f"{k}: {v}" for k, v in parts if v is not None and v != "")
