"""Track D contestant d11_cascade: rules for the deterministic fields, the LLM only for the semantic ones.

Level 0 (Track A rules) answers sicherheitsrelevant / stillstand_minuten / ersatzteil_materialnr — exact on
the dev set. Level 2 = the most economical LLM configuration of the main matrix (llama.cpp, Gemma 4 E4B Q4,
3-shot, N = 16) with a *reduced* prompt and schema: only komponente / schadensart / ursache / massnahme
(data/track_d/<scale>/prompts.semantic3shot.jsonl, spec/schema_stoermeldung_semantic.json) → fewer output
tokens per row. Every row reaches the LLM: GLiNER (level 1, d10) resolves too few semantic fields to skip
it (ADR 0019). If the LLM answer for a row is invalid, the row falls back to the level-0/1 values.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from zj_llm import extract as llm_extract
from zj_llm.server import image_digest, running
from zj_ner import fields as fx

REPO = Path(__file__).resolve().parents[5]
SEMANTIC = json.loads((REPO / "spec" / "schema_stoermeldung_semantic.json").read_text("utf-8"))
SEM_FIELDS = tuple(SEMANTIC["required"])
LLM_MODEL, CONCURRENCY = "gemma4_e4b_q4", 16


def parse_semantic(text: str) -> dict[str, Any] | None:
    from zj_reference import track_d as ref  # noqa: PLC0415

    try:
        obj = json.loads(text.strip())
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(obj, dict) or set(obj) != set(SEM_FIELDS):  # pyright: ignore[reportUnknownArgumentType]
        return None
    o: dict[str, Any] = obj  # pyright: ignore[reportUnknownVariableType]
    if o["schadensart"] not in ref.catalog()["schadensarten"]:
        return None
    if any(o[k] is not None and not isinstance(o[k], str) for k in ("komponente", "ursache", "massnahme")):
        return None
    return o


def main(argv: list[str] | None = None) -> int:
    from zj_reference import track_a as ta  # noqa: PLC0415
    from zjmark import result, stage  # noqa: PLC0415

    p = argparse.ArgumentParser(prog="python -m zj_ner.d11_cascade")
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--threads", type=int, default=0)
    p.add_argument(
        "--rows", type=int, default=0, help="debug: only the first N rows (never in measured runs)"
    )
    a = p.parse_args(argv)
    m = llm_extract.MODELS[LLM_MODEL]
    eng = llm_extract.ENGINES["llamacpp"](m, CONCURRENCY, schema=SEMANTIC)
    raw_rows = pq.read_table(a.input).to_pylist()  # pyright: ignore[reportUnknownMemberType]
    prompts = llm_extract.read_prompts(a.input, 3, "semantic3shot")
    if a.rows:
        raw_rows, prompts = raw_rows[: a.rows], prompts[: a.rows]
    if [r["meldung_id"] for r in raw_rows] != [q["meldung_id"] for q in prompts]:
        raise ValueError("rows.parquet and prompts.semantic3shot.jsonl disagree on row order")
    with ExitStack() as stack:
        with stage("load"):
            evidence = stack.enter_context(running(eng.server))
            lex = ta.load_lexicon()
            eng.request(prompts[0]["messages"])
            evidence = {**evidence, "server_vram_mb_loaded": llm_extract.vram_mb(eng.server)}
        with stage("extract"):
            t0 = time.perf_counter()
            with stage("rules"):
                base = [fx.level0(r, lex) for r in raw_rows]
            with stage("llm"):
                llm = llm_extract.run_all(prompts, eng.request, CONCURRENCY)
            dt = time.perf_counter() - t0
            out: list[dict[str, Any]] = []
            fallback = 0
            for raw, b, lo in zip(raw_rows, base, llm, strict=True):
                vals = b.as_json()
                sem = parse_semantic(str(lo.get("text") or ""))
                if sem is None:
                    fallback += 1
                    vals["schadensart"] = vals.get("schadensart") or "Sonstiges"
                else:
                    vals.update(sem)
                out.append(
                    {
                        "meldung_id": raw["meldung_id"],
                        "text": json.dumps(vals, ensure_ascii=False),
                        "completion_tokens": lo.get("completion_tokens"),
                        "prompt_tokens": lo.get("prompt_tokens"),
                        "finish_reason": lo.get("finish_reason"),
                        "latency_ms": lo.get("latency_ms"),
                    }
                )
        with stage("write"):
            a.output.write_text("".join(json.dumps(o, ensure_ascii=False) + "\n" for o in out), "utf-8")
        with stage("ttft"):
            ttft = [eng.ttft(q["messages"]) * 1e3 for q in prompts[1 : 1 + llm_extract.TTFT_ROWS]]
    toks = sum(int(o["completion_tokens"] or 0) for o in out)
    lat = sorted(float(o["latency_ms"]) for o in out)
    result(
        rows=len(out),
        notes="cascade: Track A rules + llama.cpp Gemma 4 E4B Q4 3-shot, LLM for the 4 semantic fields",
        extra={
            "engine": "cuda (server)",
            "image": image_digest(eng.server.image),
            "server_args": eng.server.args,
            "model": LLM_MODEL,
            "concurrency": CONCURRENCY,
            "shots": 3,
            "llm_fields": list(SEM_FIELDS),
            "llm_row_share": 1.0,
            "llm_fallback_rows": fallback,
            "throughput": {
                "rows_per_s": round(len(out) / dt, 3),
                "output_tok_per_s": round(toks / dt, 1),
                "completion_tokens": toks,
            },
            "latency_ms": {
                "p50": lat[len(lat) // 2],
                "p95": lat[int(len(lat) * 0.95)],
                "p99": lat[int(len(lat) * 0.99)],
            },
            "ttft_ms": {"median": round(statistics.median(ttft), 1), "n": len(ttft)} if ttft else None,
            **evidence,
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
