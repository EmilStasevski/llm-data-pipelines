"""Central place for data locations. Override the root with RUHRBENCH_DATA."""

import os
from pathlib import Path

ROOT = Path(os.environ.get("RUHRBENCH_DATA", Path(__file__).resolve().parents[2] / "data"))
RAW = ROOT / "raw"
INTERIM = ROOT / "interim"
GOLD = ROOT / "gold"


def ensure(*dirs: Path) -> None:
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
