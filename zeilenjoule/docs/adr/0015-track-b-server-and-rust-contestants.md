# ADR 0015: Track-B-Server-Wettbewerber (b06–b08) und Rust-ort (b10)

**Status:** angenommen (2026-11-08)

## Kontext
TEI, vLLM und llama.cpp sind Server (Docker). Der Harness hat ein `servers`-Feld, startet aber keine
Server. PLAN §5.3 verlangt, dass Serverstart und Modell-Laden als eigene Stufe `load` gemessen werden.
Die Referenz schneidet bei 512 Token ab; im realen Korpus sind 485 Dokumente länger.

## Entscheidung
- **Jeder Server-Wettbewerber verwaltet seinen Container selbst** (`zj_embed.track_b.server`): `docker run`
  (Image per Digest gepinnt, `--network host`, nur `127.0.0.1`) → `/health` → GPU-Nachweis → Ende `load`;
  danach `read`, `embed` (HTTP, mehrere Requests parallel, Batching macht der Server), `write`; der
  Container wird immer entfernt, sein Log landet im Run-Log. Energie ist systemweit gemessen, der Server
  ist also im Messfenster enthalten.
- **GPU-Nachweis (Regel 6):** VRAM der Container-Prozesse (NVML, per Host-PID) ≥ Gewichtsgröße, plus ein
  Log-Marker, wo der Server einen ausgibt (TEI `on Cuda`, vLLM `pooling_type=CLS`). llama.cpp:
  `-dev CUDA0 -ngl 99 --fit off` (sonst Startfehler).
- **Gleiche Trunkierung wie die Referenz:** TEI und vLLM schneiden serverseitig bei 512 ab (TEI über
  `models/derived/bge-m3/hf512`, vLLM über `--max-model-len`/`truncate_prompt_tokens`). llama.cpp lehnt zu
  lange Eingaben ab → der Client tokenisiert (HF-Tokenizer, wie b03/b04) und sendet Token-IDs.
- **Gleiches Modell:** alle nutzen bge-m3 @ `5617a9f6…` (TEI/vLLM: safetensors-Konvertierung, llama.cpp:
  GGUF f16 mit dem Konverter desselben Commits wie das Image). PLANs „vLLM (Qwen3-Embedding)" wird
  zurückgestellt: ein anderes Modell würde das Kosinus-Gate gegen die bge-m3-Referenz nicht bestehen
  können; Qwen3-Embedding kommt ggf. als eigener Modellvergleich.
- **b10_rs_ort:** Rust `ort` lädt dieselbe `libonnxruntime.so` 1.31.1, denselben fp16-Graphen und nutzt
  dasselbe Batching wie b04 → der Vergleich isoliert die Host-Sprache.
- **Tuning** (nicht gemessene Proben auf `real`, unruhige Maschine): TEI 64 Texte × 8 parallel,
  `--max-batch-tokens 65536`; vLLM 256 × 8 (Konfiguration kaum relevant); llama.cpp `-fa on`, 16 Slots,
  `-ub 1024` (größere Micro-Batches rechnen eine maskierte Attention über alle gepackten Sequenzen).

## Folgen
- Die `load`-Stufe enthält Containerstart; vLLM liest im gemessenen Lauf den im Warm-up gefüllten
  Compile-Cache (`models/cache/vllm`). Kalt-Start wird in COMPAT festgehalten, nicht gerankt.
- Ein späterer Harness-Server-Mechanismus (für Track C/D) kann diese Logik übernehmen.
