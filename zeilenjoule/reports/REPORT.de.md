# ZeilenJoule: Bericht

> Generiert von `task report` aus `results/zj.duckdb`; keine Zahl ist von Hand eingetragen.
> Strompreise 0.373 €/kWh (Haushalt) und 0.167 €/kWh (Industrie), 344 g CO₂/kWh (`config/bench.yaml`, Quellen in PLAN §3.4).

Methodik: `docs/METHODIK.md`. Energie = GPU + CPU-Paket im Messfenster (systemweit); GPU aus integrierten NVML-Leistungswerten (ADR 0006). Netto = Brutto minus Leerlaufleistung mal Zeit; Wettbewerber ohne GPU-Nutzung bekommen keinen GPU-Nettoanteil (ADR 0014). ZJ-Score = √((T/T_best)·(E/E_best)), 1,00 = bestes in beidem.
## Track A: Dataframe- und Zeilenverarbeitung (`a.full`)

### Skala S (10 000 Zeilen)

| Wettbewerber | n | Wall s (Median) | IQR s | Zeilen/s | E brutto J | E netto J | mJ/Zeile | €/1 Mio. Zeilen (Haushalt) | €/1 Mio. Zeilen (Industrie) | g CO₂/1 Mio. Zeilen | ZJ-Score | niedr. Konfidenz |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `a17_rs_handrolled` | 5 | 0,080 | 0,078–0,081 | 124 291 | 0,8 | 0,3 | 0,082 | 0,00000850 | 0,00000381 | 0,01 | 1,00 | 100 % |
| `a16_rs_arrow_rayon` | 5 | 0,212 | 0,212–0,214 | 47 232 | 2,1 | 0,6 | 0,212 | 0,0000220 | 0,00000985 | 0,02 | 2,61 | 100 % |
| `a15_rs_polars` | 5 | 0,312 | 0,290–0,312 | 32 042 | 3,1 | 1,1 | 0,313 | 0,0000325 | 0,0000145 | 0,03 | 3,85 | 100 % |
| `a10_polars_lazy` | 5 | 0,563 (CV>5 %) | 0,527–0,585 | 17 767 | 13,0 | 9,3 | 1,298 | 0,000135 | 0,0000602 | 0,12 | 10,52 | 100 % |
| `a11_polars_streaming` | 5 | 0,592 (CV>5 %) | 0,588–0,616 | 16 891 | 12,9 | 8,8 | 1,292 | 0,000134 | 0,0000599 | 0,12 | 10,76 | 100 % |
| `a09_polars_eager` | 5 | 0,654 (CV>5 %) | 0,609–0,685 | 15 284 | 13,3 | 8,7 | 1,333 | 0,000138 | 0,0000618 | 0,13 | 11,49 | 100 % |
| `a01_py_csv_loop` | 5 | 0,690 | 0,686–0,703 | 14 494 | 18,5 | 13,7 | 1,849 | 0,000192 | 0,0000858 | 0,18 | 13,90 | 100 % |
| `a03_pandas_itertuples` | 5 | 0,714 | 0,708–0,717 | 14 014 | 16,8 | 11,8 | 1,681 | 0,000174 | 0,0000780 | 0,16 | 13,48 | 100 % |
| `a05_pandas_vectorized` | 5 | 0,723 (CV>5 %) | 0,722–0,726 | 13 828 | 15,9 | 10,7 | 1,585 | 0,000164 | 0,0000735 | 0,15 | 13,18 | 100 % |
| `a06_pandas_arrow` | 5 | 0,736 (CV>5 %) | 0,705–0,828 | 13 579 | 15,8 | 10,2 | 1,575 | 0,000163 | 0,0000731 | 0,15 | 13,26 | 100 % |
| `a13_duckdb_py` | 5 | 1,065 (CV>5 %) | 1,052–1,104 | 9 387 | 23,7 | 16,8 | 2,373 | 0,000246 | 0,00011 | 0,23 | 19,57 | 100 % |
| `a02_pandas_iterrows` ⚠anti | 5 | 1,285 | 1,269–1,293 | 7 783 | 32,4 | 23,3 | 3,244 | 0,000336 | 0,00015 | 0,31 | 25,13 | 100 % |
| `a04_pandas_apply` | 5 | 1,295 (CV>5 %) | 1,174–1,297 | 7 721 | 30,5 | 21,3 | 3,046 | 0,000316 | 0,000141 | 0,29 | 24,45 | 100 % |
| `a07_cudf_pandas` ⚠CPU-Fallback | 5 | 2,112 | 2,064–2,237 | 4 734 | 49,6 | 33,0 | 4,956 | 0,000513 | 0,00023 | 0,47 | 39,83 | 100 % |
| `a23_polars_gpu_hybrid` | 5 | 2,200 (CV>5 %) | 2,158–2,201 | 4 545 | 43,3 | 25,9 | 4,327 | 0,000448 | 0,000201 | 0,41 | 37,98 | 100 % |
| `a08_polars_map_elements` ⚠anti | 5 | 2,223 (CV>5 %) | 2,217–2,229 | 4 499 | 24,5 | 9,8 | 2,450 | 0,000254 | 0,000114 | 0,23 | 28,73 | 100 % |

### Skala M (100 000 Zeilen)

