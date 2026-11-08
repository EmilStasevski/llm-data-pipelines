"""Synthetic SAP-PM-like fault notifications (Störmeldungen): labels first, then German text.

Determinism and prefix stability: rows are generated in fixed chunks of `chunk_rows`; chunk k is
driven only by SeedSequence(seed, spawn_key=(TABLE_ID, k)). Row i therefore depends only on
(seed, i), and scale S is a prefix of M, M of L, L of XL.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass
from enum import IntEnum, auto
from statistics import NormalDist
from typing import Any

import numpy as np

from zj_datagen import noise, render
from zj_datagen.lexicon import Komponente, Lexicon, Schadensart, Werk

TABLE_ID = 1
ID_BASE = 2_000_000_000  # 10-digit SAP-like notification numbers, fixed width → string sort = numeric sort

VISIBLE = (
    "meldung_id", "werk", "anlage", "equipment_nr", "meldungsdatum", "schicht",
    "prioritaet", "meldungstext", "stillstand", "kosten", "status",
)  # fmt: skip
GOLD = (
    "meldung_id", "komponente", "schadensart", "ursache", "massnahme", "sicherheitsrelevant",
    "stillstand_minuten", "kosten_eur", "ersatzteil_materialnr", "datum_iso",
    "werk", "regierungsbezirk", "nuts2", "noise",
)  # fmt: skip

SCHICHTEN = ("Früh", "Spät", "Nacht")
SCHICHT_P = (0.40, 0.35, 0.25)
MATNR_PHRASEN = (
    "ET Mat.-Nr. {m} verbaut",
    "Ersatzteil {m} eingebaut",
    "Material-Nr.: {m}",
    "Mat.Nr. {m} getauscht",
)
STILL_PHRASEN = ("Stillstand {s}", "Anlage stand {s}", "Ausfallzeit {s}")
STILL_EN = "Downtime {s}"
_STD_NORMAL = NormalDist()  # costs are log-normal around exp(6.5) ≈ 665 €


class U(IntEnum):
    """Columns of the per-chunk uniform matrix: one fixed column per decision, so a row's values
    never depend on how many decisions earlier rows needed."""

    WERK = 0
    ANLAGE = auto()
    KOMP = auto()
    FORM = auto()
    FORM_K = auto()
    SCHADEN_TYP = auto()
    SCHADEN = auto()
    PHRASE = auto()
    KOMPOSITUM = auto()
    ORT = auto()
    EQ_TEXT = auto()
    EQ_SLOT = auto()
    URSACHE = auto()
    URSACHE_UNB = auto()
    URSACHE_WAHL = auto()
    URSACHE_PH = auto()
    MASSN = auto()
    MASSN_OFFEN = auto()
    MASSN_WAHL = auto()
    MASSN_PH = auto()
    SICHER = auto()
    SICHER_PH = auto()
    SICHER_POS = auto()
    MATNR = auto()
    MATNR_NUM = auto()
    MATNR_PH = auto()
    STILL_LEER = auto()
    STILL_ART = auto()
    STILL_WERT = auto()
    STILL_STIL = auto()
    STILL_TEXT = auto()
    STILL_TEXT_PH = auto()
    KOSTEN_LEER = auto()
    KOSTEN_KA = auto()
    KOSTEN_NEG = auto()
    KOSTEN_WERT = auto()
    KOSTEN_RUND = auto()
    KOSTEN_STIL = auto()
    DATUM = auto()
    DATUM_STIL = auto()
    DATUM_UNG = auto()
    SCHICHT = auto()
    PRIO = auto()
    STATUS = auto()
    ORDER = auto()
    N_ENGLISCH = auto()
    N_TYPO = auto()
    N_TRANSLIT = auto()
    N_MOJI = auto()
    N_GROSS = auto()
    N_KLEIN = auto()
    N_SATZ = auto()
    N_LEER = auto()


N_COLS = len(U)


@dataclass(frozen=True)
class Config:
    seed: int
    chunk_rows: int
    date_start: dt.date
    date_days: int
    p: dict[str, float]
    noise: dict[str, float]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Config:
        start, end = (dt.date.fromisoformat(x) for x in d["date_range"])
        sm = d["stoermeldungen"]
        return cls(
            seed=int(d["seed"]),
            chunk_rows=int(d["chunk_rows"]),
            date_start=start,
            date_days=(end - start).days + 1,
            p=dict(sm["p"]),
            noise=dict(d["noise"]),
        )


def _pick[T](seq: tuple[T, ...], u: float) -> T:
    return seq[min(int(u * len(seq)), len(seq) - 1)]


def _index(n: int, u: float) -> int:
    return min(int(u * n), n - 1)


def _weighted(p: tuple[float, ...], u: float) -> int:
    acc = 0.0
    for i, w in enumerate(p):
        acc += w
        if u < acc:
            return i
    return len(p) - 1


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def equipment(werk_idx: int, anlage_idx: int, komp_idx: int, slot: int, k: Komponente) -> str:
    """Stable equipment number for (plant, line, component, slot); two families like 'P-4711' / 'M 0815-A'."""
    num = (werk_idx * 7919 + anlage_idx * 1031 + komp_idx * 131 + slot * 17) % 9000 + 1000
    if (werk_idx + komp_idx + slot) % 3 == 0:
        return f"{k.eq} {num:04d}-{'ABC'[slot % 3]}"
    return f"{k.eq}-{num:04d}"


def _prio(sicher: bool, schaden: str, u: float) -> int:
    if sicher:
        return 1
    if schaden in ("Bruch", "Kurzschluss", "Leckage"):
        p: tuple[float, ...] = (0.3, 0.4, 0.2, 0.1)
    elif schaden in ("Fehlalarm", "Sonstiges"):
        p = (0.05, 0.15, 0.4, 0.4)
    else:
        p = (0.1, 0.3, 0.4, 0.2)
    return _weighted(p, u) + 1


@dataclass
class _Row:
    """Labels of one row, decided before any text is rendered."""

    werk: Werk
    anlage: str
    eq: str
    komp: Komponente
    form: str
    genus: str
    schaden: Schadensart
    englisch: bool


def _labels(lex: Lexicon, p: dict[str, float], pn: dict[str, float], r: np.ndarray) -> _Row:
    wi = _index(len(lex.werke), r[U.WERK])
    werk = lex.werke[wi]
    anlagen = lex.anlagen[werk.sektor]
    ai = _index(len(anlagen), r[U.ANLAGE])
    ki = _index(len(lex.komponenten), r[U.KOMP])
    komp = lex.komponenten[ki]
    form, genus = komp.formen[0] if r[U.FORM] < p["form_kanonisch"] else _pick(komp.formen, r[U.FORM_K])
    keys = tuple(s.key for s in lex.schadensarten)
    sk = (
        _pick(komp.schaeden, r[U.SCHADEN])
        if r[U.SCHADEN_TYP] < p["schaden_typisch"]
        else _pick(keys, r[U.SCHADEN])
    )
    return _Row(
        werk=werk,
        anlage=f"{anlagen[ai]} {(wi + ai) % 3 + 1}",
        eq=equipment(wi, ai, ki, _index(3, r[U.EQ_SLOT]), komp),
        komp=komp,
        form=form,
        genus=genus,
        schaden=lex.schadensart(sk),
        englisch=bool(r[U.N_ENGLISCH] < pn["englisch"]),
    )


def _damage_sentence(row: _Row, p: dict[str, float], r: np.ndarray) -> tuple[str, bool]:
    """(sentence, whether an English phrase was used)."""
    sa, k = row.schaden, row.komp
    if sa.kompositum and k.stamm and r[U.KOMPOSITUM] < p["kompositum"]:
        satz = k.stamm + sa.kompositum  # "Druckluftleitungs" + "leckage"
        row.englisch = False  # a compound is German by construction; the tag must not claim otherwise
    else:
        tpl = _pick(sa.en if row.englisch else sa.phrasen, r[U.PHRASE])
        satz = tpl.replace("{N}", render.nominativ(row.form, row.genus)).replace(
            "{A}", render.an_dativ(row.form, row.genus)
        )
    if r[U.ORT] < p["ort_anlage"]:
        satz += f" in {row.anlage}"
    if r[U.EQ_TEXT] < p["equipment_im_text"]:
        satz += f" ({row.eq})"
    return noise.satz(_cap(satz)), row.englisch


def _cause(lex: Lexicon, p: dict[str, float], r: np.ndarray) -> tuple[str | None, str | None]:
    """(sentence, gold cause)."""
    if r[U.URSACHE] >= p["ursache"]:
        return None, None
    if r[U.URSACHE_UNB] < p["ursache_unbekannt"]:
        return noise.satz(_cap(_pick(lex.ursache_unbekannt, r[U.URSACHE_PH]))), None
    ur = _pick(lex.ursachen, r[U.URSACHE_WAHL])
    return noise.satz(_cap(_pick(ur.phrasen, r[U.URSACHE_PH]))), ur.name


def _action(lex: Lexicon, p: dict[str, float], r: np.ndarray) -> tuple[str | None, str | None]:
    """(sentence, gold action)."""
    if r[U.MASSN] >= p["massnahme"]:
        return None, None
    if r[U.MASSN_OFFEN] < p["massnahme_offen"]:
        return noise.satz(_cap(_pick(lex.massnahme_offen, r[U.MASSN_PH]))), None
    ma = _pick(lex.massnahmen, r[U.MASSN_WAHL])
    return noise.satz(_cap(_pick(ma.phrasen, r[U.MASSN_PH]))), ma.name


def _downtime(p: dict[str, float], r: np.ndarray, englisch: bool) -> tuple[str, int | None, str | None]:
    """(visible column, gold minutes, optional sentence repeating it in the text)."""
    if r[U.STILL_LEER] < p["stillstand_leer"]:
        return "", None, None
    art, w = r[U.STILL_ART], r[U.STILL_WERT]
    if art < 0.7:
        minutes = (int(w * 32) + 1) * 15  # quarter hours up to 8 h
    elif art < 0.85:
        minutes = (int(w * 12) + 1) * 60  # whole hours up to 12 h
    else:
        minutes = int(w * 240) + 1  # odd minute values
    vis, gold = render.stillstand(minutes, _index(render.STILLSTAND_STYLES, r[U.STILL_STIL]))
    satz = None
    if r[U.STILL_TEXT] < p["stillstand_im_text"]:
        satz = noise.satz((STILL_EN if englisch else _pick(STILL_PHRASEN, r[U.STILL_TEXT_PH])).format(s=vis))
    return vis, gold, satz


def _costs(p: dict[str, float], r: np.ndarray) -> tuple[str, float | None]:
    if r[U.KOSTEN_LEER] < p["kosten_leer"]:
        return "", None
    if r[U.KOSTEN_KA] < p["kosten_ka"] / (1 - p["kosten_leer"]):  # overall rate = kosten_ka
        return "k.A.", None
    z = _STD_NORMAL.inv_cdf(min(max(float(r[U.KOSTEN_WERT]), 1e-12), 1 - 1e-12))
    cents = round(math.exp(6.5 + 1.1 * z) * 100)
    if r[U.KOSTEN_RUND] < 0.3:
        cents = max(100, round(cents, -2))
    if r[U.KOSTEN_NEG] < p["kosten_negativ"]:
        cents = -cents
    return render.kosten(cents, _index(render.KOSTEN_STYLES, r[U.KOSTEN_STIL]))


def _date(cfg: Config, r: np.ndarray) -> tuple[str, str | None]:
    if r[U.DATUM_UNG] < cfg.p["datum_ungueltig"]:
        return _pick(render.UNGUELTIGE_DATEN, r[U.DATUM]), None
    d = cfg.date_start + dt.timedelta(days=int(r[U.DATUM] * cfg.date_days))
    return render.datum(d, _index(render.DATUM_STYLES, r[U.DATUM_STIL]))


def generate_chunk(
    lex: Lexicon, cfg: Config, chunk: int, n: int
) -> tuple[dict[str, list[Any]], dict[str, list[Any]]]:
    """Rows [chunk*chunk_rows, chunk*chunk_rows + n). Returns (visible columns, gold columns)."""
    if n > cfg.chunk_rows:
        raise ValueError("n must be <= chunk_rows")
    dec_ss, noise_ss = np.random.SeedSequence(cfg.seed, spawn_key=(TABLE_ID, chunk)).spawn(2)
    # always draw the full chunk so a shorter last chunk equals the prefix of a full one
    u = np.random.default_rng(dec_ss).random((cfg.chunk_rows, N_COLS))[:n]
    nrng = np.random.default_rng(noise_ss)
    p, pn = cfg.p, cfg.noise
    vis: dict[str, list[Any]] = {c: [] for c in VISIBLE}
    gold: dict[str, list[Any]] = {c: [] for c in GOLD}

    for j in range(n):
        r = u[j]
        row = _labels(lex, p, pn, r)
        first, englisch = _damage_sentence(row, p, r)
        saetze = [first]
        cause_satz, ursache = _cause(lex, p, r)
        action_satz, massnahme = _action(lex, p, r)
        saetze += [s for s in (cause_satz, action_satz) if s]
        matnr = None
        if r[U.MATNR] < p["materialnr"]:
            matnr = f"10{int(r[U.MATNR_NUM] * 1_000_000):06d}"
            saetze.append(noise.satz(_pick(MATNR_PHRASEN, r[U.MATNR_PH]).format(m=matnr)))
        still_vis, still_gold, still_satz = _downtime(p, r, englisch)
        if still_satz:
            saetze.append(still_satz)
        sicher = bool(r[U.SICHER] < p["sicherheit"])
        if sicher:
            s_satz = noise.satz(_cap(_pick(lex.sicherheit_phrasen, r[U.SICHER_PH])))
            saetze.insert(0 if r[U.SICHER_POS] < 0.5 else len(saetze), s_satz)
        if len(saetze) > 2 and r[U.ORDER] < 0.3:  # light reordering of the trailing sentences
            saetze[1], saetze[2] = saetze[2], saetze[1]
        draws = {
            "typo": r[U.N_TYPO], "translit": r[U.N_TRANSLIT], "gross": r[U.N_GROSS], "klein": r[U.N_KLEIN],
            "satzzeichen": r[U.N_SATZ], "leerzeichen": r[U.N_LEER], "mojibake": r[U.N_MOJI],
        }  # fmt: skip
        text, tags = noise.apply_all(" ".join(saetze), pn, draws, nrng)
        if englisch:
            tags.insert(0, "englisch")

        kosten_vis, kosten_gold = _costs(p, r)
        datum_vis, datum_gold = _date(cfg, r)
        if massnahme is None:
            status = "offen" if r[U.STATUS] < 0.6 else "in Arbeit"
        else:
            status = ("abgeschlossen", "in Arbeit", "storniert")[_weighted((0.85, 0.1, 0.05), r[U.STATUS])]
        mid = str(ID_BASE + chunk * cfg.chunk_rows + j)

        for col, val in (
            ("meldung_id", mid), ("werk", row.werk.werk), ("anlage", row.anlage), ("equipment_nr", row.eq),
            ("meldungsdatum", datum_vis), ("schicht", SCHICHTEN[_weighted(SCHICHT_P, r[U.SCHICHT])]),
            ("prioritaet", str(_prio(sicher, row.schaden.key, r[U.PRIO]))), ("meldungstext", text),
            ("stillstand", still_vis), ("kosten", kosten_vis), ("status", status),
        ):  # fmt: skip
            vis[col].append(val)
        for col, val in (
            ("meldung_id", mid), ("komponente", row.komp.name), ("schadensart", row.schaden.key),
            ("ursache", ursache), ("massnahme", massnahme), ("sicherheitsrelevant", sicher),
            ("stillstand_minuten", still_gold), ("kosten_eur", kosten_gold), ("ersatzteil_materialnr", matnr),
            ("datum_iso", datum_gold), ("werk", row.werk.werk),
            ("regierungsbezirk", row.werk.regierungsbezirk), ("nuts2", row.werk.nuts2),
            ("noise", ",".join(tags)),
        ):  # fmt: skip
            gold[col].append(val)
    return vis, gold
