# Track C: `c.dense` (Spezifikation / specification)

**Task:** load documents + bge-m3 vectors + metadata into a fresh store, build its vector index, then answer
1 000 filtered top-10 nearest-neighbour queries (PLAN §9: `c.load` + `c.index` + `c.query` in **one run**,
separated by stages). Similarity = inner product = cosine (all vectors are L2-normalised).

Scales: `M` (100 000 synthetic Störmeldungen, qrels) and `real` (15 305 real NRW documents, known-item
queries, ADR 0012). `L` follows for the top contestants.

## Input (`task trackC:inputs SCALE=…`, directory `data/track_c/{scale}/`)
The harness passes `{input}` = `data/track_c/{scale}/docs.parquet`; the other files sit next to it.

| File | Content |
|---|---|
| `docs.parquet` | `id` string, `ort` string\|null (synthetic: `werk`; real: `ort`, 1 null), `bezirk` string\|null (synthetic: `regierungsbezirk`; real: null), `datum` date32\|null, `doc_text` string. Row *i* = document *i*. |
| `vectors.f16.npy` | float16 `(N, 1024)`, row *i* = document *i*: the Track B **reference** vectors (b01 code path, fp32) rounded to fp16 (PLAN §9: same vectors for all, half precision). |
| `queries.jsonl` | 1 000 lines `{"qid", "filter_class", "filter"}`, 250 per class, in this order. `filter` keys (all optional, AND-combined): `ort` (equality), `bezirk` (equality), `date_from`, `date_to` (ISO dates, **inclusive**). A null `datum` never satisfies a date filter. |
| `queries.f32.npy` | float32 `(1000, 1024)`: query embeddings (bge-m3, b01 code path, L2-normalised), row *j* = query *j*. |

Filter classes: synthetic `none` / `werk` / `datum` / `kombiniert` (bezirk + quarter); real `none` / `ort` /
`datum` / `kombiniert` (ort + ±15 days). Source: `spec/qrels_c.M.jsonl`, `spec/queries_c.real.jsonl`.

## Output (`{output}`, `.npy`)
`int32 (1000, 10)`: row *j* = the row indices (into `docs.parquet`) of query *j*'s 10 best documents
**that satisfy its filter**, best first; `-1` pads when fewer than 10 documents match.

**Concurrency 16** (PLAN §6: QPS at concurrency 1 and 16): after stage `query`, stage `query_c16` sends the
same 1 000 queries from 16 client threads (each with its own handle: connection, cursor, gRPC channel) and
writes the result to `<out>.c16.npy` (same format). It must pass the same checks. In-process GPU engines
share one device without batching (a dynamic batcher is a different contestant).

`ZJRESULT.extra` must contain `latency_ms` = `{"p50", "p95", "p99"}` of the per-query client-side latency
(stage `query`, concurrency 1), `throughput` = `{"qps_c1", "qps_c16", "concurrency", "c16_identical_rows"}`, the index parameters (`index`, e.g. `{"type": "hnsw", "m": 16,
"ef_construction": 200, "ef_search": 64}`) and, where the store has one, `disk_bytes`.

## Stages (`zjmark`)
`load` (server start / connect / open, PLAN §5.3), `ingest` (bulk load rows + vectors + metadata: best native
path), `index` (vector index build incl. metadata/payload indexes; engines that index during ingest report
it there and say so in `notes`), `query` (the 1 000 queries one after another, concurrency 1), `query_c16` (the same from 16 client
threads), `write`.
Every run starts from an **empty** store (cold, reproducible); the store is deleted afterwards.

## Reference (`task trackC:reference SCALE=…` → `data/reference/track_c/{scale}/`)
- `exact_top10.npy`: `zj_reference.track_c` (exact filtered brute force, numpy **fp32**, ties → lower row index)
  over the **fp32** reference document vectors = ground truth for recall. (c00 is the GPU contestant doing the
  same thing in torch; it is verified against this file like everyone else.)
- `labels.parquet` (synthetic only): gold `komponente`, `schadensart`, equipment group per row, for nDCG.
- `quality.json`: nDCG@10 (synthetic, graded qrels rule `zj_eval.relevance`) or hit@10 / MRR@10 (real,
  known item) of the exact top-10, per filter class: the dense retrieval quality ceiling.

## Verification (`zj_eval.verify_track_c`, fail-closed)
1. Shape `(1000, 10)`, int32, indices in `[-1, N)`, no duplicates per row, every returned row satisfies the
   query's filter, `-1` only as trailing padding.
2. **recall@10 vs. `exact_top10` ≥ 0.95 in every filter class** (PLAN §9 fair play: equal recall).
   recall@10 of a query = |result ∩ exact| / |exact| (|exact| = min(10, matching documents)).
3. Reported, not gated: nDCG@10 / hit@10 / MRR@10 per class (same rule as `quality.json`).

## Track B recall gate (spec/track_b.md §3)
For `b.embed` outputs at a scale with Track C queries (`M`, `real`; not `S`): exact filtered top-10 with
the candidate's document vectors and the reference query vectors; the scale's quality metric (nDCG@10 /
hit@10, all 1 000 queries) must be within **0.01** of the same metric with the reference vectors.

## `c.lexical` and `c.hybrid` (PLAN §9: BM25 + RRF, compound-word subset; ADR 0017)

Same inputs plus `queries.text.jsonl` (`{"qid", "text"}`, same order; `task trackC:lexref`), same output
format, same filters. Reference (`zj_reference.track_c_lex`, written out to be obviously correct):
analyzer = Tantivy's (split on non-alphanumerics, drop tokens ≥ 40 bytes, lower case, Snowball German
stem; no stop words, no decompounding); BM25 k1 1.2, b 0.75, query = set of its terms (OR), corpus-wide
statistics; RRF k = 60, ranks from 1, over the dense top-100 (exact fp32) and the BM25 top-100, both inside
the filter. Ties → lower row. Files in `data/reference/track_c/{scale}/`: `bm25_index.npz`, `bm25_top10.npy`,
`bm25_top100.npy`, `dense_top100.npy`, `hybrid_top10.npy`, `quality_lex.json`.

**`c.lexical`** (filtered BM25 top-10; c11_tantivy): verified by **tie-aware BM25 recall@10 ≥ 0.95 per
filter class** — a returned row counts when its reference BM25 score reaches the reference's 10th score
within 2 % (equal scores are frequent in short synthetic reports; Tantivy stores lengths in one byte).

**`c.hybrid`** (each engine: its own vector index + its own full-text ranking, fused by RRF k = 60 over
two top-100 legs, the engine's documented way): besides `<out>.npy` (fused top-10), stage `legs` (after
`write`, outside the comparison) writes the two candidate lists `<out>.dense.npy` and `<out>.lex.npy`
(int32 (1000, 100), -1 padded). Verification: structure + filter for all three; dense leg recall@10 ≥ 0.95
per class vs exact search; lexical leg quality ≥ 0.5 × reference BM25 quality (a sanity floor: the
engine's analyzer and ranking function are what is being compared, e.g. Postgres `ts_rank` is not BM25,
FTS5 has no German stemmer); fused top-10 = RRF of the legs, tie-aware, mean agreement ≥ 0.95 (rank origin
0 vs 1 differs between engines).

Reported: nDCG@10 (M) / hit@10 + MRR@10 (real) of fused output and of both legs, per filter class and for
the **compound-word subset** (`kompositum`: the query is one word such as „Pumpenleckage“, 35 of 1 000 at M;
`getrennt`: the rest).