| Wettbewerber | n | Wall s (Median) | IQR s | Zeilen/s | E brutto J | E netto J | mJ/Zeile | €/1 Mio. Zeilen (Haushalt) | €/1 Mio. Zeilen (Industrie) | g CO₂/1 Mio. Zeilen | ZJ-Score | niedr. Konfidenz |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `a17_rs_handrolled` | 5 | 0,213 | 0,211–0,216 | 469 652 | 5,2 | 3,8 | 0,052 | 0,00000538 | 0,00000241 | 0,00 | 1,00 | 100 % |
| `a16_rs_arrow_rayon` | 5 | 0,613 (CV>5 %) | 0,517–0,718 | 163 074 | 11,6 | 7,7 | 0,116 | 0,0000121 | 0,00000540 | 0,01 | 2,54 | 100 % |
| `a15_rs_polars` | 5 | 0,710 | 0,704–0,737 | 140 761 | 26,3 | 21,3 | 0,263 | 0,0000272 | 0,0000122 | 0,03 | 4,11 | 100 % |
| `a11_polars_streaming` | 5 | 0,928 (CV>5 %) | 0,874–0,940 | 107 754 | 37,2 | 31,2 | 0,372 | 0,0000385 | 0,0000172 | 0,04 | 5,59 | 100 % |
| `a10_polars_lazy` | 5 | 1,019 (CV>5 %) | 0,985–1,041 | 98 149 | 37,6 | 30,2 | 0,376 | 0,0000390 | 0,0000175 | 0,04 | 5,89 | 100 % |
| `a09_polars_eager` | 5 | 2,262 | 2,186–2,275 | 44 203 | 49,7 | 34,2 | 0,497 | 0,0000515 | 0,0000231 | 0,05 | 10,08 | 100 % |
| `a23_polars_gpu_hybrid` | 5 | 2,852 | 2,746–2,883 | 35 066 | 71,3 | 49,0 | 0,713 | 0,0000739 | 0,0000331 | 0,07 | 13,56 | 100 % |
| `a13_duckdb_py` | 5 | 3,265 (CV>5 %) | 3,252–3,275 | 30 631 | 76,6 | 53,0 | 0,766 | 0,0000793 | 0,0000355 | 0,07 | 15,03 | 100 % |
| `a01_py_csv_loop` | 5 | 3,447 | 3,436–3,458 | 29 012 | 94,8 | 71,5 | 0,948 | 0,0000982 | 0,0000440 | 0,09 | 17,18 | 100 % |
| `a06_pandas_arrow` | 5 | 3,756 | 3,658–3,842 | 26 625 | 92,7 | 67,0 | 0,927 | 0,0000960 | 0,0000430 | 0,09 | 17,74 | 100 % |
| `a05_pandas_vectorized` | 5 | 3,814 | 3,808–3,928 | 26 220 | 97,2 | 70,8 | 0,972 | 0,000101 | 0,0000451 | 0,09 | 18,30 | 100 % |
| `a03_pandas_itertuples` | 5 | 3,905 | 3,904–4,029 | 25 609 | 105,2 | 78,6 | 1,052 | 0,000109 | 0,0000488 | 0,10 | 19,27 | 100 % |
| `a07_cudf_pandas` ⚠CPU-Fallback | 5 | 4,537 (CV>5 %) | 4,477–4,852 | 22 041 | 151,6 | 114,7 | 1,516 | 0,000157 | 0,0000703 | 0,14 | 24,93 | 80 % |
| `a04_pandas_apply` | 5 | 8,659 | 8,626–8,740 | 11 549 | 236,7 | 178,0 | 2,367 | 0,000245 | 0,00011 | 0,23 | 43,05 | 0 % |
| `a02_pandas_iterrows` ⚠anti | 5 | 9,531 | 9,432–9,625 | 10 492 | 258,4 | 194,4 | 2,584 | 0,000268 | 0,00012 | 0,25 | 47,18 | 0 % |
| `a08_polars_map_elements` ⚠anti | 5 | 20,449 (CV>5 %) | 18,709–20,508 | 4 890 | 159,7 | 20,9 | 1,597 | 0,000165 | 0,0000741 | 0,15 | 54,33 | 0 % |

### Skala L (1 000 000 Zeilen)

