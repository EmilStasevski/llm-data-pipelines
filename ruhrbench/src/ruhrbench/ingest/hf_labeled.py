"""Ready-labeled German datasets from the Hugging Face Hub (needs: uv sync --extra hf).

Datasets are fetched as the Hub's auto-converted Parquet files (branch refs/convert/parquet).
This works for old script-based datasets such as deepset/germanquad, which recent versions
of `datasets` refuse to load ("Dataset scripts are no longer supported").

Only datasets I am confident exist are listed. Add NER / topic-classification sets
(GermEval-14, 10kGNAD, ...) after checking their current Hub ids.
"""

from __future__ import annotations

import shutil
from pathlib import Path

PARQUET_REVISION = "refs/convert/parquet"

DATASETS = {
    # extractive QA with gold answers + contexts: retrieval and RAG evaluation
    "germanquad": "deepset/germanquad",
}


def fetch(name: str, out_dir: Path) -> list[Path]:
    from huggingface_hub import snapshot_download

    out_dir.mkdir(parents=True, exist_ok=True)
    snap = Path(
        snapshot_download(
            repo_id=DATASETS[name],
            repo_type="dataset",
            revision=PARQUET_REVISION,
            allow_patterns=["*.parquet"],
        )
    )
    written = []
    for f in sorted(snap.rglob("*.parquet")):
        # layout in the parquet branch: <config>/<split>/0000.parquet
        dest = out_dir / f"{name}_{f.parent.name}_{f.stem}.parquet"
        shutil.copyfile(f, dest)  # follows the cache symlink, copies the real bytes
        written.append(dest)
    if not written:
        raise RuntimeError(f"no parquet files found for {DATASETS[name]} at {PARQUET_REVISION}")
    return written
