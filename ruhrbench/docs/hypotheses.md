# Hypotheses

Written on 2026-11-03, before any M4 or M5 measurement. Do not edit a prediction after its run has started. Fill in the outcome and link the evidence when the run is done.

| # | Prediction | Outcome | Evidence |
|---|---|---|---|
| 1 | A BM25 + row-matcher pipeline reaches within a few F1 points of the LLM pipelines on statement rows at a tiny fraction of the energy. | open | |
| 2 | For GPU-bound stages (embedding, LLM decode) the language of the client does not change throughput measurably; for parsing, chunking and BM25 indexing a Rust implementation is clearly faster than pure Python. | open | |
| 3 | Lowering the GPU power limit costs little decode speed (memory-bandwidth-bound) and improves tokens per watt, but costs more during compute-bound prefill/embedding. | open | |
| 4 | Output tokens dominate energy; cutting output length (short keys, schema-constrained output, no thinking) saves more than any other single change. | open | |
| 5 | Prior-year vs current-year column confusion is the most common LLM error on financial statements. | open | |

Outcome is one of: confirmed, refuted, inconclusive.

Hypothesis 3 has a narrow test range on this card: the allowed power limit is 150 W to 216 W with a 180 W default (see `versions.md`).