| Wettbewerber | n | Wall s (Median) | IQR s | Zeilen/s | E brutto J | E netto J | mJ/Zeile | €/1 Mio. Zeilen (Haushalt) | €/1 Mio. Zeilen (Industrie) | g CO₂/1 Mio. Zeilen | ZJ-Score | niedr. Konfidenz |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `a17_rs_handrolled` | 5 | 0,914 | 0,899–0,917 | 1 094 335 | 56,0 | 50,2 | 0,056 | 0,00000580 | 0,00000260 | 0,01 | 1,00 | 100 % |
| `a16_rs_arrow_rayon` | 5 | 2,691 | 2,672–2,707 | 371 603 | 96,9 | 77,9 | 0,097 | 0,0000100 | 0,00000449 | 0,01 | 2,26 | 100 % |
| `a10_polars_lazy` | 5 | 3,623 | 3,529–3,645 | 276 037 | 252,1 | 228,1 | 0,252 | 0,0000261 | 0,0000117 | 0,02 | 4,23 | 100 % |
| `a11_polars_streaming` | 5 | 3,630 | 3,598–3,653 | 275 507 | 254,3 | 228,5 | 0,254 | 0,0000263 | 0,0000118 | 0,02 | 4,25 | 100 % |
| `a15_rs_polars` | 5 | 4,474 | 4,441–4,697 | 223 532 | 268,3 | 237,4 | 0,268 | 0,0000278 | 0,0000124 | 0,03 | 4,85 | 100 % |
| `a13_duckdb_py` | 5 | 5,591 | 5,563–5,643 | 178 848 | 279,8 | 242,2 | 0,280 | 0,0000290 | 0,0000130 | 0,03 | 5,53 | 0 % |
| `a23_polars_gpu_hybrid` | 5 | 5,886 | 5,880–6,040 | 169 888 | 335,9 | 291,8 | 0,336 | 0,0000348 | 0,0000156 | 0,03 | 6,22 | 0 % |
| `a09_polars_eager` | 5 | 11,658 | 11,600–12,008 | 85 780 | 414,4 | 334,7 | 0,414 | 0,0000429 | 0,0000192 | 0,04 | 9,72 | 0 % |
| `a07_cudf_pandas` ⚠CPU-Fallback | 5 | 28,355 | 28,352–28,466 | 35 268 | 1 125,1 | 903,3 | 1,125 | 0,000117 | 0,0000522 | 0,11 | 24,98 | 0 % |
| `a01_py_csv_loop` | 5 | 30,908 | 30,851–30,953 | 32 355 | 837,3 | 630,9 | 0,837 | 0,0000867 | 0,0000388 | 0,08 | 22,50 | 0 % |
| `a06_pandas_arrow` | 5 | 34,248 | 34,156–34,276 | 29 199 | 865,6 | 632,9 | 0,866 | 0,0000897 | 0,0000402 | 0,08 | 24,08 | 0 % |
| `a05_pandas_vectorized` | 5 | 35,520 | 35,520–35,689 | 28 153 | 910,7 | 670,7 | 0,911 | 0,0000944 | 0,0000422 | 0,09 | 25,15 | 0 % |
| `a03_pandas_itertuples` | 5 | 35,869 | 35,724–36,146 | 27 879 | 984,2 | 744,3 | 0,984 | 0,000102 | 0,0000457 | 0,09 | 26,28 | 0 % |
| `a04_pandas_apply` | 5 | 82,515 | 82,216–83,418 | 12 119 | 2 307,2 | 1 742,0 | 2,307 | 0,000239 | 0,000107 | 0,22 | 61,02 | 0 % |

### Skala XL (10 000 000 Zeilen)

| Wettbewerber | n | Wall s (Median) | IQR s | Zeilen/s | E brutto J | E netto J | mJ/Zeile | €/1 Mio. Zeilen (Haushalt) | €/1 Mio. Zeilen (Industrie) | g CO₂/1 Mio. Zeilen | ZJ-Score | niedr. Konfidenz |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `a17_rs_handrolled` | 5 | 6,822 | 6,749–6,843 | 1 465 823 | 552,6 | 506,1 | 0,055 | 0,00000573 | 0,00000256 | 0,01 | 1,00 | 0 % |
| `a16_rs_arrow_rayon` | 5 | 24,214 | 23,978–24,259 | 412 989 | 923,5 | 758,0 | 0,092 | 0,00000957 | 0,00000428 | 0,01 | 2,44 | 0 % |
| `a11_polars_streaming` | 5 | 28,539 | 28,510–28,561 | 350 395 | 2 402,6 | 2 209,0 | 0,240 | 0,0000249 | 0,0000111 | 0,02 | 4,26 | 0 % |
| `a10_polars_lazy` | 5 | 29,015 | 28,752–29,321 | 344 655 | 2 407,6 | 2 206,7 | 0,241 | 0,0000249 | 0,0000112 | 0,02 | 4,30 | 0 % |
| `a13_duckdb_py` | 5 | 34,456 | 34,039–34,670 | 290 222 | 2 341,8 | 2 107,3 | 0,234 | 0,0000243 | 0,0000109 | 0,02 | 4,63 | 0 % |
| `a23_polars_gpu_hybrid` | 5 | 35,566 | 35,521–35,585 | 281 167 | 3 043,6 | 2 802,6 | 0,304 | 0,0000315 | 0,0000141 | 0,03 | 5,36 | 0 % |
| `a15_rs_polars` | 5 | 42,302 | 42,263–42,309 | 236 396 | 2 668,5 | 2 378,7 | 0,267 | 0,0000276 | 0,0000124 | 0,03 | 5,47 | 0 % |

### Nicht gewertete Läufe (Regel 4: nicht versteckt)

| Wettbewerber | Skala | Status | n | Detail |
|---|---|---|---|---|
| `a12_polars_gpu` | S | fail | 5 | exit code 1 (see logs/runs/17a7791b-412f-4b26-a3bb-3bddaf61bb2c/026e0e4e-68a7-4f35-b29d-281eaf0b372c.log) |

## Track B: Embeddings (`b.embed`, bge-m3)

Wall-Zeit inklusive Modell-Laden (Stufe `load`). Verifikation: mittlere Kosinus-Ähnlichkeit ≥ 0,995 zur fp32-Referenz; seit 2026-11-08 zusätzlich der Retrieval-Teil (spec/track_b.md §3, M und real). Davor gemessene Läufe wurden ohne ihn geprüft.

### Skala S (10 000 Dokumente)

