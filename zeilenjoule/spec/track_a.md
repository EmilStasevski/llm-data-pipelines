# Track A: Spezifikation / specification (`a.full`)

*Version 1.0 (Phase 4, 2026-11-07). Verbindlich für alle Wettbewerber; Referenz: `reference/src/zj_reference/track_a.py`.
Gemeinsame Unit-Tests: `spec/track_a_cases.jsonl`. Ausgabeschema: `spec/track_a.schema.json`.
Kompiliertes Lexikon (Eingabe aller Wettbewerber): `spec/track_a_lexicon.json`.*

Deutsch zuerst, English summary at the end. Regexes are **RE2 syntax** (no lookaround, no backreferences).

## 0. Eingabe / Ausgabe

- Eingabe: `data/synth/stoermeldungen/<Skala>/stoermeldungen.cp1252.csv`: cp1252, Trennzeichen `;`,
  Quote `"` (RFC-4180-Verdopplung), Zeilenende CRLF, eine Kopfzeile. Spalten: `meldung_id;werk;anlage;
  equipment_nr;meldungsdatum;schicht;prioritaet;meldungstext;stillstand;kosten;status`. Leeres Feld = leerer String.
- Lexikon: `spec/track_a_lexicon.json` (darf zur Laufzeit gelesen werden; das Einlesen zählt zur Laufzeit).
- Ausgabe: eine Parquet-Datei (`{output}`), Kompression zstd, Schema exakt wie `spec/track_a.schema.json`,
  Zeilen aufsteigend nach `meldung_id` (bytewise; IDs sind gleich lang).

## 1. Textnormalisierung `norm(s)` (für **alle** Textspalten)

1. **Mojibake-Reparatur** mit der festen Tabelle `mojibake` aus dem Lexikon (`Ã¤→ä Ã¶→ö Ã¼→ü ÃŸ→ß Ã„→Ä Ã–→Ö
   Ãœ→Ü â‚¬→€`): jede Vorkommnis ersetzen, längster Schlüssel zuerst (`â‚¬` vor den zweistelligen). Kein ftfy.
2. **Unicode NFC.**
3. **Leerraum**: Leerraumzeichen sind genau `U+0009 U+000A U+000B U+000C U+000D U+0020 U+00A0`.
   Jede Folge davon wird zu einem einzelnen `U+0020`; danach Anfang/Ende trimmen.

## 2. Felder

| Ausgabe | Typ | Regel |
|---|---|---|
| `meldung_id` | string | `norm(meldung_id)` |
| `werk`, `anlage`, `schicht`, `status` | string | `norm(…)`; leerer String bleibt leer (nicht null) |
| `equipment_nr` | string, null | `norm(equipment_nr)` gegen `^([A-Z])[ -]?([0-9]{4})(-[A-Z])?$` → `"{1}-{2}{3}"` (z. B. `M 0815-A` → `M-0815-A`, `P-4711` → `P-4711`); kein Treffer → null |
| `datum` | date, null | §3 |
| `prioritaet` | int8, null | `norm(prioritaet)` gegen `^[0-9]+$` → Ganzzahl, nur 1–4 gültig, sonst null |
| `stillstand_min` | int32, null | §5 |
| `kosten_eur` | float64, null | §4 |
| `text_norm` | string | §6 auf `norm(meldungstext)` |
| `text_lower` | string | `lower(text_norm)` (Unicode-Kleinschreibung, **nicht** casefold: `ß` bleibt `ß`) |
| `text_fold` | string | `text_lower` mit `ä→ae ö→oe ü→ue ß→ss` |
| `materialnr` | string, null | erster Treffer von `(^|[^0-9])(10[0-9]{6})([^0-9]|$)` in `text_norm`, Gruppe 2 |
| `sicherheitsrelevant_rule` | bool | wahr, wenn ein Eintrag aus `safety_triggers_fold` als **Teilstring** in `text_fold` vorkommt |
| `komponente_rule` | string, null | §7 |
| `regierungsbezirk`, `nuts2` | string, null | Lookup `werke[werk]` im Lexikon; unbekanntes Werk → null |
| `doc_text` | string | §8 |

## 3. Datum

Auf `norm(meldungsdatum)`:
- `^([0-9]{1,2})\.([0-9]{1,2})\.([0-9]{2}|[0-9]{4})$` → Tag, Monat, Jahr; zweistelliges Jahr `YY` → `2000 + YY`.
- `^([0-9]{4})-([0-9]{2})-([0-9]{2})$` → ISO.
- Nur gültige Kalenderdaten (Schaltjahre beachten); `31.02.2026`, `00.05.2026`, `2026-13-01` → null. Sonst null.

## 4. Deutsche Zahlen (Kosten)

Auf `norm(kosten)`, Muster (ganz):
`^(-)?([0-9]{1,3}(?:[. ][0-9]{3})+|[0-9]+)(?:,([0-9]+))?(?: ?(?:€|EUR))?$`
(nach `norm` ist jedes NBSP bereits ein Leerzeichen). Wert = `[-]` + Ganzzahlteil ohne Trennzeichen + `.` +
Nachkommastellen, als float64 korrekt gerundet geparst. Kein Treffer (z. B. `k.A.`, leer) → null.

