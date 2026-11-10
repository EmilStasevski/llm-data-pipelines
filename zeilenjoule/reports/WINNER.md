# ZeilenJoule: Gewinner je Track

> Generiert von `task report` aus `results/zj.duckdb` (PLAN §11.1); keine Zahl von Hand.

## Track A: Dataframe- und Zeilenverarbeitung (`a.full`) – Skala L

- **Schnellster:** `a17_rs_handrolled` (0,91 s)
- **Effizientester:** `a17_rs_handrolled` (56,0 J)

**Pareto-Front (Zeit ↔ Energie):**

| Wettbewerber | Wall s | Energie J | ZJ-Score | Qualität | Gate |
|---|---|---|---|---|---|
| `a17_rs_handrolled` | 0,91 | 56,0 | 1,00 | – | – |

## Track B: Embeddings (`b.embed`, bge-m3) – Skala M

- **Schnellster:** `b06_tei` (108,76 s)
- **Effizientester:** `b06_tei` (19 632,2 J)

**Pareto-Front (Zeit ↔ Energie):**

| Wettbewerber | Wall s | Energie J | ZJ-Score | Qualität | Gate |
|---|---|---|---|---|---|
| `b06_tei` | 108,76 | 19 632,2 | 1,00 | – | – |

## Track C: Speicherung, Index, Suche (`c.dense`) – Skala M

- **Schnellster:** `c00_exact_gpu` (4,48 s)
- **Effizientester:** `c00_exact_gpu` (228,4 J)

**Pareto-Front (Zeit ↔ Energie):**

| Wettbewerber | Wall s | Energie J | ZJ-Score | Qualität | Gate |
|---|---|---|---|---|---|
| `c00_exact_gpu` | 4,48 | 228,4 | 1,00 | – | – |

## Track C: Hybrid-Suche (`c.hybrid`, Dense + lexikalisch, RRF k = 60) – Skala M

- **Schnellster:** `c08_qdrant_hybrid` (66,62 s)
- **Effizientester:** `c08_qdrant_hybrid` (1 071,9 J)

**Pareto-Front (Zeit ↔ Energie):**

| Wettbewerber | Wall s | Energie J | ZJ-Score | Qualität | Gate |
|---|---|---|---|---|---|
| `c08_qdrant_hybrid` | 66,62 | 1 071,9 | 1,00 | – | – |

## Track C: lexikalische Suche (`c.lexical`, BM25) – Skala M

- **Schnellster:** `c11_tantivy` (1,49 s)
- **Effizientester:** `c11_tantivy` (26,8 J)

**Pareto-Front (Zeit ↔ Energie):**

| Wettbewerber | Wall s | Energie J | ZJ-Score | Qualität | Gate |
|---|---|---|---|---|---|
| `c11_tantivy` | 1,49 | 26,8 | 1,00 | – | – |

## Track D: LLM-Extraktion je Zeile (`d.extract`) – Skala D2k

- **Schnellster:** `d03_vllm_qwen35_9b_awq_3shot` (376,10 s)
- **Effizientester:** `d11_cascade` (34 925,0 J)

**Pareto-Front (Zeit ↔ Energie):**

| Wettbewerber | Wall s | Energie J | ZJ-Score | Qualität | Gate |
|---|---|---|---|---|---|
| `d03_vllm_qwen35_9b_awq_3shot` | 376,10 | 49 290,2 | 1,19 | 0,964 | ✓ |
| `d11_cascade` | 388,93 | 34 925,0 | 1,02 | 0,966 | ✓ |

## Track E: End-to-End-RAG (`e.rag`) – Skala L

_Noch keine gewerteten Läufe._

## Produktions-Stack (Regel 5)

_Noch keine gewertete End-to-End-Pipeline._