| Wettbewerber | n | Wall s (Median) | IQR s | Dokumente/s | E brutto J | E netto J | mJ/Dok. | €/1 Mio. Dokumente (Haushalt) | €/1 Mio. Dokumente (Industrie) | g CO₂/1 Mio. Dokumente | ZJ-Score | niedr. Konfidenz |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `b10_rs_ort` | 5 | 12,870 | 12,833–12,909 | 777 | 2 205,0 | 2 102,4 | 220,497 | 0,0228 | 0,0102 | 21,07 | 1,00 | 0 % |
| `b04_ort_cuda` | 5 | 15,443 | 15,361–15,459 | 648 | 2 332,0 | 2 218,2 | 233,203 | 0,0242 | 0,0108 | 22,28 | 1,13 | 0 % |
| `b03_st_fp16_sorted` | 5 | 15,922 | 15,771–15,985 | 628 | 2 300,6 | 2 182,2 | 230,060 | 0,0238 | 0,0107 | 21,98 | 1,14 | 0 % |
| `b02_st_fp16` | 5 | 19,864 | 19,857–19,925 | 503 | 2 519,3 | 2 372,1 | 251,929 | 0,0261 | 0,0117 | 24,07 | 1,33 | 0 % |
| `b06_tei` | 5 | 21,998 | 21,909–22,021 | 455 | 2 462,3 | 2 287,4 | 246,233 | 0,0255 | 0,0114 | 23,53 | 1,38 | 0 % |
| `b08_llamacpp_embed` | 5 | 30,501 | 30,488–30,673 | 328 | 2 654,5 | 2 409,1 | 265,447 | 0,0275 | 0,0123 | 25,36 | 1,69 | 0 % |
| `b07_vllm_pooling` | 5 | 40,349 | 40,297–40,976 | 248 | 2 560,4 | 2 236,2 | 256,036 | 0,0265 | 0,0119 | 24,47 | 1,91 | 0 % |
| `b01_st_fp32` | 5 | 50,887 | 50,788–50,924 | 197 | 8 878,0 | 8 532,8 | 887,804 | 0,092 | 0,0412 | 84,83 | 3,99 | 0 % |
| `b00_ort_cpu_int8` | 5 | 397,976 | 397,186–398,252 | 25 | 37 685,2 | 34 950,0 | 3 768,521 | 0,39 | 0,175 | 360,10 | 22,99 | 0 % |

### Skala M (100 000 Dokumente)

| Wettbewerber | n | Wall s (Median) | IQR s | Dokumente/s | E brutto J | E netto J | mJ/Dok. | €/1 Mio. Dokumente (Haushalt) | €/1 Mio. Dokumente (Industrie) | g CO₂/1 Mio. Dokumente | ZJ-Score | niedr. Konfidenz |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `b06_tei` | 5 | 108,756 | 108,541–108,987 | 919 | 19 632,2 | 18 774,3 | 196,322 | 0,0203 | 0,00911 | 18,76 | 1,00 | 0 % |
| `b10_rs_ort` | 5 | 110,063 | 109,867–110,115 | 909 | 21 451,0 | 20 584,7 | 214,510 | 0,0222 | 0,00995 | 20,50 | 1,05 | 0 % |
| `b03_st_fp16_sorted` | 5 | 112,335 | 112,274–112,489 | 890 | 21 222,1 | 20 270,1 | 212,221 | 0,022 | 0,00984 | 20,28 | 1,06 | 0 % |
| `b04_ort_cuda` | 5 | 113,731 | 113,670–113,795 | 879 | 21 623,8 | 20 681,3 | 216,238 | 0,0224 | 0,01 | 20,66 | 1,07 | 0 % |
| `b02_st_fp16` | 5 | 146,472 | 146,111–151,222 | 683 | 23 943,7 | 22 707,2 | 239,437 | 0,0248 | 0,0111 | 22,88 | 1,28 | 0 % |
| `b07_vllm_pooling` | 5 | 164,533 | 163,872–170,227 | 608 | 21 510,1 | 20 222,9 | 215,101 | 0,0223 | 0,00998 | 20,55 | 1,29 | 0 % |
| `b08_llamacpp_embed` | 5 | 246,839 | 237,142–247,103 | 405 | 25 449,2 | 23 573,7 | 254,492 | 0,0264 | 0,0118 | 24,32 | 1,72 | 0 % |
| `b01_st_fp32` | 5 | 458,781 | 458,159–458,818 | 218 | 87 935,6 | 84 530,1 | 879,356 | 0,0911 | 0,0408 | 84,03 | 4,35 | 0 % |

### Skala real (15 305 Dokumente)

| Wettbewerber | n | Wall s (Median) | IQR s | Dokumente/s | E brutto J | E netto J | mJ/Dok. | €/1 Mio. Dokumente (Haushalt) | €/1 Mio. Dokumente (Industrie) | g CO₂/1 Mio. Dokumente | ZJ-Score | niedr. Konfidenz |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `b10_rs_ort` | 5 | 38,068 | 38,036–38,216 | 402 | 7 192,0 | 6 897,1 | 469,913 | 0,0487 | 0,0218 | 44,90 | 1,07 | 0 % |
| `b04_ort_cuda` | 5 | 40,851 | 40,816–40,897 | 375 | 7 319,8 | 7 018,2 | 478,263 | 0,0496 | 0,0222 | 45,70 | 1,12 | 0 % |
| `b03_st_fp16_sorted` | 5 | 41,221 | 41,189–41,279 | 371 | 7 243,1 | 6 937,9 | 473,248 | 0,049 | 0,022 | 45,22 | 1,12 | 0 % |
| `b06_tei` | 5 | 43,420 | 43,277–43,434 | 352 | 6 753,5 | 6 406,2 | 441,262 | 0,0457 | 0,0205 | 42,17 | 1,11 | 0 % |
| `b02_st_fp16` | 5 | 52,214 | 52,165–52,336 | 293 | 8 326,4 | 7 939,6 | 544,034 | 0,0564 | 0,0252 | 51,99 | 1,35 | 0 % |
| `b07_vllm_pooling` | 5 | 68,311 | 67,647–69,221 | 224 | 6 308,5 | 5 742,8 | 412,184 | 0,0427 | 0,0191 | 39,39 | 1,34 | 0 % |
| `b08_llamacpp_embed` | 5 | 80,111 | 79,332–81,300 | 191 | 9 108,0 | 8 467,3 | 595,103 | 0,0617 | 0,0276 | 56,87 | 1,74 | 0 % |
| `b01_st_fp32` | 5 | 160,226 | 160,017–160,450 | 96 | 30 466,7 | 29 370,1 | 1 990,637 | 0,206 | 0,0923 | 190,22 | 4,51 | 0 % |

