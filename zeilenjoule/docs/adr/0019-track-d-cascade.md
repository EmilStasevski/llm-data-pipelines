# ADR 0019: Track D – Kaskade spart Ausgabe-Token statt LLM-Aufrufe; GLiNER nur als eigener Kandidat

**Status:** angenommen (2026-11-09)

## Kontext
PLAN §10 skizziert die Kaskade L0 Regeln → L1 GLiNER → L2 LLM nur für Ungelöstes und will den Anteil der
Zeilen sehen, die das LLM erreichen. Messung auf der Dev-Menge (300 Zeilen, ungemessen):
- Track-A-Regeln treffen `sicherheitsrelevant`, `stillstand_minuten`, `ersatzteil_materialnr` zu 1,000,
  `komponente` zu 0,97 (17 von 2 000 ohne Treffer) – die LLMs liegen bei `komponente` bei 0,99–1,00.
- SauerkrautLM-GLiNER (Spans auf Katalognamen abgebildet) löst bei Schwelle 0,3 nur 34 % `schadensart`
  (davon 80 % richtig), 21 % `ursache`, 6 % `massnahme` auf; „kein Span“ heißt nicht „null“ (43 % der Ursachen
  sind null). Damit wären nur 1 % der Zeilen ohne LLM vollständig.

## Entscheidung
- **d11 = Regeln + LLM für die vier semantischen Felder** (komponente, schadensart, ursache, massnahme) mit
  verkleinertem Prompt und Schema; die Ersparnis kommt aus weniger Ausgabe-Token (≈ 56 statt 93 je Zeile bei
  Gemma 4 E4B), nicht aus weniger Aufrufen. LLM-Anteil der Zeilen = 100 % (im Ergebnis vermerkt).
- **GLiNER wird nicht als Tor benutzt**, sondern als eigener Kandidat **d10** (Regeln + GLiNER, kein LLM)
  gemessen: die Nicht-LLM-Untergrenze (ungemessen macro-F1 0,775 in 20 s für 2 000 Zeilen).
- Abbildung der Spans nur auf Katalog**namen** (die der Prompt auch bekommt), nicht auf die Phrasenlisten des
  Datengenerators – sonst würde die Regel die synthetische Erzeugung zurückrechnen.
- Prompts der Kaskade entstehen wie alle anderen zentral (`zj_eval.track_d prepare`); die bisherigen
  Prompt-Dateien bleiben bytegleich (Prüfsummen geprüft).

## Konsequenzen
- Die Kaskade ist an Gemma 4 E4B gebunden (sparsamste gewertete Konfiguration); andere LLMs wären weitere
  Kandidaten. Auf echten Meldungen ohne Katalogtreffer kann der Regelanteil kleiner sein.
- Ungemessene Probe (D2k): d11 5,2 Zeilen/s, macro-F1 0,966; d10 macro-F1 0,775.
