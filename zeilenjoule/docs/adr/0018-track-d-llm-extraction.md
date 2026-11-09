# ADR 0018: Track D – identische Prompts, Kataloge im Prompt, strenge JSON-Gültigkeit, Sweep auf `dev`

**Status:** angenommen (2026-11-09)

## Kontext
PLAN §10 verlangt JSON nach Schema, Constrained Decoding, Reasoning aus, macro-F1 und JSON-Gültigkeit
≥ 99,5 %, einen Konkurrenz-Sweep N ∈ {1, 4, 16, 64} mit Qwen3.5-9B Q4 und danach alle Modelle. Die
Gold-Werte von `komponente`, `ursache` und `massnahme` sind **kanonische Katalogwerte** (z. B. „Ventil“ →
`Regelventil`, „Austausch durchgeführt“ → `Bauteil getauscht`).

## Entscheidung
- **Prompts einmal erzeugen** (`zj_eval.track_d prepare`) und als JSONL ablegen: jede Engine sendet dieselben
  Nachrichten; der Client ist für alle Engines derselbe (`zj_llm.extract`).
- **Kataloge im Systemprompt** (Komponenten mit Schreibweisen, Ursachen, Maßnahmen): so arbeitet ein
  SAP-PM-Katalog; ohne Katalog misst Token-F1 nur, ob das Modell zufällig die Gold-Schreibweise trifft.
  Das Schema bleibt laut PLAN `string|null` (nur `schadensart` ist Enum).
- **Strenge Gültigkeit**: nur der ganze Text als Schema-konformes Objekt zählt; kein Reparieren, kein Abziehen
  von Markdown. Constrained Decoding macht das für alle Engines erreichbar.
- **Gate in `zj verify`** = Gültigkeit ≥ 99,5 % + kein Reasoning (PLAN §10); macro-F1 wird berichtet, das
  Gewinner-Gate (≥ 0,9 × bestes) braucht alle Läufe und gehört in den Bericht.
- **Sweep auf der Dev-Menge** (300 Zeilen, Skala `dev`, 1 Wiederholung): die Wahl von N berührt die
  Testmenge nicht und kostet ~1 h statt ~6 h. vLLM nutzt AWQ 4 bit (GGUF ist in vLLM nicht der übliche Weg),
  Ollama sein Registry-Modell `qwen3.5:9b` (Q4_K_M, eigene Konvertierung): so benutzt man die Werkzeuge.
- **Ollama im Docker-Image** (gepinnt), Modelle unter `models/ollama`: die Host-Installation (0.13.5, ohne
  Qwen-3.5-Unterstützung) bleibt unberührt.

## Konsequenzen
- Ollama- und vLLM-Läufe nutzen andere Gewichtsdateien als llama.cpp (gleiche Bitbreite); im Bericht benannt.
- gpt-oss lässt sich nicht ganz ohne Reasoning betreiben; seine Reasoning-Token zählen in `completion_tokens`
  und können an `max_tokens` 256 scheitern (→ ungültiges JSON → disqualifiziert, nicht versteckt).
- Ungemessene Probe (Qwen3.5-9B Q4, llama.cpp, N = 4, 200 Zeilen): macro-F1 0,953 (0-shot) / 0,975
  (3-shot), Gültigkeit 1,0; nur Richtwert, Messung folgt.
