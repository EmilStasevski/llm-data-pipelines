"""Tiny dependency-free German/English guesser based on function-word counts."""

from __future__ import annotations

import re

# Words that are frequent in one language and (nearly) absent in the other.
_DE = {"und", "der", "die", "das", "nicht", "ist", "für", "mit", "von", "den", "im",
       "auf", "dem", "zu", "wird", "sich", "auch", "ein", "eine", "des", "wurde", "bei"}  # fmt: skip
_EN = {"and", "the", "of", "to", "is", "for", "with", "that", "on", "as", "by",
       "are", "this", "from", "which", "have", "has", "were", "be"}  # fmt: skip
_WORD = re.compile(r"[a-zäöüß]+")


def guess_lang(text: str, sample: int = 300_000) -> str:
    """'de', 'en', 'mixed' or 'unk' (too little text)."""
    words = _WORD.findall(text[:sample].lower())
    de = sum(w in _DE for w in words)
    en = sum(w in _EN for w in words)
    if de + en < 5:
        return "unk"
    if de > 1.5 * en:
        return "de"
    if en > 1.5 * de:
        return "en"
    return "mixed"
