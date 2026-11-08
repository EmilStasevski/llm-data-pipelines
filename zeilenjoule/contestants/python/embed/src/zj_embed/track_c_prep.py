"""`python -m zj_embed.track_c_prep --scale M|real` → data/track_c/{scale}/ (spec/track_c.md inputs).

docs.parquet (id, ort, bezirk, datum, doc_text), vectors.f16.npy (the Track B fp32 reference vectors in
fp16), queries.jsonl (filters with normalised keys) and queries.f32.npy (query embeddings, b01 code path:
sentence-transformers fp32, CUDA, batch 32). Offline preparation, never measured.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from zj_embed.track_b.common import DIM, MODEL_DIR, REPO, Args

SOURCES = {  # scale → (table, SELECT list renaming to the spec columns, queries file)
    "real": ("data/real/corpus/real_nrw.parquet", ["doc_id", "ort", None, "datum", "doc_text"],
             "spec/queries_c.real.jsonl"),
}  # fmt: skip
SYNTH = ["meldung_id", "werk", "regierungsbezirk", "datum", "doc_text"]
KEYS = {"werk": "ort", "ort": "ort", "regierungsbezirk": "bezirk", "date_from": "date_from",
        "date_to": "date_to", "datum_von": "date_from", "datum_bis": "date_to"}  # fmt: skip
SCHEMA = pa.schema([("id", pa.string()), ("ort", pa.string()), ("bezirk", pa.string()),
                    ("datum", pa.date32()), ("doc_text", pa.string())])  # fmt: skip


def build(scale: str) -> dict[str, object]:
    src, cols, qfile = SOURCES.get(
        scale, (f"data/reference/track_a/{scale}/a_full.parquet", SYNTH, f"spec/qrels_c.{scale}.jsonl")
    )
    t = pq.read_table(REPO / src, columns=[c for c in cols if c])
    n = t.num_rows
    arrays = [t.column(c) if c else pa.nulls(n, pa.string()) for c in cols]
    docs = pa.Table.from_arrays(arrays, schema=SCHEMA)  # pyright: ignore[reportUnknownMemberType]
    out = REPO / "data" / "track_c" / scale
    out.mkdir(parents=True, exist_ok=True)
    pq.write_table(docs, out / "docs.parquet", compression="zstd")  # pyright: ignore[reportUnknownMemberType]

    ref = np.load(REPO / "data" / "reference" / "track_b" / scale / "bge-m3.f32.npy", mmap_mode="r")
    if ref.shape != (n, DIM):
        raise ValueError(f"reference vectors {ref.shape} do not match {n} docs")
    np.save(out / "vectors.f16.npy", np.asarray(ref, dtype=np.float16), allow_pickle=False)

    qs = [json.loads(x) for x in (REPO / qfile).read_text(encoding="utf-8").splitlines() if x.strip()]
    lines = [json.dumps({"qid": q["qid"], "filter_class": q["filter_class"],
                         "filter": {KEYS[k]: v for k, v in q["filter"].items()}}, ensure_ascii=False)
             for q in qs]  # fmt: skip
    (out / "queries.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    from zj_embed.track_b import st  # noqa: PLC0415

    t0 = time.perf_counter()
    model = st.load(Args(out, out, 0, MODEL_DIR, None), half=False)
    qv = st.encode(model, [str(q["text"]) for q in qs], batch_size=32).astype(np.float32)
    np.save(out / "queries.f32.npy", qv, allow_pickle=False)
    info: dict[str, object] = {
        "scale": scale, "docs": n, "queries": len(qs), "source": src, "queries_from": qfile,
        "vectors": f"data/reference/track_b/{scale}/bge-m3.f32.npy → float16",
        "query_vectors": "sentence-transformers fp32 CUDA, batch 32 (= b01)",
        "seconds_unmeasured": round(time.perf_counter() - t0, 1),
    }  # fmt: skip
    (out / "manifest.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    return info


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m zj_embed.track_c_prep")
    p.add_argument("--scale", required=True)
    print(json.dumps(build(p.parse_args(argv).scale)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
