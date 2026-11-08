"""Generate tables at a scale: parallel chunks, ordered writing, deterministic manifest."""

from __future__ import annotations

import datetime as dt
import json
import multiprocessing
import os
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any

import pyarrow as pa
import yaml

from zj_datagen import lexicon, qualitaetsmeldungen, stoermeldungen
from zj_datagen.writer import TableWriter, write_xlsx_sample

SCALES = {"S": 10_000, "M": 100_000, "L": 1_000_000, "XL": 10_000_000}
XLSX_SAMPLE_ROWS = 1000

STOER_GOLD = pa.schema([
    ("meldung_id", pa.string()), ("komponente", pa.string()), ("schadensart", pa.string()),
    ("ursache", pa.string()), ("massnahme", pa.string()), ("sicherheitsrelevant", pa.bool_()),
    ("stillstand_minuten", pa.int32()), ("kosten_eur", pa.float64()), ("ersatzteil_materialnr", pa.string()),
    ("datum_iso", pa.date32()), ("werk", pa.string()), ("regierungsbezirk", pa.string()),
    ("nuts2", pa.string()), ("noise", pa.string()),
])  # fmt: skip
QUALI_GOLD = pa.schema([
    ("qmeldung_id", pa.string()), ("fehlerklasse", pa.string()), ("ursachenkategorie", pa.string()),
    ("massnahme", pa.string()), ("messwert", pa.float64()), ("einheit", pa.string()),
    ("abweichung", pa.float64()), ("abweichung_mm", pa.float64()), ("messwert_in_toleranz", pa.bool_()),
    ("werk", pa.string()), ("noise", pa.string()),
])  # fmt: skip

Chunk = tuple[dict[str, list[Any]], dict[str, list[Any]]]


@dataclass(frozen=True)
class Table:
    name: str
    visible: tuple[str, ...]
    gold_schema: pa.Schema
    rows_factor: Callable[[dict[str, Any]], float]


TABLES = {
    "stoermeldungen": Table("stoermeldungen", stoermeldungen.VISIBLE, STOER_GOLD, lambda _: 1.0),
    "qualitaetsmeldungen": Table(
        "qualitaetsmeldungen",
        qualitaetsmeldungen.VISIBLE,
        QUALI_GOLD,
        lambda cfg: float(cfg["qualitaetsmeldungen"]["rows_per_scale_row"]),
    ),
}

# per-worker-process state, set once by _init (exactly one of the two is used per pool)
_stoer: tuple[lexicon.Lexicon, stoermeldungen.Config] | None = None
_quali: qualitaetsmeldungen.QState | None = None


def _init(table: str, lexdir: str, cfg: dict[str, Any]) -> None:
    global _stoer, _quali  # noqa: PLW0603  (process-pool initializer state)
    lex = lexicon.load(Path(lexdir))
    if table == "stoermeldungen":
        _stoer = (lex, stoermeldungen.Config.from_dict(cfg))
    else:
        qcfg = qualitaetsmeldungen.Config.from_dict(cfg)
        qlex = qualitaetsmeldungen.load_lexicon(Path(lexdir))
        suppliers = qualitaetsmeldungen.lieferanten(qcfg.seed, qcfg.n_lieferanten)
        _quali = qualitaetsmeldungen.QState(lex.werke, qlex, suppliers, qcfg)


def _chunk(job: tuple[int, int]) -> Chunk:
    chunk, n = job
    if _stoer is not None:
        vis, gold = stoermeldungen.generate_chunk(*_stoer, chunk, n)
        gold["datum_iso"] = [dt.date.fromisoformat(x) if x else None for x in gold["datum_iso"]]
        return vis, gold
    assert _quali is not None, "worker not initialised"
    return qualitaetsmeldungen.generate_chunk(_quali, chunk, n)


def load_config(repo: Path) -> dict[str, Any]:
    return dict(yaml.safe_load((repo / "config" / "datagen.yaml").read_text(encoding="utf-8")))


def generate(
    repo: Path, scale: str, out: Path, workers: int | None = None, table: str = "stoermeldungen"
) -> dict[str, Any]:
    """Generate `table` at `scale` into out/<table>/<scale>/ and return the manifest."""
    t = TABLES[table]
    cfg = load_config(repo)
    rows = round(SCALES[scale] * t.rows_factor(cfg))
    lexdir = repo / "spec" / "lexicon"
    lex = lexicon.load(lexdir)
    chunk_rows = int(cfg["chunk_rows"])
    jobs = [(k, min(chunk_rows, rows - k * chunk_rows)) for k in range(-(-rows // chunk_rows))]
    outdir = out / table / scale
    w = TableWriter(outdir, table, t.visible, t.gold_schema)
    sample: list[list[Any]] = []
    workers = workers or max(1, min(len(jobs), (os.cpu_count() or 2) - 2))
    # spawn, not fork: pyarrow starts threads, and fork() from a multi-threaded process can deadlock
    ctx = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(
        workers, mp_context=ctx, initializer=_init, initargs=(table, str(lexdir), cfg)
    ) as ex:
        for vis, gold in ex.map(_chunk, jobs):  # map preserves order → deterministic files
            if table == "stoermeldungen" and scale == "S" and len(sample) < XLSX_SAMPLE_ROWS:
                take = XLSX_SAMPLE_ROWS - len(sample)
                sample += [list(r) for r in zip(*(vis[c][:take] for c in t.visible), strict=True)]
            w.write(vis, gold)
    files = w.close()
    if sample:
        p = outdir / f"{table}_sample.xlsx"
        write_xlsx_sample(p, t.visible, sample)
        files["sample.xlsx"] = {
            "path": p.name,
            "bytes": p.stat().st_size,
            "sha256": None,
        }  # xlsx zip is not byte-stable
    manifest: dict[str, Any] = {
        "table": table,
        "scale": scale,
        "rows": w.rows,
        "seed": cfg["seed"],
        "chunk_rows": chunk_rows,
        "lexicon_sha256": lex.sha256,
        "config": cfg,
        "versions": {
            d: metadata.version(d) for d in ("zj-datagen", "numpy", "pyarrow", "pyyaml", "openpyxl", "faker")
        },
        "files": files,
    }
    (outdir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return manifest
