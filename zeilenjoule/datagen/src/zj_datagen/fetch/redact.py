"""Remove contact details from real free text (rule 8: never collect personal data).

TED descriptions occasionally contain e-mail addresses or phone numbers of contact persons;
CC BY 4.0 requires marking changes, so the dataset card documents this redaction."""

from __future__ import annotations

import re

MARK = "[entfernt]"
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# German phone formats: +49 211 123456, 0211/123456, (0211) 12 34 56, 0211-123456 (≥ 7 digits overall)
PHONE = re.compile(r"(?:\+49|0049|\(0\d{2,5}\)|0\d{2,5})(?:[ \-/]?\d){5,12}")


def redact(text: str | None) -> tuple[str | None, int]:
    """(redacted text, number of replacements)."""
    if not text:
        return text, 0
    text, n1 = EMAIL.subn(MARK, text)
    text, n2 = PHONE.subn(MARK, text)
    return text, n1 + n2