## 5. Dauern (Stillstand)

Auf `norm(stillstand)`, ohne Beachtung der Groß-/Kleinschreibung (Muster ganz):
`^(?:ca\. ?)?(?:([0-9]+(?:,[0-9]+)?) ?- ?)?([0-9]+(?:,[0-9]+)?) ?(h|std\.|stunde|stunden|min|minuten)$`
- Bei Bereichen zählt die **Obergrenze** (Gruppe 2).
- `h | std. | stunde | stunden` → × 60, `min | minuten` → × 1. Dezimalkomma erlaubt.
- Minuten = exakter Dezimalwert × Faktor, kaufmännisch gerundet (Hälfte weg von 0), int32. Kein Treffer → null.

## 6. Abkürzungen (`text_norm`)

Tokenweise auf `norm(meldungstext)` (nach §1 gibt es nur einfache Leerzeichen):
1. Text an `U+0020` in Tokens zerlegen.
2. Je Token: führende Zeichen aus `strip_leading` (`([{"'`) und abschließende Zeichen aus `strip_trailing`
   (`,;:!?)]}"'`) abtrennen (wiederholt, gierig). Der Punkt `.` wird **nicht** abgetrennt: Schlüssel wie `ca.` enden darauf.
3. Ist `lower(Kern)` ein Schlüssel von `abbreviations` (Schlüssel sind kleingeschrieben), wird der Kern durch die
   Ausschreibung ersetzt; Präfix und Suffix bleiben erhalten.
4. Tokens mit einem Leerzeichen wieder zusammenfügen.

Begründung: tokenweise statt Regex mit Grenzklasse. Lookbehind fehlt in RE2/Rust-regex; die Leerzeichen-Zerlegung ist
in jeder Sprache und in DataFrame-APIs identisch umsetzbar. `KW-Pumpe` wird daher nicht aufgelöst (das Wörterbuch
enthält die Form `kw-pumpe`).

## 7. Komponente (Wörterbuch, längster Treffer)

`komponenten_fold` im Lexikon: gefaltete Oberflächenform (≥ 4 Zeichen) → kanonischer Name. Gesucht wird in `text_fold`
nach **allen Vorkommen** jeder Form als Teilstring (auch innerhalb von Komposita). Gewählt wird die **längste** Form;
bei gleicher Länge die mit der **kleinsten Startposition**. Keine Form → null.

## 8. `doc_text`

Teile in dieser Reihenfolge, nur wenn der Wert nicht null und nicht leer ist, verbunden mit genau `" | "`:
`Werk: {werk}`, `Anlage: {anlage}`, `Equipment: {equipment_nr}`, `Datum: {datum als YYYY-MM-DD}`,
`Priorität: {prioritaet}`, `Meldung: {text_norm}`.

## 9. Verifikation (`zj verify`, Regel 4)

`zj_eval.verify_track_a` vergleicht Ausgabe und Referenzausgabe mit DuckDB: Spaltennamen und -typen exakt, Zeilenzahl,
dann je Spalte **wertweise** über `meldung_id` (float64: |Δ| ≤ 1e-9; Strings exakt; null = null). Ausgegeben werden
Anzahl Abweichungen je Spalte und die ersten 20 Unterschiede. Bytegleichheit der Parquet-Datei ist nicht nötig.

---

## English summary

- Input: the cp1252 CSV (`;`, CRLF) + `spec/track_a_lexicon.json`. Output: zstd Parquet with the exact schema of
  `spec/track_a.schema.json`, sorted by `meldung_id`.
- `norm`: fixed mojibake table (longest key first) → NFC → collapse the whitespace set {TAB, LF, VT, FF, CR, SPACE, NBSP}
  to one space, trim. Applied to every text column.
- Dates: `D{1,2}.M{1,2}.(YY|YYYY)` (YY → 20YY) or ISO; invalid calendar dates → null.
- Numbers: optional `-`, thousands `.`/space in groups of 3, decimal `,`, optional ` €`/` EUR`; else null.
- Durations: optional `ca.`, optional range (upper bound wins), units h/Std./Stunde(n) ×60, min/Minuten ×1,
  round half away from zero.
- Abbreviations: **per whitespace token** after stripping leading `([{"'` and trailing `,;:!?)]}"'`; case-insensitive
  lookup of the core; prefix/suffix kept. Portable without lookaround.
- `text_lower` = Unicode lower (not casefold), `text_fold` = lower + ä→ae ö→oe ü→ue ß→ss.
- Rules: equipment canonical form, first `10xxxxxx` material number in `text_norm`, safety = any folded trigger substring,
  component = longest folded dictionary substring match (ties → earliest start), plant → Regierungsbezirk/NUTS-2 lookup.
- `doc_text`: `Werk | Anlage | Equipment | Datum | Priorität | Meldung` parts, empty ones omitted, joined by `" | "`.
- Verification by value with DuckDB (floats 1e-9, strings exact).