## Track C: Speicherung, Index, Suche (`c.dense`)

Ein Lauf = leerer Speicher → Laden (`ingest`) → Index (`index`) → 1 000 gefilterte Top-10-Anfragen nacheinander (`query`) → dieselben aus 16 Client-Threads (`query_c16`), Wall-Zeit inkl. Server-Start (`load`). Gewertet nur bei recall@10 ≥ 0,95 in jeder Filterklasse gegenüber exakter Suche (spec/track_c.md).

### Skala M (100 000 Dokumente)

| Wettbewerber | n | Wall s (Median) | IQR s | Dokumente/s | E brutto J | E netto J | mJ/Dok. | €/1 Mio. Dokumente (Haushalt) | €/1 Mio. Dokumente (Industrie) | g CO₂/1 Mio. Dokumente | ZJ-Score | niedr. Konfidenz |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `c00_exact_gpu` | 5 | 4,478 | 4,379–4,489 | 22 329 | 228,4 | 198,4 | 2,284 | 0,000237 | 0,000106 | 0,22 | 1,00 | 100 % |
| `c02_cuvs_cagra` | 5 | 6,236 | 6,170–6,260 | 16 035 | 263,9 | 218,8 | 2,639 | 0,000273 | 0,000122 | 0,25 | 1,27 | 0 % |
| `c01_faiss_cpu` | 5 | 16,883 | 16,805–16,953 | 5 923 | 1 140,4 | 1 027,8 | 11,404 | 0,00118 | 0,000529 | 1,09 | 4,34 | 0 % |
| `c05_duckdb_vss` | 5 | 26,811 | 26,729–27,377 | 3 730 | 1 388,1 | 1 204,1 | 13,881 | 0,00144 | 0,000644 | 1,33 | 6,03 | 0 % |
| `c07_lancedb` | 5 | 46,368 | 46,101–46,512 | 2 157 | 1 080,9 | 764,6 | 10,809 | 0,00112 | 0,000501 | 1,03 | 7,00 | 0 % |
| `c08_qdrant_cpu` | 5 | 46,428 | 46,419–47,593 | 2 154 | 885,6 | 562,3 | 8,856 | 0,000918 | 0,000411 | 0,85 | 6,34 | 0 % |
| `c08_qdrant_gpu` | 5 | 50,737 | 50,721–51,099 | 1 971 | 712,1 | 363,2 | 7,121 | 0,000738 | 0,00033 | 0,68 | 5,94 | 0 % |
| `c06_sqlite_vec` | 5 | 77,977 | 76,409–78,232 | 1 282 | 2 606,7 | 2 073,6 | 26,067 | 0,0027 | 0,00121 | 2,49 | 14,10 | 0 % |
| `c03_pgvector` | 5 | 123,965 | 123,939–124,790 | 807 | 3 277,6 | 2 420,1 | 32,776 | 0,0034 | 0,00152 | 3,13 | 19,93 | 0 % |

### Skala real (15 305 Dokumente)

| Wettbewerber | n | Wall s (Median) | IQR s | Dokumente/s | E brutto J | E netto J | mJ/Dok. | €/1 Mio. Dokumente (Haushalt) | €/1 Mio. Dokumente (Industrie) | g CO₂/1 Mio. Dokumente | ZJ-Score | niedr. Konfidenz |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `c00_exact_gpu` | 5 | 2,828 | 2,813–2,872 | 5 413 | 67,4 | 46,1 | 4,406 | 0,000456 | 0,000204 | 0,42 | 1,00 | 100 % |
| `c01_faiss_cpu` | 5 | 3,243 | 3,242–3,340 | 4 719 | 197,4 | 174,6 | 12,899 | 0,00134 | 0,000598 | 1,23 | 1,83 | 100 % |
| `c02_cuvs_cagra` | 5 | 5,391 | 5,328–5,520 | 2 839 | 154,8 | 113,2 | 10,114 | 0,00105 | 0,000469 | 0,97 | 2,09 | 0 % |
| `c05_duckdb_vss` | 5 | 8,556 | 8,175–8,637 | 1 789 | 299,4 | 242,8 | 19,560 | 0,00203 | 0,000907 | 1,87 | 3,67 | 0 % |
| `c06_sqlite_vec` | 5 | 12,507 | 12,477–12,597 | 1 224 | 402,3 | 317,9 | 26,283 | 0,00272 | 0,00122 | 2,51 | 5,14 | 0 % |
| `c03_pgvector` | 5 | 16,161 (CV>5 %) | 15,108–16,510 | 947 | 319,9 | 207,7 | 20,899 | 0,00217 | 0,000969 | 2,00 | 5,21 | 0 % |
| `c08_qdrant_cpu` | 5 | 16,191 | 15,673–16,204 | 945 | 159,9 | 50,5 | 10,447 | 0,00108 | 0,000485 | 1,00 | 3,68 | 0 % |
| `c07_lancedb` | 5 | 23,512 | 23,397–23,515 | 651 | 274,5 | 112,3 | 17,938 | 0,00186 | 0,000832 | 1,71 | 5,82 | 0 % |
| `c08_qdrant_gpu` | 5 | 24,621 | 24,416–24,914 | 622 | 380,1 | 190,9 | 24,833 | 0,00257 | 0,00115 | 2,37 | 7,01 | 0 % |

