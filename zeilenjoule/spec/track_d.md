# Track D: Spezifikation / specification (`d.extract`)

> Zeilenweise LLM-Extraktion: aus jeder Störmeldung ein JSON-Objekt nach `spec/schema_stoermeldung.json`.
> Referenz und Bewertung: `reference/src/zj_reference/track_d.py`, Eingaben: `task trackD:prep`.

## 1. Eingabe / Ausgabe
- **Skalen:** `D2k` (2 000 Zeilen, Messung) und `dev` (300 Zeilen, nur Konkurrenz-Sweep, PLAN §10 Schritt 1).
- **Eingabe:** `data/track_d/<skala>/prompts.{0shot,3shot}.jsonl`, je Zeile `{"meldung_id", "messages"}` in
  Eingabereihenfolge. Alle Engines senden **dieselben Bytes** (Systemprompt + optional 3 Beispiele + Meldung).
  `dev` hat nur 0-shot: die drei Beispiele stammen aus `dev`.
- **Ausgabe:** `out.jsonl`, eine Zeile je Eingabezeile in Eingabereihenfolge:
  `{"meldung_id", "text", "completion_tokens", "prompt_tokens", "finish_reason", "latency_ms"}`;
  `text` = unveränderte Modellantwort.

## 2. Prompt
- Deutscher Systemprompt mit den Katalogen aus `spec/lexicon/` (Komponenten mit Schreibweisen,
  Schadensarten = Enum, Ursachen, Maßnahmen, Sicherheits-Auslöser) und den Regeln für Stillstand
  (ganze Minuten, Bereich → Obergrenze) und Materialnummer (nur Ziffern). Identisch für alle Zeilen →
  gemeinsamer Präfix → Prefix-Caching.
- 3-shot: drei feste Dev-Zeilen (vollständig mit Ersatzteil; Sicherheitsereignis mit Lücken; verrauscht).
- Nachrichtentext: `Anlage`, `Equipment`, `Meldungstext`, `Stillstand` (Spalten aus `d2k.parquet`).

## 3. Dekodierung (alle Engines)
temperature 0, seed 0, `max_tokens` 256, **JSON-Schema als Constrained Decoding** (OpenAI `response_format`
json_schema bei llama.cpp/vLLM, `format` bei Ollama). Reasoning aus: Qwen 3.5 / Gemma 4
`enable_thinking=false` (Ollama `think: false`); gpt-oss hat keinen Aus-Schalter → `reasoning_effort=low`.

## 4. Bewertung
- **gültig** = gesamter Text (äußere Leerzeichen entfernt) ist *ein* JSON-Objekt, das das Schema erfüllt
  (Typen, Enum, alle Schlüssel, keine weiteren). Kein Markdown-Zaun, kein Reparieren.
- Exakt: `schadensart`, `sicherheitsrelevant`, `stillstand_minuten`, `ersatzteil_materialnr` (nur Ziffern
  verglichen). Token-F1 (case-folded Wörter): `komponente`, `ursache`, `massnahme`. null/null = 1,
  null/Wert = 0. Ungültige Zeile = 0 in allen Feldern. **macro-F1** = Mittel der sieben Feldmittel.

## 5. Verifikation (`zj verify`, Regel 4)
Bestanden, wenn alle Zeilen in Reihenfolge vorhanden sind, **JSON-Gültigkeit ≥ 99,5 %** und kein
Reasoning-Ausstoß (`<think>`, completion_tokens > 256). Die Qualität (macro-F1) wird berichtet; das
Gewinner-Gate **macro-F1 ≥ 0,9 × bestes macro-F1** wendet der Bericht über alle Läufe an.

## 6. Läufe
Stufen: `load` (Container, Modell, GPU-Nachweis, eine ungezählte Anfrage: Kernel + Präfix-Cache),
`extract` (alle Zeilen, N Anfragen gleichzeitig), `write`, `ttft` (20 Zeilen einzeln gestreamt, außerhalb
des Vergleichs). Kennzahlen: Zeilen/s und Output-Token/s aus `extract`, J/Zeile, VRAM, TTFT.

## 7. Ohne LLM und Kaskade (`d10_gliner`, `d11_cascade`, ADR 0019)
- **Stufe 0 (Regeln)** = Track-A-Regeln (spec/track_a.md): `komponente` (Wörterbuch), `sicherheitsrelevant`
  (Auslöser), `stillstand_minuten` (Spalte Stillstand), `ersatzteil_materialnr` (Regex).
- **Stufe 1 (GLiNER)** = SauerkrautLM-GLiNER, Labels Bauteil/Schaden/Ursache/Maßnahme, Schwelle 0,3 (auf `dev`
  gewählt). Ein Span zählt, wenn er alle Wörter (≥ 4 Buchstaben, gefaltet, als Teilstring) genau eines
  Katalog**namens** enthält (keine Phrasenlisten des Generators); mehrere → längster Name.
- **d10** = Stufe 0 + 1, nicht zugeordnet: `schadensart` = „Sonstiges“, `ursache`/`massnahme` = null.
- **d11** = Stufe 0 für Sicherheit/Stillstand/Materialnummer + **LLM nur für die vier semantischen Felder**
  (`prompts.semantic3shot.jsonl`, `spec/schema_stoermeldung_semantic.json`; llama.cpp, Gemma 4 E4B Q4, 3-shot,
  N = 16 – die sparsamste Konfiguration der Hauptmatrix). Ungültige LLM-Antwort → Werte aus Stufe 0/1.
  Alle Zeilen erreichen das LLM (Stufe 1 löst auf `dev` zu wenig auf, siehe ADR 0019).

## English summary
Each engine sends identical prompts (German system prompt with the lexicon catalogues, optional 3 fixed
dev-set shots) with temperature 0, max_tokens 256, JSON-schema constrained decoding and reasoning off, and
writes the raw model text per row. Valid = the whole text is one schema-conforming JSON object. Exact match
for enum/bool/int/material number, token-F1 for component/cause/action, macro-F1 = mean of the seven
fields. `zj verify` passes with all rows in order, JSON validity ≥ 99.5 % and no reasoning output; the
winner gate (macro-F1 ≥ 0.9 × best) is applied by the report. Concurrency sweep on the 300-row dev set.
§7: d10 = Track A rules + GLiNER spans mapped onto catalogue names (no LLM); d11 cascade = rules for
safety/downtime/material number + the LLM (Gemma 4 E4B, 3-shot) for the four semantic fields only.
