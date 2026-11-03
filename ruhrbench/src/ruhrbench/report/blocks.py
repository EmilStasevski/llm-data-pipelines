"""Replace `<!-- generated:NAME -->` blocks in Markdown files and render small tables.

Numbers in docs come from code, never from typing: prose stays hand-written, tables live in
marked blocks that a report command rewrites.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

import polars as pl


def markdown_table(df: pl.DataFrame) -> str:
    def cell(value: object) -> str:
        return "" if value is None else str(value).replace("|", "\\|")

    lines = [
        "| " + " | ".join(df.columns) + " |",
        "|" + "---|" * df.width,
        *("| " + " | ".join(cell(v) for v in row) + " |" for row in df.rows()),
    ]
    return "\n".join(lines)


def update_blocks(path: Path, blocks: dict[str, str]) -> Sequence[str]:
    """Rewrite each named block in place. Raises if a block is missing, so typos cannot hide."""
    text = path.read_text(encoding="utf-8")
    for name, body in blocks.items():
        pattern = re.compile(
            rf"(<!-- generated:{re.escape(name)} -->\n).*?(<!-- /generated -->)",
            re.DOTALL,
        )
        replacement = rf"\g<1>{body.replace(chr(92), chr(92) * 2)}\n\g<2>"
        text, n = pattern.subn(replacement, text)
        if n != 1:
            raise ValueError(f"expected one block {name!r} in {path}, found {n}")
    path.write_text(text, encoding="utf-8")
    return list(blocks)