### Stufen, Latenz, Qualität (Mediane)

Latenz: client-seitig je Anfrage (Konkurrenz 1), vom Wettbewerber gemeldet; QPS c16 = 1 000 Anfragen aus 16 Client-Threads / Wall-Zeit; mJ/Anfrage = Energie der Stufe (brutto, GPU + CPU) / 1 000. nDCG@10 (synthetisch, abgestufte Relevanz) bzw. hit@10 (real, Known-Item) = Qualität der Treffer; die exakte Suche ist die Obergrenze des Dense-Teils. Kompositum = Anfragen aus einem Wort (spec/track_c.md).

| Wettbewerber | Skala | load s | ingest s | index s | query s | p50 ms | p95 ms | p99 ms | QPS c1 | QPS c16 | mJ/Anfrage c1 | mJ/Anfrage c16 | recall@10 | nDCG@10 | hit@10 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `c00_exact_gpu` | M | 1,0 | 0,1 | 0,0 | 1,2 | 1,08 | 1,10 | 1,10 | 836 | 715 | 48,48 | 112,40 | 0,9999 | 0,8477 | – |
| `c01_faiss_cpu` | M | 0,0 | 0,1 | 10,7 | 3,5 | 3,02 | 8,62 | 13,42 | 285 | 442 | 206,66 | 146,43 | 0,9998 | 0,8476 | – |
| `c02_cuvs_cagra` | M | 0,4 | 0,1 | 1,3 | 0,6 | 0,44 | 1,15 | 1,17 | 1 548 | 328 | 60,88 | 119,37 | 0,9998 | 0,8477 | – |
| `c03_pgvector` | M | 3,2 | 1,8 | 106,8 | 10,2 | 7,02 | 33,75 | 34,73 | 98 | 760 | 254,88 | 105,17 | 0,9997 | 0,8477 | – |
| `c05_duckdb_vss` | M | 0,1 | 1,8 | 16,2 | 6,9 | 7,12 | 15,65 | 18,53 | 144 | 767 | 177,99 | 98,26 | 0,9987 | 0,8475 | – |
| `c06_sqlite_vec` | M | 0,0 | 2,3 | 0,0 | 62,4 | 54,47 | 88,78 | 97,11 | 16 | 79 | 1 585,21 | 962,91 | 0,9997 | 0,8477 | – |
| `c07_lancedb` | M | 0,5 | 1,0 | 9,3 | 30,5 | 25,44 | 62,27 | 65,51 | 33 | 191 | 292,24 | 212,92 | 0,9833 | 0,8476 | – |
| `c08_qdrant_cpu` | M | 0,8 | 24,2 | 13,6 | 4,1 | 3,91 | 6,38 | 7,03 | 244 | 517 | 36,97 | 26,57 | 0,9998 | 0,8477 | – |
| `c08_qdrant_gpu` | M | 2,6 | 24,1 | 16,1 | 4,1 | 3,88 | 6,20 | 6,73 | 246 | 522 | 62,01 | 25,89 | 0,9998 | 0,8477 | – |
| `c00_exact_gpu` | real | 1,1 | 0,0 | 0,0 | 0,3 | 0,23 | 0,25 | 0,26 | 2 956 | 1 734 | 8,70 | 11,80 | 0,9934 | – | 0,9990 |
| `c01_faiss_cpu` | real | 0,0 | 0,0 | 1,1 | 1,0 | 0,31 | 2,61 | 4,22 | 1 000 | 1 121 | 62,92 | 58,65 | 0,9869 | – | 0,9950 |
| `c02_cuvs_cagra` | real | 0,5 | 0,0 | 0,8 | 0,5 | 0,42 | 0,45 | 0,55 | 1 965 | 339 | 26,04 | 80,66 | 0,9865 | – | 0,9960 |
| `c03_pgvector` | real | 3,2 | 0,5 | 8,4 | 2,7 | 2,37 | 7,42 | 11,25 | 371 | 2 831 | 32,38 | 14,34 | 0,9902 | – | 0,9980 |
| `c05_duckdb_vss` | real | 0,1 | 0,5 | 1,7 | 5,2 | 5,13 | 7,53 | 8,25 | 193 | 1 338 | 138,32 | 60,64 | 0,9874 | – | 0,9970 |
| `c06_sqlite_vec` | real | 0,0 | 0,5 | 0,0 | 9,9 | 8,50 | 14,37 | 16,08 | 101 | 537 | 247,16 | 143,33 | 0,9920 | – | 0,9990 |
| `c07_lancedb` | real | 0,5 | 0,3 | 2,4 | 13,4 | 14,04 | 19,05 | 20,83 | 75 | 157 | 115,17 | 65,25 | 0,9855 | – | 0,9990 |
| `c08_qdrant_cpu` | real | 0,8 | 3,8 | 5,3 | 2,9 | 2,41 | 4,88 | 5,17 | 343 | 600 | 25,53 | 20,12 | 0,9926 | – | 0,9990 |
| `c08_qdrant_gpu` | real | 2,6 | 3,8 | 11,4 | 2,9 | 2,37 | 4,95 | 5,36 | 344 | 578 | 26,26 | 20,10 | 0,9918 | – | 0,9990 |

