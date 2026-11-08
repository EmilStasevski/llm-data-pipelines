"""Load and validate spec/lexicon/*.yaml (German domain vocabulary for the generator and Track A)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import yaml

GENERA = ("m", "f", "n")


@dataclass(frozen=True)
class Werk:
    werk: str
    stadt: str
    regierungsbezirk: str
    nuts2: str
    sektor: str
    kuerzel: str


@dataclass(frozen=True)
class Komponente:
    name: str
    formen: tuple[tuple[str, str], ...]  # (Oberflächenform, Genus)
    stamm: str | None
    schaeden: tuple[str, ...]
    eq: str


@dataclass(frozen=True)
class Schadensart:
    key: str
    phrasen: tuple[str, ...]
    kompositum: str | None
    en: tuple[str, ...]


@dataclass(frozen=True)
class Benannt:
    """A canonical value with its surface phrases (Ursache, Maßnahme)."""

    name: str
    phrasen: tuple[str, ...]


@dataclass(frozen=True)
class Lexicon:
    werke: tuple[Werk, ...]
    anlagen: dict[str, tuple[str, ...]]
    komponenten: tuple[Komponente, ...]
    schadensarten: tuple[Schadensart, ...]
    ursachen: tuple[Benannt, ...]
    ursache_unbekannt: tuple[str, ...]
    massnahmen: tuple[Benannt, ...]
    massnahme_offen: tuple[str, ...]
    abkuerzungen: dict[str, str]
    sicherheit_trigger: tuple[str, ...]
    sicherheit_phrasen: tuple[str, ...]
    sha256: str  # over all lexicon files, recorded in manifests/cards

    def schadensart(self, key: str) -> Schadensart:
        return next(s for s in self.schadensarten if s.key == key)


class LexiconError(ValueError):
    pass


def _load(path: Path) -> dict[str, Any]:
    return cast("dict[str, Any]", yaml.safe_load(path.read_text(encoding="utf-8")))


def load(lexdir: Path) -> Lexicon:
    """Load all lexicon files and validate cross-references (raises LexiconError)."""
    files = sorted(lexdir.glob("*.yaml"))
    digest = hashlib.sha256()
    for f in files:
        digest.update(f.name.encode() + b"\0" + f.read_bytes())
    w, k = _load(lexdir / "werke_nrw.yaml"), _load(lexdir / "komponenten.yaml")
    s, u = _load(lexdir / "schadensarten.yaml"), _load(lexdir / "ursachen.yaml")
    m, a, sich = (
        _load(lexdir / "massnahmen.yaml"),
        _load(lexdir / "abkuerzungen.yaml"),
        _load(lexdir / "sicherheit.yaml"),
    )
    lex = Lexicon(
        werke=tuple(Werk(**x) for x in w["werke"]),
        anlagen={sek: tuple(v) for sek, v in w["anlagen"].items()},
        komponenten=tuple(
            Komponente(x["name"], tuple(x["formen"].items()), x["stamm"], tuple(x["schaeden"]), x["eq"])
            for x in k["komponenten"]
        ),
        schadensarten=tuple(
            Schadensart(key, tuple(v["phrasen"]), v["kompositum"], tuple(v["en"]))
            for key, v in s["schadensarten"].items()
        ),
        ursachen=tuple(Benannt(x["name"], tuple(x["phrasen"])) for x in u["ursachen"]),
        ursache_unbekannt=tuple(u["unbekannt"]),
        massnahmen=tuple(Benannt(x["name"], tuple(x["phrasen"])) for x in m["massnahmen"]),
        massnahme_offen=tuple(m["offen"]),
        abkuerzungen={str(kk): str(vv) for kk, vv in a["abkuerzungen"].items()},
        sicherheit_trigger=tuple(sich["trigger"]),
        sicherheit_phrasen=tuple(sich["phrasen"]),
        sha256=digest.hexdigest(),
    )
    validate(lex)
    return lex


def validate(lex: Lexicon) -> None:
    errs: list[str] = []
    keys = {x.key for x in lex.schadensarten}
    for w in lex.werke:
        if w.sektor not in lex.anlagen:
            errs.append(f"Werk {w.werk}: Sektor {w.sektor!r} hat keine Anlagenliste")
        if not (w.nuts2.startswith("DEA") and len(w.nuts2) == 4):
            errs.append(f"Werk {w.werk}: NUTS-2 {w.nuts2!r} ist kein NRW-Code")
    if len({w.werk for w in lex.werke}) != len(lex.werke):
        errs.append("doppelte Werke")
    for c in lex.komponenten:
        errs += [f"{c.name}: unbekannte Schadensart {s!r}" for s in c.schaeden if s not in keys]
        errs += [f"{c.name}: Form {f!r} hat Genus {g!r}" for f, g in c.formen if g not in GENERA]
        if c.name not in dict(c.formen):
            errs.append(f"{c.name}: kanonischer Name fehlt in formen")
    for s in lex.schadensarten:
        for p in (*s.phrasen, *s.en):
            if "{N}" not in p and "{A}" not in p:
                errs.append(f"Schadensart {s.key}: Phrase {p!r} nennt die Komponente nicht")
    trig = [t.lower() for t in lex.sicherheit_trigger]
    for p in lex.sicherheit_phrasen:
        if not any(t in p.lower() for t in trig):
            errs.append(f"Sicherheitsphrase {p!r} enthält keinen Trigger")
    if errs:
        raise LexiconError("; ".join(errs))
