# ADR 0021: Produktionsarchitektur = e03 (Postgres + pgvector, TEI, vLLM), Go-Dienste, Rust-Parser als Binary

**Status:** angenommen (2026-11-11, Gate 2 vom Nutzer bestätigt)

## Kontext
PLAN §11.1 Regel 5: Produktions-Stack = schnellste End-to-End-Pipeline, die alle Gates besteht; Abwägung nur, wenn
sie > 1,5 × die Energie der effizientesten braucht oder > 2 Dienste mehr als die nächstschnellere. Gemessen
(Session 444b1e05, L): e03 1 740 s / 247,9 kJ / Qualität 0,754; e02 1 864 s; e05 1 954 s; e01 5 330 s.

## Entscheidung
- Stack von **e03_postgres_only**: PostgreSQL 18 + pgvector (Vektoren, Volltext, SQL-Sicht, River-Queue), TEI
  bge-m3, vLLM Qwen3.5-9B AWQ als einziges LLM (Router, Antworten, Extraktion), Go-Gateway + Go-Worker.
- Rust-Parser (a16/a17) als vom Worker aufgerufenes Binary, kein gRPC-Dienst (Parsen < 0,1 % der Energie).
- Keine zweite LLM-Instanz für die Kaskade d11 (16 GB VRAM); Extraktion mit demselben vLLM-Modell, 3-shot.

## Konsequenzen
- Regel 5 ausgelöst? Nein: e03 ist zugleich am effizientesten (Verhältnis 1,00), Δ Dienste zu e02 = +2 (Grenze > 2).
- Erster Phase-9-Schritt: TEI und vLLM gleichzeitig auf einer GPU (vLLM-Speicheranteil senken), dann Lasttests.
- Alternativen für Gate 2: e02 (DuckDB eingebettet, ein Dienst, 7 % langsamer, 16 % mehr Energie) für einen
  Betrieb ohne Datenbankserver; e05/Qdrant nur, wenn Vektorsuche getrennt skalieren muss.

## Nachtrag 2026-11-11: GPU-Teilung geprüft (ungemessen)
TEI (bge-m3 fp16, `--max-batch-tokens 16384`) + vLLM (Qwen3.5-9B AWQ, `--gpu-memory-utilization 0.70`,
`--max-num-seqs 32`, 4 096 Kontext) laufen gleichzeitig: 12,8 von 16,3 GB belegt, KV-Cache 38 550 Token. Durchsatz:
Einbetten allein 1 341 Texte/s; LLM allein 401 Tok/s, mit gleichzeitigem Einbetten von 10 000 Texten 343 Tok/s (−14 %).
TEI lehnt mehr als ≈ 512 gleichzeitige Eingaben mit HTTP 429 ab → der Worker begrenzt Einbett-Anfragen (4 × 128).