## Track C: lexikalische Suche (`c.lexical`, BM25)

Wie `c.dense`, aber gefilterte BM25-Top-10 (Tantivy, deutscher Snowball-Stemmer). Gewertet nur bei gleichstandsfestem BM25-Recall@10 ≥ 0,95 je Filterklasse gegenüber der Referenz (spec/track_c.md, ADR 0017).

### Skala M (100 000 Dokumente)

| Wettbewerber | n | Wall s (Median) | IQR s | Dokumente/s | E brutto J | E netto J | mJ/Dok. | €/1 Mio. Dokumente (Haushalt) | €/1 Mio. Dokumente (Industrie) | g CO₂/1 Mio. Dokumente | ZJ-Score | niedr. Konfidenz |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `c11_tantivy` | 5 | 1,489 | 1,465–1,504 | 67 164 | 26,8 | 16,9 | 0,268 | 0,0000278 | 0,0000124 | 0,03 | 1,00 | 100 % |

### Skala real (15 305 Dokumente)

| Wettbewerber | n | Wall s (Median) | IQR s | Dokumente/s | E brutto J | E netto J | mJ/Dok. | €/1 Mio. Dokumente (Haushalt) | €/1 Mio. Dokumente (Industrie) | g CO₂/1 Mio. Dokumente | ZJ-Score | niedr. Konfidenz |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `c11_tantivy` | 5 | 0,955 | 0,930–0,976 | 16 019 | 12,0 | 5,4 | 0,787 | 0,0000815 | 0,0000365 | 0,08 | 1,00 | 100 % |

### Stufen, Latenz, Qualität (Mediane)

Latenz: client-seitig je Anfrage (Konkurrenz 1), vom Wettbewerber gemeldet; QPS c16 = 1 000 Anfragen aus 16 Client-Threads / Wall-Zeit; mJ/Anfrage = Energie der Stufe (brutto, GPU + CPU) / 1 000. nDCG@10 (synthetisch, abgestufte Relevanz) bzw. hit@10 (real, Known-Item) = Qualität der Treffer; die exakte Suche ist die Obergrenze des Dense-Teils. Kompositum = Anfragen aus einem Wort (spec/track_c.md).

| Wettbewerber | Skala | load s | ingest s | index s | query s | p50 ms | p95 ms | p99 ms | mJ/Anfrage c1 | BM25-recall@10 | nDCG@10 | nDCG@10 Kompositum | hit@10 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `c11_tantivy` | M | 0,0 | 0,6 | 0,3 | 0,6 | 0,51 | 1,21 | 1,46 | 12,64 | 1,0000 | 0,7770 | 1,0000 | – |
| `c11_tantivy` | real | 0,0 | 0,4 | 0,3 | 0,2 | 0,13 | 0,66 | 1,00 | 2,19 | 0,9995 | – | – | 0,9970 |

## Track C: Hybrid-Suche (`c.hybrid`, Dense + lexikalisch, RRF k = 60)

Jede Engine mit eigenem Vektorindex und eigener Volltextsuche, je 100 Kandidaten, fusioniert per RRF. Wall-Zeit enthält die Prüfstufe `legs` (beide Kandidatenlisten erneut, bei allen gleich); Latenz aus Stufe `query`. Postgres `ts_rank` ist kein BM25; SQLite FTS5 hat keinen deutschen Stemmer. Gewertet nur, wenn Dense-Bein (Recall ≥ 0,95), Fusion (≥ 0,95) und lexikalisches Bein (Plausibilität) bestehen (ADR 0017).

### Skala M (100 000 Dokumente)

| Wettbewerber | n | Wall s (Median) | IQR s | Dokumente/s | E brutto J | E netto J | mJ/Dok. | €/1 Mio. Dokumente (Haushalt) | €/1 Mio. Dokumente (Industrie) | g CO₂/1 Mio. Dokumente | ZJ-Score | niedr. Konfidenz |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `c08_qdrant_hybrid` | 5 | 66,616 | 66,460–67,207 | 1 501 | 1 071,9 | 615,6 | 10,719 | 0,00111 | 0,000497 | 1,02 | 1,00 | 0 % |
| `c07_lancedb_hybrid` | 5 | 101,844 | 100,876–102,012 | 982 | 1 435,9 | 696,8 | 14,359 | 0,00149 | 0,000666 | 1,37 | 1,43 | 0 % |
| `c03_pgvector_hybrid` | 5 | 125,137 | 125,103–125,214 | 799 | 4 187,8 | 3 268,6 | 41,878 | 0,00434 | 0,00194 | 4,00 | 2,71 | 0 % |
| `c05_duckdb_hybrid` | 5 | 134,559 | 134,385–134,655 | 743 | 4 170,8 | 3 239,3 | 41,708 | 0,00432 | 0,00193 | 3,99 | 2,80 | 0 % |
| `c06_sqlite_hybrid` | 5 | 184,241 | 181,323–184,284 | 543 | 4 699,6 | 3 441,8 | 46,996 | 0,00487 | 0,00218 | 4,49 | 3,48 | 0 % |

