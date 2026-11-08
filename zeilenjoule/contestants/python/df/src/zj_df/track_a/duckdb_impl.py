"""DuckDB SQL implementation of spec/track_a.md (a13_duckdb_py; reused by a20_duckdb_cli).

Everything runs inside DuckDB: read_csv(encoding='windows-1252') → SQL macros → COPY … TO parquet.
DuckDB pitfalls handled: regexp_extract returns '' (not NULL) when nothing matches; read_csv yields NULL
for empty fields (spec wants ''); DECIMAL ROUND rounds half away from zero (exact durations).
"""

from __future__ import annotations

import json
from typing import Any


def _lit(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def macros(lex: dict[str, Any]) -> list[str]:
    moji = sorted(lex["mojibake"].items(), key=lambda kv: -len(kv[0]))
    repl = "s"
    for bad, good in moji:
        repl = f"replace({repl}, {_lit(bad)}, {_lit(good)})"
    fold = "s"
    for k, v in lex["fold"].items():
        fold = f"replace({fold}, {_lit(k)}, {_lit(v)})"
    lead = "".join("\\" + c if c in "[]^-\\" else c for c in lex["strip_leading"])
    trail = "".join("\\" + c if c in "[]^-\\" else c for c in lex["strip_trailing"])
    abbr = "MAP {" + ", ".join(f"{_lit(k)}: {_lit(v)}" for k, v in lex["abbreviations"].items()) + "}"
    safety = " OR ".join(f"contains(s, {_lit(t)})" for t in lex["safety_triggers_fold"])
    number = r"^(-)?([0-9]{1,3}(?:[. ][0-9]{3})+|[0-9]+)(?:,([0-9]+))?(?: ?(?:€|EUR))?$"
    duration = r"^(?:ca\. ?)?(?:([0-9]+(?:,[0-9]+)?) ?- ?)?([0-9]+(?:,[0-9]+)?) ?(h|std\.|stunde|stunden|min|minuten)$"
    return [
        f"CREATE OR REPLACE MACRO zj_norm(s) AS trim(regexp_replace(nfc_normalize({repl}), "
        r"'[\t\n\x0B\x0C\r \x{A0}]+', ' ', 'g'), ' ')",
        "CREATE OR REPLACE MACRO zj_equipment(s) AS CASE WHEN regexp_full_match(s, '([A-Z])[ -]?([0-9]{4})(-[A-Z])?') "
        "THEN regexp_extract(s, '^([A-Z])', 1) || '-' || regexp_extract(s, '([0-9]{4})', 1) "
        "|| regexp_extract(s, '(-[A-Z])$', 1) END",
        "CREATE OR REPLACE MACRO zj_date(s) AS CASE "
        r"WHEN regexp_full_match(s, '([0-9]{1,2})\.([0-9]{1,2})\.([0-9]{2}|[0-9]{4})') THEN TRY_CAST("
        r"(CASE WHEN length(regexp_extract(s, '\.([0-9]+)$', 1)) = 2 THEN '20' ELSE '' END)"
        r" || regexp_extract(s, '\.([0-9]+)$', 1) || '-' || lpad(regexp_extract(s, '^[0-9]+\.([0-9]+)', 1), 2, '0')"
        r" || '-' || lpad(regexp_extract(s, '^([0-9]+)', 1), 2, '0') AS DATE) "
        "WHEN regexp_full_match(s, '[0-9]{4}-[0-9]{2}-[0-9]{2}') THEN TRY_CAST(s AS DATE) END",
        "CREATE OR REPLACE MACRO zj_prio(s) AS CASE WHEN regexp_full_match(s, '[0-9]+') "
        "AND TRY_CAST(s AS BIGINT) BETWEEN 1 AND 4 THEN CAST(s AS TINYINT) END",
        f"CREATE OR REPLACE MACRO zj_eur(s) AS CASE WHEN regexp_matches(s, {_lit(number)}) THEN CAST("
        f"regexp_extract(s, {_lit(number)}, 1) || replace(replace(regexp_extract(s, {_lit(number)}, 2), '.', ''), ' ', '')"
        f" || '.' || coalesce(nullif(regexp_extract(s, {_lit(number)}, 3), ''), '0') AS DOUBLE) END",
        f"CREATE OR REPLACE MACRO zj_minutes(s) AS CASE WHEN regexp_matches(lower(s), {_lit(duration)}) THEN CAST(ROUND("
        f"CAST(replace(regexp_extract(lower(s), {_lit(duration)}, 2), ',', '.') AS DECIMAL(38, 18)) * "
        f"CASE WHEN regexp_extract(lower(s), {_lit(duration)}, 3) IN ('h', 'std.', 'stunde', 'stunden') THEN 60 ELSE 1 END"
        ", 0) AS INTEGER) END",
        f"CREATE OR REPLACE MACRO zj_expand(s) AS array_to_string(list_transform(string_split(s, ' '), lambda tok: "
        f"coalesce(regexp_extract(tok, {_lit(f'^([{lead}]*)')}, 1) || ({abbr})[lower(regexp_extract(tok, "
        f"{_lit(f'^[{lead}]*(.*?)[{trail}]*$')}, 1))] || regexp_extract(tok, {_lit(f'([{trail}]*)$')}, 1), tok)), ' ')",
        f"CREATE OR REPLACE MACRO zj_fold(s) AS {fold}",
        f"CREATE OR REPLACE MACRO zj_safety(s) AS ({safety})",
        "CREATE OR REPLACE MACRO zj_matnr(s) AS nullif(regexp_extract(s, '(^|[^0-9])(10[0-9]{6})([^0-9]|$)', 2), '')",
    ]


def tables(lex: dict[str, Any]) -> list[str]:
    forms = ", ".join(f"({_lit(f)}, {_lit(n)})" for f, n in lex["komponenten_fold"].items())
    werke = ", ".join(
        f"({_lit(w)}, {_lit(v['regierungsbezirk'])}, {_lit(v['nuts2'])})" for w, v in lex["werke"].items()
    )
    return [
        f"CREATE OR REPLACE TEMP TABLE zj_forms AS SELECT * FROM (VALUES {forms}) v(form, name)",
        f"CREATE OR REPLACE TEMP TABLE zj_werke AS SELECT * FROM (VALUES {werke}) v(werk, regierungsbezirk, nuts2)",
    ]


INPUT_COLUMNS = (
    "meldung_id", "werk", "anlage", "equipment_nr", "meldungsdatum", "schicht",
    "prioritaet", "meldungstext", "stillstand", "kosten", "status",
)  # fmt: skip


def pipeline(input_csv: str, output: str) -> list[str]:
    cols = ", ".join(f"coalesce({c}, '') AS {c}" for c in INPUT_COLUMNS)  # empty field → '' (spec)
    return [
        f"""CREATE OR REPLACE TEMP TABLE zj_in AS SELECT {cols} FROM read_csv({_lit(input_csv)}, delim=';', quote='"',
            escape='"', header=true, all_varchar=true, encoding='windows-1252')""",
        """CREATE OR REPLACE TEMP TABLE zj_t AS
           WITH n AS (
             SELECT zj_norm(meldung_id) AS meldung_id, zj_norm(werk) AS werk, zj_norm(anlage) AS anlage,
                    zj_equipment(zj_norm(equipment_nr)) AS equipment_nr,
                    zj_date(zj_norm(meldungsdatum)) AS datum,
                    zj_norm(schicht) AS schicht, zj_prio(zj_norm(prioritaet)) AS prioritaet,
                    zj_minutes(zj_norm(stillstand)) AS stillstand_min, zj_eur(zj_norm(kosten)) AS kosten_eur,
                    zj_norm(status) AS status, zj_expand(zj_norm(meldungstext)) AS text_norm
             FROM zj_in)
           SELECT *, lower(text_norm) AS text_lower, zj_fold(lower(text_norm)) AS text_fold FROM n""",
        """CREATE OR REPLACE TEMP TABLE zj_k AS
           SELECT t.meldung_id,
                  arg_max(f.name, length(f.form) * 1000000 - instr(t.text_fold, f.form)) AS komponente_rule
           FROM zj_t t JOIN zj_forms f ON contains(t.text_fold, f.form)
           GROUP BY t.meldung_id""",
        f"""COPY (
             SELECT t.meldung_id, t.werk, t.anlage, t.equipment_nr, t.datum, t.schicht, t.prioritaet,
                    t.stillstand_min, t.kosten_eur, t.status, t.text_norm, t.text_lower, t.text_fold,
                    zj_matnr(t.text_norm) AS materialnr, zj_safety(t.text_fold) AS sicherheitsrelevant_rule,
                    k.komponente_rule, w.regierungsbezirk, w.nuts2,
                    concat_ws(' | ',
                      CASE WHEN t.werk <> '' THEN 'Werk: ' || t.werk END,
                      CASE WHEN t.anlage <> '' THEN 'Anlage: ' || t.anlage END,
                      CASE WHEN t.equipment_nr <> '' THEN 'Equipment: ' || t.equipment_nr END,
                      CASE WHEN t.datum IS NOT NULL THEN 'Datum: ' || strftime(t.datum, '%Y-%m-%d') END,
                      CASE WHEN t.prioritaet IS NOT NULL THEN 'Priorität: ' || t.prioritaet END,
                      CASE WHEN t.text_norm <> '' THEN 'Meldung: ' || t.text_norm END) AS doc_text
             FROM zj_t t LEFT JOIN zj_k k USING (meldung_id) LEFT JOIN zj_werke w USING (werk)
             ORDER BY t.meldung_id
           ) TO {_lit(output)} (FORMAT parquet, COMPRESSION zstd)""",
    ]


def script(lex: dict[str, Any], input_csv: str, output: str, threads: int = 0) -> str:
    """The complete SQL as one script (a20_duckdb_cli runs exactly this through the duckdb CLI)."""
    head = [f"SET threads = {threads}"] if threads > 0 else []
    return (
        ";\n".join(["LOAD encodings", *head, *macros(lex), *tables(lex), *pipeline(input_csv, output)])
        + ";\n"
    )


def lexicon_json(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return dict(json.load(f))
