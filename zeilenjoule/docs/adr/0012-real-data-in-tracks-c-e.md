# ADR 0012: Real data in Tracks C and E (timing and robustness, label-free)

- Status: accepted (2026-11-07). User request: "yeah do real c/e time measure".
- Context: PLAN §6.2 uses real open data for robustness and the demo. Tracks C/E need embeddings (Track B, Phase 5), so they cannot run yet. The real data has no gold labels, and after de-duplication it holds 15,305 documents (thru 1,005 / TED 13,514 = all NRW contract notices 2026-01..10 / BA 786), while the synthetic data has 1M rows at L.
- Decision:
  - `task data:real-corpus` builds `data/real/corpus/real_nrw.parquet` (`doc_id`, `quelle`, `ort`, `datum`, `titel`, `doc_text`) and `spec/queries_c.real.jsonl`: 1,000 **known-item** queries (unique titles of TED/BA documents), 250 per filter class. The classes are `none`, `ort` (median selectivity 1.3 %, the analogue of werk at about 4 %), `datum` (±15 days, 9.6 %) and `kombiniert` (0.08 %).
  - Tracks C and E get an extra scale **`real`** next to the synthetic M/L:
    - C: `c.load`, `c.index`, `c.query` (p50/p95/p99, QPS at concurrency 1/16, J/query).
    - Recall@10 is measured against c00 exact (filtered) brute force, which needs no labels, plus the known-item hit rate.
    - `c.hybrid` nDCG stays synthetic (it needs qrels).
  - E: Time-to-RAG and Energy-to-RAG on the real corpus, and per-question latency/J with the real queries. Answer correctness, citation F1 and router accuracy stay synthetic (they need gold).
  - The report shows real-data rows separately and never mixes them into the winner score (winner = synthetic L, PLAN §11.1).
- Consequences / caveats:
  - Bulk steps on 15k documents finish in well under 10 s, so their energy is `energy_confidence = low` by design. Per-query latency is unaffected (1,000 queries).
  - Real texts are longer and more varied (TED descriptions about 400 characters) than the synthetic messages. That is the point: a robustness check of tokenisation, index build and the German analyzers.
  - The full TED set was fetched with the user's OK (2026-11-07): 36 requests, 20 of them from cache. The cap is now 15,000, the TED page-number pagination limit.