### Skala real (15 305 Dokumente)

| Wettbewerber | n | Wall s (Median) | IQR s | Dokumente/s | E brutto J | E netto J | mJ/Dok. | €/1 Mio. Dokumente (Haushalt) | €/1 Mio. Dokumente (Industrie) | g CO₂/1 Mio. Dokumente | ZJ-Score | niedr. Konfidenz |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `c03_pgvector_hybrid` | 5 | 26,480 | 26,101–26,863 | 578 | 691,5 | 508,4 | 45,178 | 0,00468 | 0,0021 | 4,32 | 1,62 | 0 % |
| `c06_sqlite_hybrid` | 5 | 26,678 | 26,669–26,740 | 574 | 688,0 | 494,4 | 44,951 | 0,00466 | 0,00209 | 4,30 | 1,62 | 0 % |
| `c08_qdrant_hybrid` | 5 | 28,474 | 28,140–28,982 | 538 | 264,9 | 67,9 | 17,310 | 0,00179 | 0,000803 | 1,65 | 1,04 | 0 % |
| `c07_lancedb_hybrid` | 5 | 48,705 | 48,179–48,949 | 314 | 475,7 | 142,1 | 31,084 | 0,00322 | 0,00144 | 2,97 | 1,82 | 0 % |
| `c05_duckdb_hybrid` | 5 | 57,115 | 56,976–57,171 | 268 | 1 522,7 | 1 133,5 | 99,488 | 0,0103 | 0,00462 | 9,51 | 3,52 | 0 % |

### Stufen, Latenz, Qualität (Mediane)

Latenz: client-seitig je Anfrage (Konkurrenz 1), vom Wettbewerber gemeldet; QPS c16 = 1 000 Anfragen aus 16 Client-Threads / Wall-Zeit; mJ/Anfrage = Energie der Stufe (brutto, GPU + CPU) / 1 000. nDCG@10 (synthetisch, abgestufte Relevanz) bzw. hit@10 (real, Known-Item) = Qualität der Treffer; die exakte Suche ist die Obergrenze des Dense-Teils. Kompositum = Anfragen aus einem Wort (spec/track_c.md).

| Wettbewerber | Skala | load s | ingest s | index s | query s | p50 ms | p95 ms | p99 ms | mJ/Anfrage c1 | nDCG@10 | nDCG Kompositum | nDCG lex | nDCG lex Komp. | nDCG dense | hit@10 | hit lex | hit dense | Fusion |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `c03_pgvector_hybrid` | M | 3,1 | 3,7 | 22,5 | 48,1 | 25,83 | 154,71 | 189,42 | 1 307,34 | 0,8041 | 1,0000 | 0,7308 | 1,0000 | 0,8475 | – | – | – | 0,9997 |
| `c05_duckdb_hybrid` | M | 0,1 | 1,9 | 18,8 | 56,8 | 59,22 | 74,99 | 84,00 | 1 504,84 | 0,8313 | 1,0000 | 0,7772 | 1,0000 | 0,8476 | – | – | – | 1,0000 |
| `c06_sqlite_hybrid` | M | 0,0 | 2,6 | 0,2 | 90,1 | 94,03 | 115,54 | 117,60 | 2 312,70 | 0,8273 | 1,0000 | 0,7740 | 1,0000 | 0,8477 | – | – | – | 1,0000 |
| `c07_lancedb_hybrid` | M | 0,5 | 0,9 | 9,8 | 40,5 | 37,51 | 71,28 | 74,81 | 375,78 | 0,8316 | 1,0000 | 0,7771 | 1,0000 | 0,8476 | – | – | – | 1,0000 |
| `c08_qdrant_hybrid` | M | 0,8 | 27,0 | 17,7 | 6,7 | 6,58 | 8,25 | 9,23 | 62,55 | 0,8294 | 1,0000 | 0,7716 | 1,0000 | 0,8477 | – | – | – | 0,9724 |
| `c03_pgvector_hybrid` | real | 3,2 | 1,4 | 3,2 | 8,9 | 3,21 | 37,86 | 99,07 | 237,80 | – | – | – | – | – | 1,0000 | 1,0000 | 0,9990 | 1,0000 |
| `c05_duckdb_hybrid` | real | 0,1 | 0,5 | 3,0 | 26,6 | 25,35 | 35,58 | 42,13 | 699,51 | – | – | – | – | – | 0,9990 | 0,9970 | 0,9980 | 1,0000 |
| `c06_sqlite_hybrid` | real | 0,0 | 0,6 | 0,1 | 12,9 | 10,60 | 22,97 | 27,88 | 332,44 | – | – | – | – | – | 1,0000 | 0,9960 | 0,9990 | 1,0000 |
| `c07_lancedb_hybrid` | real | 0,5 | 0,4 | 3,0 | 22,2 | 23,12 | 27,89 | 28,97 | 191,00 | – | – | – | – | – | 1,0000 | 0,9970 | 0,9990 | 1,0000 |
| `c08_qdrant_hybrid` | real | 1,9 | 4,6 | 8,3 | 4,4 | 3,96 | 6,75 | 8,23 | 38,29 | – | – | – | – | – | 1,0000 | 0,9970 | 0,9990 | 0,9855 |
