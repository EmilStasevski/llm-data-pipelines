# Track B: `b.embed` (Spezifikation / specification)

**Task:** embed every `doc_text` of the input with **BAAI/bge-m3** (dense head, revision
`5617a9f61b028005a4858fdac845db406aefb181`) and write the vectors.

## Input
`data/track_b/{scale}/docs.parquet`, built by `task trackB:inputs`, with columns `id` (string) and `doc_text` (string):
- `S|M|L|XL`: `meldung_id` + `doc_text` of the Track A reference output `data/reference/track_a/{scale}/a_full.parquet`. That output is identical for every passing Track A contestant (rule 4).
- `real`: `doc_id` + `doc_text` of `data/real/corpus/real_nrw.parquet` (ADR 0012).

## Output (`{output}`, `.npy`)
- `float16`, shape `(N, 1024)`, **row i = input row i** (input order; a contestant that reorders internally, e.g. for length-sorted batching, restores the order). This replaces PLAN's separate id-order file: the ids are the input's `id` column.
- Vectors **L2-normalised** (‖v‖ = 1 ± 0.01 after fp16 rounding).
- Truncation: **max 512 tokens** (bge-m3 tokenizer, incl. special tokens). Pooling: CLS token (the model's `1_Pooling` config).
- No query/passage prefix (bge-m3 does not use one).

## Reference
`b01_st_fp32` code path (sentence-transformers fp32, CUDA), stored as `float32` at
`data/reference/track_b/{scale}/bge-m3.f32.npy` (`task trackB:reference SCALE=…`).

## Verification (`zj_eval.verify_track_b`, fail-closed)
1. Shape `(N_reference, 1024)`, dtype float16, all finite, every norm in [0.99, 1.01].
2. **Mean cosine similarity to the reference ≥ 0.995** (PLAN §8). Minimum and 1st percentile are reported.
3. **Retrieval gate** (spec/track_c.md, since 2026-11-08) at scales with Track C queries (`M`, `real`; not `S`): exact filtered top-10 with the candidate's vectors and the reference query vectors; nDCG@10 (M) / hit@10 (real) within **0.01** of the reference vectors' value. Runs measured before 2026-11-08 were verified with 1–2 only; their verify detail says "pending".

## Stages (`zjmark`)
`load` (model to device; measured separately, PLAN §5.3), `read`, `embed` (tokenisation + forward + pooling), `write`.
