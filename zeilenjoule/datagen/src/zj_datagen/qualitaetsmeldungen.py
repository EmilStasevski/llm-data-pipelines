"""Synthetic quality notifications (8D/complaints). Same determinism/prefix rules as stoermeldungen.

Gold consistency: the verdict (urteil) is i.O. exactly when fehlerklasse == "kein Fehler"; for
measurement defects the rendered measured value lies outside the rendered tolerance, otherwise
inside (gold column messwert_in_toleranz, computed from the *rounded* value).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import IntEnum, auto
from pathlib import Path
from typing import Any, cast

import numpy as np
import yaml
from faker import Faker

from zj_datagen import noise
from zj_datagen.lexicon import Werk

TABLE_ID = 2
ID_BASE = 3_000_000_000

VISIBLE = (
    "qmeldung_id", "werk", "lieferant", "materialnr", "charge", "meldungsdatum",
    "merkmal", "messwert", "toleranz", "urteil", "freitext",
)  # fmt: skip
GOLD = (
    "qmeldung_id", "fehlerklasse", "ursachenkategorie", "massnahme", "messwert", "einheit",
    "abweichung", "abweichung_mm", "messwert_in_toleranz", "werk", "noise",
)  # fmt: skip

# feature group → defect class when the measured value is out of tolerance
MASS_KLASSE = {
    "mm": "Maßabweichung", "HRC": "Materialfehler", "MPa": "Materialfehler", "µm": "Oberflächenfehler",
    "g": "Funktionsfehler", "Nm": "Funktionsfehler",
}  # fmt: skip
SONSTIGE = (
    "Oberflächenfehler",
    "Materialfehler",
    "Verpackungsschaden",
    "Kennzeichnungsfehler",
    "Funktionsfehler",
)


@dataclass(frozen=True)
class Merkmal:
    name: str
    einheit: str
    dezimal: int
    nenn: float | None = None
    tol: float | None = None
    max: float | None = None
    min: float | None = None

    def inside(self, w: float) -> bool:
        eps = 1e-9
        if self.nenn is not None and self.tol is not None:
            return abs(w - self.nenn) <= self.tol + eps
        if self.max is not None:
            return w <= self.max + eps
        return self.min is not None and w >= self.min - eps


@dataclass(frozen=True)
class QLexicon:
    merkmale: tuple[Merkmal, ...]
    messfehler: tuple[str, ...]
    fehlerklassen: dict[str, tuple[str, ...]]
    ursachen: dict[str, tuple[str, ...]]
    ursache_unbekannt: tuple[str, ...]
    massnahmen: dict[str, tuple[str, ...]]
    massnahme_offen: tuple[str, ...]


def load_lexicon(lexdir: Path) -> QLexicon:
    d = cast("dict[str, Any]", yaml.safe_load((lexdir / "qualitaet.yaml").read_text(encoding="utf-8")))
    return QLexicon(
        merkmale=tuple(Merkmal(**m) for m in d["merkmale"]),
        messfehler=tuple(d["messfehler"]),
        fehlerklassen={k: tuple(v) for k, v in d["fehlerklassen"].items()},
        ursachen={k: tuple(v) for k, v in d["ursachenkategorien"].items()},
        ursache_unbekannt=tuple(d["ursache_unbekannt"]),
        massnahmen={k: tuple(v) for k, v in d["massnahmen"].items()},
        massnahme_offen=tuple(d["massnahme_offen"]),
    )


class U(IntEnum):
    WERK = 0
    LIEFERANT = auto()
    MATNR = auto()
    CHARGE = auto()
    DATUM = auto()
    MERKMAL = auto()
    TYP = auto()
    SONST = auto()
    WERT = auto()
    VORZEICHEN = auto()
    TOL_STIL = auto()
    WERT_STIL = auto()
    FEHLER_PH = auto()
    URSACHE = auto()
    URSACHE_KAT = auto()
    URSACHE_PH = auto()
    MASSN_OFFEN = auto()
    MASSN = auto()
    MASSN_PH = auto()
    ORDER = auto()
    N_TYPO = auto()
    N_TRANSLIT = auto()
    N_MOJI = auto()
    N_GROSS = auto()
    N_KLEIN = auto()
    N_SATZ = auto()
    N_LEER = auto()


N_COLS = len(U)


def _pick[T](seq: tuple[T, ...], u: float) -> T:
    return seq[min(int(u * len(seq)), len(seq) - 1)]


def _de(x: float, dec: int) -> str:
    return f"{x:.{dec}f}".replace(".", ",")


# commercial legal forms only (Faker also yields e.V., Stiftung or bare surnames, odd for suppliers)
RECHTSFORMEN = ("GmbH", "AG", "KG", "OHG", "GbR", "KGaA", "SE")


def lieferanten(seed: int, n: int) -> tuple[str, ...]:
    """Fictional supplier names (Faker de_DE, seeded; Faker version pinned in the lockfile)."""
    fake = Faker("de_DE")
    fake.seed_instance(seed)
    names: list[str] = []
    while len(names) < n:
        c = fake.company()
        if c.endswith(RECHTSFORMEN) and c not in names:
            names.append(c)
    return tuple(names)


def _measure(m: Merkmal, outside: bool, u_wert: float, u_sign: float) -> float:
    """Measured value, rounded to the feature's decimals. Values keep at least one rounding unit
    (10**-dezimal) away from the limit, so rounding can never move a value across it."""
    step = 10.0**-m.dezimal
    if m.nenn is not None and m.tol is not None:
        if outside:
            off = m.tol + step + m.tol * u_wert
            w = m.nenn + (off if u_sign < 0.5 else -off)
        else:
            w = m.nenn + (m.tol - step) * (2 * u_wert - 1)
    elif m.max is not None:
        w = m.max + step + m.max * u_wert if outside else (m.max - step) * (0.2 + 0.8 * u_wert)
    else:
        assert m.min is not None
        w = m.min - step - 0.25 * m.min * u_wert if outside else m.min + step + 0.4 * m.min * u_wert
    w = round(w, m.dezimal)
    if m.inside(w) == outside:  # fail loudly instead of writing inconsistent gold
        raise AssertionError(f"{m.name}: value {w} contradicts outside={outside}")
    return w


def _tolerance(m: Merkmal, style: float) -> str:
    e, d = m.einheit, m.dezimal
    if m.nenn is not None and m.tol is not None:
        if style < 0.5:
            return f"{_de(m.nenn, d)} ± {_de(m.tol, d)} {e}"
        if style < 0.8:
            return f"{_de(m.nenn - m.tol, d)}–{_de(m.nenn + m.tol, d)} {e}"
        return f"{_de(m.nenn, d)} +{_de(m.tol, d)}/-{_de(m.tol, d)} {e}"
    if m.max is not None:
        return f"max. {_de(m.max, d)} {e}" if style < 0.7 else f"<= {_de(m.max, d)} {e}"
    assert m.min is not None
    return f"min. {_de(m.min, d)} {e}"


@dataclass(frozen=True)
class Config:
    seed: int
    chunk_rows: int
    date_start: dt.date
    date_days: int
    p: dict[str, float]
    noise: dict[str, float]
    n_lieferanten: int

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Config:
        start, end = (dt.date.fromisoformat(x) for x in d["date_range"])
        q = d["qualitaetsmeldungen"]
        return cls(
            seed=int(d["seed"]),
            chunk_rows=int(d["chunk_rows"]),
            date_start=start,
            date_days=(end - start).days + 1,
            p=dict(q["p"]),
            noise=dict(d["noise"]),
            n_lieferanten=int(q["lieferanten"]),
        )


@dataclass(frozen=True)
class QState:
    """Static inputs of the generator (built once per worker process)."""

    werke: tuple[Werk, ...]
    qlex: QLexicon
    suppliers: tuple[str, ...]
    cfg: Config


def _fill(tpl: str, m: Merkmal, wert_vis: str) -> str:
    return tpl.replace("{M}", m.name).replace("{W}", wert_vis)


@dataclass(frozen=True)
class _Befund:
    """What was found for one notification, decided before the text is rendered."""

    klasse: str
    outside: bool
    m: Merkmal
    wert_vis: str


def _freitext(st: QState, b: _Befund, r: np.ndarray) -> tuple[list[str], str | None, str | None]:
    """(sentences, gold 6M category, gold action). Out-of-tolerance rows always name feature + value."""
    q, p = st.qlex, st.cfg.p
    klasse, outside, m, wert_vis = b.klasse, b.outside, b.m, b.wert_vis
    if outside:
        saetze = [_fill(_pick(q.messfehler, r[U.FEHLER_PH]), m, wert_vis)]
        if r[U.SONST] < 0.5:
            saetze.append(_fill(_pick(q.fehlerklassen[klasse], r[U.FEHLER_PH]), m, wert_vis))
    else:
        saetze = [_fill(_pick(q.fehlerklassen[klasse], r[U.FEHLER_PH]), m, wert_vis)]
    if klasse == "kein Fehler":
        return [*saetze, "Reklamation geschlossen"], None, None
    kat = massnahme = None
    if r[U.URSACHE] < p["ursache"]:
        kat = _pick(tuple(q.ursachen), r[U.URSACHE_KAT])
        saetze.append(_pick(q.ursachen[kat], r[U.URSACHE_PH]))
    else:
        saetze.append(_pick(q.ursache_unbekannt, r[U.URSACHE_PH]))
    if r[U.MASSN_OFFEN] < p["massnahme_offen"]:
        saetze.append(_pick(q.massnahme_offen, r[U.MASSN_PH]))
    else:
        massnahme = _pick(tuple(q.massnahmen), r[U.MASSN])
        saetze.append(_pick(q.massnahmen[massnahme], r[U.MASSN_PH]))
    if r[U.ORDER] < 0.3:
        saetze[-1], saetze[-2] = saetze[-2], saetze[-1]
    return saetze, kat, massnahme


def generate_chunk(st: QState, chunk: int, n: int) -> tuple[dict[str, list[Any]], dict[str, list[Any]]]:
    cfg = st.cfg
    dec_ss, noise_ss = np.random.SeedSequence(cfg.seed, spawn_key=(TABLE_ID, chunk)).spawn(2)
    u = np.random.default_rng(dec_ss).random((cfg.chunk_rows, N_COLS))[:n]
    nrng = np.random.default_rng(noise_ss)
    p = cfg.p
    vis: dict[str, list[Any]] = {c: [] for c in VISIBLE}
    gold: dict[str, list[Any]] = {c: [] for c in GOLD}
    for j in range(n):
        r = u[j]
        werk = _pick(st.werke, r[U.WERK])
        m = _pick(st.qlex.merkmale, r[U.MERKMAL])
        if r[U.TYP] < p["massfehler"]:
            outside, klasse = True, MASS_KLASSE[m.einheit]
        elif r[U.TYP] < p["massfehler"] + p["sonstiger_fehler"]:
            outside, klasse = False, _pick(SONSTIGE, r[U.SONST])
        else:
            outside, klasse = False, "kein Fehler"
        w = _measure(m, outside, r[U.WERT], r[U.VORZEICHEN])
        sep = "" if r[U.WERT_STIL] < p["messwert_ohne_leerzeichen"] else " "
        wert_vis = f"{_de(w, m.dezimal)}{sep}{m.einheit}"
        ref = m.nenn if m.nenn is not None else (m.max if m.max is not None else m.min)
        assert ref is not None
        abw = round(w - ref, m.dezimal)
        saetze, kat, massnahme = _freitext(st, _Befund(klasse, outside, m, wert_vis), r)
        draws = {
            "typo": r[U.N_TYPO], "translit": r[U.N_TRANSLIT], "gross": r[U.N_GROSS], "klein": r[U.N_KLEIN],
            "satzzeichen": r[U.N_SATZ], "leerzeichen": r[U.N_LEER], "mojibake": r[U.N_MOJI],
        }  # fmt: skip
        text, tags = noise.apply_all(" ".join(noise.satz(s) for s in saetze), cfg.noise, draws, nrng)

        d = cfg.date_start + dt.timedelta(days=int(r[U.DATUM] * cfg.date_days))
        mid = str(ID_BASE + chunk * cfg.chunk_rows + j)
        for col, val in (
            ("qmeldung_id", mid), ("werk", werk.werk), ("lieferant", _pick(st.suppliers, r[U.LIEFERANT])),
            ("materialnr", f"20{int(r[U.MATNR] * 1_000_000):06d}"),
            ("charge", f"{d.year % 100:02d}{d.month:02d}-{int(r[U.CHARGE] * 1000):03d}"),
            ("meldungsdatum", f"{d.day:02d}.{d.month:02d}.{d.year}"), ("merkmal", m.name),
            ("messwert", wert_vis), ("toleranz", _tolerance(m, r[U.TOL_STIL])),
            ("urteil", "i.O." if klasse == "kein Fehler" else "n.i.O."), ("freitext", text),
        ):  # fmt: skip
            vis[col].append(val)
        for col, val in (
            ("qmeldung_id", mid), ("fehlerklasse", klasse), ("ursachenkategorie", kat),
            ("massnahme", massnahme),
            ("messwert", w), ("einheit", m.einheit), ("abweichung", abw),
            ("abweichung_mm", abw if m.einheit == "mm" else None), ("messwert_in_toleranz", m.inside(w)),
            ("werk", werk.werk), ("noise", ",".join(tags)),
        ):  # fmt: skip
            gold[col].append(val)
    return vis, gold
