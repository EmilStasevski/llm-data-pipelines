"""`python -m zj_embed.track_b.prepare inputs|reference --scale S|M|L|XL|real` (spec/track_b.md)."""

from __future__ import annotations

import argparse
import json
import sys
import time

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from zj_embed.track_b.common import MAX_TOKENS, MODEL_DIR, MODEL_ID, MODEL_REVISION, REPO, Args, read_texts

SOURCES = {"real": ("data/real/corpus/real_nrw.parquet", "doc_id")}


def input_path(scale: str) -> str:
    return f"data/track_b/{scale}/docs.parquet"


def build_input(scale: str) -> int:
    src, id_col = SOURCES.get(scale, (f"data/reference/track_a/{scale}/a_full.parquet", "meldung_id"))
    t = pq.read_table(REPO / src, columns=[id_col, "doc_text"]).rename_columns(["id", "doc_text"])
    t = t.cast(pa.schema([("id", pa.string()), ("doc_text", pa.string())]))
    out = REPO / input_path(scale)
    out.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(t, out, compression="zstd")  # pyright: ignore[reportUnknownMemberType]
    return t.num_rows


def build_reference(scale: str) -> dict[str, object]:
    from zj_embed.track_b import st  # noqa: PLC0415

    texts = read_texts(REPO / input_path(scale))
    args = Args(REPO / input_path(scale), REPO / "unused", 0, MODEL_DIR, None)
    t0 = time.perf_counter()
    vecs = st.encode(st.load(args, half=False), texts, batch_size=32).astype(np.float32)
    out = REPO / f"data/reference/track_b/{scale}"
    out.mkdir(parents=True, exist_ok=True)
    np.save(out / "bge-m3.f32.npy", vecs, allow_pickle=False)
    info: dict[str, object] = {
        "rows": len(texts), "model": MODEL_ID, "revision": MODEL_REVISION, "max_tokens": MAX_TOKENS,
        "path": "sentence-transformers fp32 CUDA, batch 32 (= b01)",
        "seconds_unmeasured": round(time.perf_counter() - t0, 1),
        "input": input_path(scale),
    }  # fmt: skip
    (out / "manifest.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    return info


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m zj_embed.track_b.prepare")
    p.add_argument("what", choices=["inputs", "reference"])
    p.add_argument("--scale", required=True)
    a = p.parse_args(argv)
    if a.what == "inputs":
        print(f"{input_path(a.scale)}: {build_input(a.scale)} rows")
    else:
        print(json.dumps(build_reference(a.scale)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
