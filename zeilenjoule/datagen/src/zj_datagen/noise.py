"""Noise injection for free text (PLAN §6.1). All functions are deterministic given the RNG."""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np

# Track A step 1 repairs exactly this table; the generator only produces mojibake for these
# characters, so the repair is an exact inverse (tested).
MOJIBAKE_CHARS = "äöüßÄÖÜ€"
MOJIBAKE = {c: c.encode("utf-8").decode("cp1252") for c in MOJIBAKE_CHARS}
REPAIR = {v: k for k, v in MOJIBAKE.items()}

TRANSLIT = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"}

# QWERTZ neighbours for keyboard typos (lower case letters only)
_ROWS = ["qwertzuiopü", "asdfghjklöä", "yxcvbnm"]
NEIGHBOURS: dict[str, str] = {}
for _r, _row in enumerate(_ROWS):
    for _i, _ch in enumerate(_row):
        _n = [_row[j] for j in (_i - 1, _i + 1) if 0 <= j < len(_row)]
        for _rr in (_r - 1, _r + 1):
            if 0 <= _rr < len(_ROWS):
                _n += [_ROWS[_rr][j] for j in (_i, _i + 1) if 0 <= j < len(_ROWS[_rr])]
        NEIGHBOURS[_ch] = "".join(_n)


def mojibake(text: str) -> str:
    return "".join(MOJIBAKE.get(c, c) for c in text)


def repair_mojibake(text: str) -> str:
    """Reference inverse (the Track A rule): longest keys first so 'â‚¬' wins over shorter ones."""
    for bad in sorted(REPAIR, key=len, reverse=True):
        text = text.replace(bad, REPAIR[bad])
    return text


def transliterate(text: str) -> str:
    return "".join(TRANSLIT.get(c, c) for c in text)


def typo(text: str, rng: np.random.Generator) -> str:
    """One keyboard typo (neighbour substitution, deletion, doubling, transposition) in a word ≥ 4 letters."""
    words = text.split(" ")
    cand = [i for i, w in enumerate(words) if len(w) >= 4 and w.isalpha()]
    if not cand:
        return text
    i = cand[int(rng.integers(len(cand)))]
    w = words[i]
    pos = int(rng.integers(1, len(w) - 1))  # keep first letter (more realistic, keeps case)
    kind = int(rng.integers(4))
    ch = w[pos]
    if kind == 0 and ch.lower() in NEIGHBOURS:
        nb = NEIGHBOURS[ch.lower()]
        rep = nb[int(rng.integers(len(nb)))]
        w = w[:pos] + (rep.upper() if ch.isupper() else rep) + w[pos + 1 :]
    elif kind == 1:
        w = w[:pos] + w[pos + 1 :]
    elif kind == 2:
        w = w[:pos] + ch + w[pos:]
    else:
        w = w[: pos - 1] + w[pos] + w[pos - 1] + w[pos + 1 :]
    words[i] = w
    return " ".join(words)


def satz(s: str) -> str:
    """Terminate a sentence with exactly one period (phrases like 'Messung i.O.' already end in one)."""
    return s if s.endswith(".") else s + "."


def upper(text: str) -> str:
    """Upper case that keeps 'µ': str.upper() maps it to Greek capital mu (U+039C), which is not
    encodable in cp1252 and would break the export (found by the strict cp1252 writer)."""
    return "".join(c if c == "µ" else c.upper() for c in text)


def drop_punctuation(text: str) -> str:
    return text.replace(",", "").rstrip(".").replace(". ", " ")


def extra_whitespace(text: str, rng: np.random.Generator) -> str:
    words = text.split(" ")
    if len(words) > 1:
        i = int(rng.integers(len(words) - 1))
        words[i] += " " * int(rng.integers(1, 3))
    return (" " if rng.random() < 0.5 else "") + " ".join(words) + ("  " if rng.random() < 0.3 else "")


# keys of the shared `noise:` block in config/datagen.yaml that apply_all handles (englisch is phrase-level)
TEXT_NOISE = ("typo", "translit", "gross", "klein", "satzzeichen", "leerzeichen", "mojibake")


def apply_all(
    text: str, rates: Mapping[str, float], draws: Mapping[str, float], rng: np.random.Generator
) -> tuple[str, list[str]]:
    """Apply text noise where draws[key] < rates[key]; order matters (mojibake last, it corrupts the
    final bytes; upper and lower case are mutually exclusive). Returns (text, applied tags)."""
    tags: list[str] = []

    def hit(key: str) -> bool:
        return draws[key] < rates[key]

    if hit("typo"):
        text = typo(text, rng)
        tags.append("typo")
    if hit("translit"):
        text = transliterate(text)
        tags.append("translit")
    if hit("gross"):
        text = upper(text)
        tags.append("gross")
    elif hit("klein"):
        text = text.lower()
        tags.append("klein")
    if hit("satzzeichen"):
        text = drop_punctuation(text)
        tags.append("satzzeichen")
    if hit("leerzeichen"):
        text = extra_whitespace(text, rng)
        tags.append("leerzeichen")
    if hit("mojibake"):
        text = mojibake(text)
        tags.append("mojibake")
    return text, tags
