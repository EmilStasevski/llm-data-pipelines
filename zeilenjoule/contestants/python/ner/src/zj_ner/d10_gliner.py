"""Track D contestant d10_gliner: no LLM — Track A rules (level 0) + SauerkrautLM-GLiNER spans (level 1).

Rules fill komponente / sicherheitsrelevant / stillstand_minuten / ersatzteil_materialnr; GLiNER (labels
Bauteil, Schaden, Ursache, Maßnahme; threshold 0.3, chosen on the dev set) fills schadensart / ursache /
massnahme where a span maps onto exactly one catalogue name (zj_ner.fields). Unmapped: schadensart =
„Sonstiges“, ursache / massnahme = null — the output is always schema-valid JSON. GLiNER runs on the GPU
(fails loudly without CUDA, rule 6).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from zj_ner import fields as fx

REPO = Path(__file__).resolve().parents[5]
MODEL = REPO / "models" / "ner" / "SauerkrautLM-GLiNER"
THRESHOLD = 0.3
BATCH = 32


def load_gliner() -> Any:
    import torch  # noqa: PLC0415
    from gliner import GLiNER  # noqa: PLC0415

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA not available: GLiNER would run on the CPU (rule 6)")
    return GLiNER.from_pretrained(str(MODEL), local_files_only=True).to("cuda")


def spans(model: Any, rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    texts = [fx.text_of(r) for r in rows]
    return model.inference(texts, list(fx.LABELS.values()), threshold=THRESHOLD, batch_size=BATCH)


def main(argv: list[str] | None = None) -> int:
    from zj_reference import track_a as ta  # noqa: PLC0415
    from zjmark import result, stage  # noqa: PLC0415

    p = argparse.ArgumentParser(prog="python -m zj_ner.d10_gliner")
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--threads", type=int, default=0)
    a = p.parse_args(argv)
    with stage("load"):
        import torch  # noqa: PLC0415

        lex, cat = ta.load_lexicon(), fx.Catalogue.build()
        model = load_gliner()
        rows = pq.read_table(a.input).to_pylist()  # pyright: ignore[reportUnknownMemberType]
        spans(model, rows[:1])  # kernels warm
    with stage("extract"):
        ents = spans(model, rows)
        out: list[dict[str, Any]] = []
        resolved = dict.fromkeys(fx.SPAN_FIELDS, 0)
        for raw, e in zip(rows, ents, strict=True):
            r = fx.level0(raw, lex)
            fx.level1(r, e, cat, THRESHOLD)
            for f in fx.SPAN_FIELDS:
                resolved[f] += f in r.resolved
            out.append(
                {
                    "meldung_id": raw["meldung_id"],
                    "text": json.dumps(r.as_json(), ensure_ascii=False),
                    "completion_tokens": 0,
                    "prompt_tokens": 0,
                    "finish_reason": "stop",
                }
            )
    with stage("write"):
        a.output.write_text("".join(json.dumps(o, ensure_ascii=False) + "\n" for o in out), "utf-8")
    result(
        rows=len(rows),
        notes="Track A rules + SauerkrautLM-GLiNER spans mapped to the catalogue, no LLM",
        extra={
            "engine": "cuda",
            "model": "VAGOsolutions/SauerkrautLM-GLiNER@ba967ee030b7",
            "threshold": THRESHOLD,
            "span_resolved_share": {f: round(v / len(rows), 4) for f, v in resolved.items()},
            "gpu_mem_mb": round(torch.cuda.max_memory_allocated() / 2**20),
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
