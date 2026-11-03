"""Level-0/1 PDF extraction baseline (PyMuPDF). Later levels (Docling, Rust) plug in beside it."""

from __future__ import annotations

import re
import time
from pathlib import Path

import polars as pl
import pymupdf as fitz  # PyMuPDF

# "Nachhal-\ntigkeit" -> "Nachhaltigkeit"; keeps "E-\nMobilität"-style hyphens (uppercase next).
_HYPHEN = re.compile(r"(?<=[a-zäöüß])-\n(?=[a-zäöüß])")


def dehyphenate(text: str) -> str:
    return _HYPHEN.sub("", text)


def extract_pdf(path: Path) -> pl.DataFrame:
    """One row per page: doc, page, text, n_chars."""
    rows = []
    with fitz.open(path) as doc:
        for i, page in enumerate(doc, start=1):
            text = dehyphenate(page.get_text("text"))
            rows.append({"doc": path.name, "page": i, "text": text, "n_chars": len(text)})
    return pl.DataFrame(
        rows, schema={"doc": pl.Utf8, "page": pl.Int64, "text": pl.Utf8, "n_chars": pl.Int64}
    )


def parse_dir(pdf_dir: Path, out: Path) -> dict:
    """Parse every PDF in pdf_dir into one parquet; returns simple timing stats."""
    pdfs = sorted(pdf_dir.glob("*.pdf"))
    t0 = time.perf_counter()
    frames = [extract_pdf(p) for p in pdfs]
    if not frames:
        raise FileNotFoundError(f"no PDFs found in {pdf_dir}")
    df = pl.concat(frames)
    elapsed = time.perf_counter() - t0
    out.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(out)
    pages = df.height
    return {
        "docs": len(pdfs),
        "pages": pages,
        "seconds": round(elapsed, 3),
        "pages_per_s": round(pages / elapsed, 1) if elapsed else None,
    }
