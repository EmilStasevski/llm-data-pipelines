"""Track A reference implementation (spec of truth for `a.full`, spec/track_a.md).

Deliberately plain: csv module, explicit loops, Decimal for exact rounding. It is slow on purpose and
is NOT a contestant. Every contestant's output is verified against this module's output.

CLI:
  python -m zj_reference.track_a compile-lexicon        # spec/lexicon/*.yaml → spec/track_a_lexicon.json
  python -m zj_reference.track_a run --input X --output Y
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import re
import sys
import unicodedata
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, cast

import pyarrow as pa
import pyarrow.parquet as pq
import yaml

REPO = Path(__file__).resolve().parents[3]
LEXICON_JSON = REPO / "spec" / "track_a_lexicon.json"

# fixed repair table from PLAN §7.2 (exactly the characters the generator corrupts)
MOJIBAKE = {"Ã¤": "ä", "Ã¶": "ö", "Ã¼": "ü", "ÃŸ": "ß", "Ã„": "Ä", "Ã–": "Ö", "Ãœ": "Ü", "â‚¬": "€"}
WHITESPACE = "\t\n\v\f\r  "
STRIP_LEADING = "([{\"'"
STRIP_TRAILING = ",;:!?)]}\"'"
FOLD = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}
MIN_FORM_LEN = 4

SCHEMA = pa.schema(
    [
        pa.field("meldung_id", pa.string(), nullable=False),
        pa.field("werk", pa.string(), nullable=False),
        pa.field("anlage", pa.string(), nullable=False),
        pa.field("equipment_nr", pa.string()),
        pa.field("datum", pa.date32()),
        pa.field("schicht", pa.string(), nullable=False),
        pa.field("prioritaet", pa.int8()),
        pa.field("stillstand_min", pa.int32()),
        pa.field("kosten_eur", pa.float64()),
        pa.field("status", pa.string(), nullable=False),
        pa.field("text_norm", pa.string(), nullable=False),
        pa.field("text_lower", pa.string(), nullable=False),
        pa.field("text_fold", pa.string(), nullable=False),
        pa.field("materialnr", pa.string()),
        pa.field("sicherheitsrelevant_rule", pa.bool_(), nullable=False),
        pa.field("komponente_rule", pa.string()),
        pa.field("regierungsbezirk", pa.string()),
        pa.field("nuts2", pa.string()),
        pa.field("doc_text", pa.string(), nullable=False),
    ]
)

RE_EQUIPMENT = re.compile(r"^([A-Z])[ -]?([0-9]{4})(-[A-Z])?$")
RE_DATE_DE = re.compile(r"^([0-9]{1,2})\.([0-9]{1,2})\.([0-9]{2}|[0-9]{4})$")
RE_DATE_ISO = re.compile(r"^([0-9]{4})-([0-9]{2})-([0-9]{2})$")
RE_NUMBER = re.compile(r"^(-)?([0-9]{1,3}(?:[. ][0-9]{3})+|[0-9]+)(?:,([0-9]+))?(?: ?(?:€|EUR))?$")
RE_DURATION = re.compile(
    r"^(?:ca\. ?)?(?:([0-9]+(?:,[0-9]+)?) ?- ?)?([0-9]+(?:,[0-9]+)?) ?(h|std\.|stunde|stunden|min|minuten)$",
    re.IGNORECASE,
)
RE_MATNR = re.compile(r"(^|[^0-9])(10[0-9]{6})([^0-9]|$)")


# ------------------------------------------------------------------------------ lexicon
def fold(s: str) -> str:
    return "".join(FOLD.get(c, c) for c in s)


def compile_lexicon(lexdir: Path) -> dict[str, Any]:
    """Compile spec/lexicon/*.yaml into the JSON every contestant reads. Fails on ambiguities."""

    def load(name: str) -> dict[str, Any]:
        return cast("dict[str, Any]", yaml.safe_load((lexdir / name).read_text(encoding="utf-8")))

    abk = load("abkuerzungen.yaml")["abkuerzungen"]
    abbreviations: dict[str, str] = {}
    for key, value in abk.items():
        k = str(key).lower()
        if k in abbreviations:
            raise ValueError(f"abbreviation keys collide after lower(): {key!r}")
        abbreviations[k] = str(value)
    komponenten: dict[str, str] = {}
    for k in load("komponenten.yaml")["komponenten"]:
        for form in k["formen"]:
            f = fold(str(form).lower())
            if len(f) < MIN_FORM_LEN:
                continue
            if komponenten.get(f, k["name"]) != k["name"]:
                raise ValueError(f"folded form {f!r} maps to two components")
            komponenten[f] = k["name"]
    sicher = load("sicherheit.yaml")
    werke = {
        w["werk"]: {"regierungsbezirk": w["regierungsbezirk"], "nuts2": w["nuts2"]}
        for w in load("werke_nrw.yaml")["werke"]
    }
    digest = hashlib.sha256()
    for f in sorted(lexdir.glob("*.yaml")):
        digest.update(f.name.encode() + b"\0" + f.read_bytes())
    return {
        "spec": "spec/track_a.md v1.0",
        "lexicon_sha256": digest.hexdigest(),
        "mojibake": MOJIBAKE,
        "whitespace": WHITESPACE,
        "strip_leading": STRIP_LEADING,
        "strip_trailing": STRIP_TRAILING,
        "fold": FOLD,
        "abbreviations": dict(sorted(abbreviations.items())),
        "safety_triggers_fold": sorted({fold(str(t).lower()) for t in sicher["trigger"]}),
        "komponenten_fold": dict(sorted(komponenten.items())),
        "werke": werke,
    }


def load_lexicon(path: Path = LEXICON_JSON) -> dict[str, Any]:
    return cast("dict[str, Any]", json.loads(path.read_text(encoding="utf-8")))


# ------------------------------------------------------------------------------ fields
def norm(s: str) -> str:
    for bad in sorted(MOJIBAKE, key=len, reverse=True):
        s = s.replace(bad, MOJIBAKE[bad])
    s = unicodedata.normalize("NFC", s)
    out: list[str] = []
    in_ws = False
    for c in s:
        if c in WHITESPACE:
            if not in_ws:
                out.append(" ")
            in_ws = True
        else:
            out.append(c)
            in_ws = False
    return "".join(out).strip(" ")


def equipment(s: str) -> str | None:
    m = RE_EQUIPMENT.match(norm(s))
    return f"{m[1]}-{m[2]}{m[3] or ''}" if m else None


def parse_date(s: str) -> dt.date | None:
    s = norm(s)
    if m := RE_DATE_DE.match(s):
        d, mo, y = int(m[1]), int(m[2]), int(m[3])
        if len(m[3]) == 2:
            y += 2000
    elif m := RE_DATE_ISO.match(s):
        y, mo, d = int(m[1]), int(m[2]), int(m[3])
    else:
        return None
    try:
        return dt.date(y, mo, d)
    except ValueError:
        return None


def parse_prio(s: str) -> int | None:
    s = norm(s)
    if s.isascii() and s.isdigit() and 1 <= int(s) <= 4:
        return int(s)
    return None


def parse_eur(s: str) -> float | None:
    m = RE_NUMBER.match(norm(s))
    if not m:
        return None
    whole = m[2].replace(".", "").replace(" ", "")
    return float(f"{m[1] or ''}{whole}.{m[3] or '0'}")


def parse_minutes(s: str) -> int | None:
    m = RE_DURATION.match(norm(s))
    if not m:
        return None
    value = Decimal(m[2].replace(",", "."))
    factor = 60 if m[3].lower() in ("h", "std.", "stunde", "stunden") else 1
    return int((value * factor).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def expand_abbreviations(text: str, lex: dict[str, Any]) -> str:
    abbr: dict[str, str] = lex["abbreviations"]
    out: list[str] = []
    for tok in text.split(" "):
        i, j = 0, len(tok)
        while i < j and tok[i] in STRIP_LEADING:
            i += 1
        while j > i and tok[j - 1] in STRIP_TRAILING:
            j -= 1
        core = tok[i:j]
        rep = abbr.get(core.lower())
        out.append(tok[:i] + rep + tok[j:] if rep is not None else tok)
    return " ".join(out)


def materialnr(text_norm: str) -> str | None:
    m = RE_MATNR.search(text_norm)
    return m[2] if m else None


def safety(text_fold: str, lex: dict[str, Any]) -> bool:
    return any(t in text_fold for t in lex["safety_triggers_fold"])


def komponente(text_fold: str, lex: dict[str, Any]) -> str | None:
    best: tuple[int, int, str] | None = None  # (length, -start, canonical)
    for form, name in lex["komponenten_fold"].items():
        start = text_fold.find(form)  # earliest occurrence is the only one that can win for this form
        if start < 0:
            continue
        cand = (len(form), -start, name)
        if best is None or cand[:2] > best[:2]:
            best = cand
    return best[2] if best else None


def doc_text(row: dict[str, Any]) -> str:
    parts = [
        ("Werk", row["werk"]), ("Anlage", row["anlage"]), ("Equipment", row["equipment_nr"]),
        ("Datum", row["datum"].isoformat() if row["datum"] else None),
        ("Priorität", row["prioritaet"]), ("Meldung", row["text_norm"]),
    ]  # fmt: skip
    return " | ".join(f"{k}: {v}" for k, v in parts if v is not None and v != "")


def process_row(r: dict[str, str], lex: dict[str, Any]) -> dict[str, Any]:
    text_norm = expand_abbreviations(norm(r["meldungstext"]), lex)
    text_lower = text_norm.lower()
    text_fold = fold(text_lower)
    werk = norm(r["werk"])
    region = lex["werke"].get(werk)
    row: dict[str, Any] = {
        "meldung_id": norm(r["meldung_id"]),
        "werk": werk,
        "anlage": norm(r["anlage"]),
        "equipment_nr": equipment(r["equipment_nr"]),
        "datum": parse_date(r["meldungsdatum"]),
        "schicht": norm(r["schicht"]),
        "prioritaet": parse_prio(r["prioritaet"]),
        "stillstand_min": parse_minutes(r["stillstand"]),
        "kosten_eur": parse_eur(r["kosten"]),
        "status": norm(r["status"]),
        "text_norm": text_norm,
        "text_lower": text_lower,
        "text_fold": text_fold,
        "materialnr": materialnr(text_norm),
        "sicherheitsrelevant_rule": safety(text_fold, lex),
        "komponente_rule": komponente(text_fold, lex),
        "regierungsbezirk": region["regierungsbezirk"] if region else None,
        "nuts2": region["nuts2"] if region else None,
    }
    row["doc_text"] = doc_text(row)
    return row


CHUNK_ROWS = 50_000


def run(input_csv: Path, output: Path, lex: dict[str, Any], chunk_rows: int = CHUNK_ROWS) -> int:
    """Stream the CSV in chunks so memory stays flat at every scale (XL = 10M rows).

    The output must be sorted by meldung_id. The generator writes the input in strictly increasing id
    order, so the reference checks that and writes chunk by chunk; an out-of-order or duplicate id
    stops the run (fail loudly) instead of producing a silently unsorted reference."""
    n, last = 0, None
    with (
        input_csv.open(encoding="cp1252", newline="") as f,
        pq.ParquetWriter(output, SCHEMA, compression="zstd") as w,
    ):
        buf: list[dict[str, Any]] = []
        for r in csv.DictReader(f, delimiter=";", quotechar='"'):
            row = process_row(r, lex)
            mid = row["meldung_id"]
            if last is not None and mid <= last:
                raise ValueError(f"input not strictly increasing by meldung_id: {last!r} then {mid!r}")
            last = mid
            buf.append(row)
            if len(buf) >= chunk_rows:
                w.write_table(pa.Table.from_pylist(buf, schema=SCHEMA))
                n += len(buf)
                buf = []
        if buf or n == 0:
            w.write_table(pa.Table.from_pylist(buf, schema=SCHEMA))
            n += len(buf)
    return n


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m zj_reference.track_a")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("compile-lexicon")
    r = sub.add_parser("run")
    r.add_argument("--input", type=Path, required=True)
    r.add_argument("--output", type=Path, required=True)
    a = p.parse_args(argv)
    if a.cmd == "compile-lexicon":
        lex = compile_lexicon(REPO / "spec" / "lexicon")
        LEXICON_JSON.write_text(json.dumps(lex, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        counts = (len(lex["abbreviations"]), len(lex["komponenten_fold"]), len(lex["safety_triggers_fold"]))
        target = LEXICON_JSON.relative_to(REPO)
        print(f"{target}: {counts[0]} abbreviations, {counts[1]} forms, {counts[2]} triggers")
        return 0
    a.output.parent.mkdir(parents=True, exist_ok=True)
    n = run(a.input, a.output, load_lexicon())
    print(f"{n} rows → {a.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
