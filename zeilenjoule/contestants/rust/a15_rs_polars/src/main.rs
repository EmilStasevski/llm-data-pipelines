//! Track A `a.full` contestant `a15_rs_polars`: the Rust `polars` crate with the lazy API.
//!
//! The plan is a 1:1 port of the Python expressions in `zj_df/track_a/polars_impl.py` (a10), so
//! a10 vs a15 isolates "Python front-end vs Rust front-end" over the same engine family. Polars' CSV
//! reader is UTF-8 only, so `encoding_rs` decodes cp1252 in memory first (a10 transcodes to a file).
//! Engine: streaming, like Python Polars 2.x `collect()` with the default engine="auto".

use polars::prelude::*;
use serde_json::{Map, json};
use std::fs::File;
use std::io::Cursor;
use std::process::ExitCode;
use zj_track_a::{Lexicon, args};

type Error = Box<dyn std::error::Error + Send + Sync>;

/// jemalloc, like the Python Polars runtime: glibc malloc is slow for multithreaded string work (ADR 0010).
#[global_allocator]
static GLOBAL: tikv_jemallocator::Jemalloc = tikv_jemallocator::Jemalloc;

const WS_RUN: &str = r"[\t\n\x0B\x0C\r \x{A0}]+";
const RE_EQUIPMENT: &str = r"^([A-Z])[ -]?([0-9]{4})(-[A-Z])?$";
const RE_DATE_DE: &str = r"^([0-9]{1,2})\.([0-9]{1,2})\.([0-9]{2}|[0-9]{4})$";
const RE_DATE_ISO: &str = r"^([0-9]{4})-([0-9]{2})-([0-9]{2})$";
const RE_NUMBER: &str = r"^(-)?([0-9]{1,3}(?:[. ][0-9]{3})+|[0-9]+)(?:,([0-9]+))?(?: ?(?:€|EUR))?$";
const RE_DURATION: &str = r"(?i)^(?:ca\. ?)?(?:([0-9]+(?:,[0-9]+)?) ?- ?)?([0-9]+(?:,[0-9]+)?) ?(h|std\.|stunde|stunden|min|minuten)$";
const RE_MATNR: &str = r"(?:^|[^0-9])(10[0-9]{6})(?:[^0-9]|$)";
const HOURS: [&str; 4] = ["h", "std.", "stunde", "stunden"];
const OUT_COLUMNS: [&str; 19] = [
    "meldung_id",
    "werk",
    "anlage",
    "equipment_nr",
    "datum",
    "schicht",
    "prioritaet",
    "stillstand_min",
    "kosten_eur",
    "status",
    "text_norm",
    "text_lower",
    "text_fold",
    "materialnr",
    "sicherheitsrelevant_rule",
    "komponente_rule",
    "regierungsbezirk",
    "nuts2",
    "doc_text",
];

fn strs<S: AsRef<str>>(name: &str, v: &[S]) -> Expr {
    let v: Vec<&str> = v.iter().map(AsRef::as_ref).collect();
    lit(Series::new(name.into(), v))
}

/// A list literal (one list value) for pattern arguments of `replace_many`/`extract_many`/`contains_any`/`is_in`.
fn list_of<S: AsRef<str>>(name: &str, v: &[S]) -> Expr {
    strs(name, v).implode(true) // keep order: patterns and replacements are aligned by position
}

fn g(s: &Expr, i: usize) -> Expr {
    s.clone().struct_().field_by_name(&i.to_string())
}

fn null_str() -> Expr {
    lit(NULL).cast(DataType::String)
}

fn escape_class(chars: &str) -> String {
    chars
        .chars()
        .map(|c| {
            if "[]^-\\".contains(c) {
                format!("\\{c}")
            } else {
                c.to_string()
            }
        })
        .collect()
}

struct PolarsRules {
    moji_keys: Vec<String>,
    moji_vals: Vec<String>,
    abbr_keys: Vec<String>,
    abbr_vals: Vec<String>,
    token_re: String,
    fold_keys: Vec<String>,
    fold_vals: Vec<String>,
    triggers: Vec<String>,
    forms: Vec<String>,
    form_names: Vec<String>,
    werke: Vec<String>,
    rb: Vec<String>,
    nuts: Vec<String>,
}

impl PolarsRules {
    fn new(lex: Lexicon) -> Self {
        let mut moji: Vec<(String, String)> = lex.mojibake.into_iter().collect();
        moji.sort_by(|a, b| {
            b.0.chars()
                .count()
                .cmp(&a.0.chars().count())
                .then(a.0.cmp(&b.0))
        });
        let (abbr_keys, abbr_vals) = lex.abbreviations.into_iter().unzip();
        let (fold_keys, fold_vals) = lex.fold.into_iter().unzip();
        let (forms, form_names) = lex.komponenten_fold.into_iter().unzip();
        let mut werke: Vec<(String, zj_track_a::Werk)> = lex.werke.into_iter().collect();
        werke.sort_by(|a, b| a.0.cmp(&b.0));
        Self {
            moji_keys: moji.iter().map(|m| m.0.clone()).collect(),
            moji_vals: moji.iter().map(|m| m.1.clone()).collect(),
            abbr_keys,
            abbr_vals,
            token_re: format!(
                "^([{}]*)(.*?)([{}]*)$",
                escape_class(&lex.strip_leading),
                escape_class(&lex.strip_trailing)
            ),
            fold_keys,
            fold_vals,
            triggers: lex.safety_triggers_fold,
            forms,
            form_names,
            rb: werke.iter().map(|w| w.1.regierungsbezirk.clone()).collect(),
            nuts: werke.iter().map(|w| w.1.nuts2.clone()).collect(),
            werke: werke.into_iter().map(|w| w.0).collect(),
        }
    }

    // §1
    fn norm(&self, e: Expr) -> Expr {
        e.str()
            .replace_many(
                list_of("k", &self.moji_keys),
                list_of("v", &self.moji_vals),
                false,
                false,
            )
            .str()
            .normalize(UnicodeForm::NFC)
            .str()
            .replace_all(lit(WS_RUN), lit(" "), false)
            .str()
            .strip_chars(lit(" "))
    }

    // §2–5
    fn equipment(e: Expr) -> Result<Expr, Error> {
        let m = e.str().extract_groups(RE_EQUIPMENT)?;
        Ok(concat_str(
            [g(&m, 1), lit("-"), g(&m, 2), g(&m, 3).fill_null(lit(""))],
            "",
            false,
        ))
    }

    fn date(e: Expr) -> Result<Expr, Error> {
        let de = e.clone().str().extract_groups(RE_DATE_DE)?;
        let iso = e.str().extract_groups(RE_DATE_ISO)?;
        let y = when(g(&de, 3).str().len_chars().eq(lit(2)))
            .then(lit("20") + g(&de, 3))
            .otherwise(g(&de, 3));
        let s_de = concat_str(
            [
                y,
                lit("-"),
                g(&de, 2).str().zfill(lit(2)),
                lit("-"),
                g(&de, 1).str().zfill(lit(2)),
            ],
            "",
            false,
        );
        let s_iso = concat_str(
            [g(&iso, 1), lit("-"), g(&iso, 2), lit("-"), g(&iso, 3)],
            "",
            false,
        );
        Ok(coalesce(&[s_de, s_iso]).str().to_date(StrptimeOptions {
            format: Some("%Y-%m-%d".into()),
            strict: false, // impossible dates → null
            exact: true,
            cache: true,
        }))
    }

    fn prio(e: Expr) -> Expr {
        let n = when(e.clone().str().contains(lit(r"^[0-9]+$"), true))
            .then(e.cast(DataType::Int64)) // non-strict like Python strict=False: overflow → null
            .otherwise(lit(NULL).cast(DataType::Int64));
        when(n.clone().is_between(lit(1), lit(4), ClosedInterval::Both))
            .then(n)
            .otherwise(lit(NULL).cast(DataType::Int64))
            .cast(DataType::Int8)
    }

    fn eur(e: Expr) -> Result<Expr, Error> {
        let m = e.str().extract_groups(RE_NUMBER)?;
        let s = concat_str(
            [
                g(&m, 1).fill_null(lit("")),
                g(&m, 2).str().replace_all(lit(r"[. ]"), lit(""), false),
                lit("."),
                g(&m, 3).fill_null(lit("0")),
            ],
            "",
            false,
        );
        Ok(s.cast(DataType::Float64))
    }

    fn minutes(e: Expr) -> Result<Expr, Error> {
        let m = e.str().extract_groups(RE_DURATION)?;
        let value = g(&m, 2);
        let unit = g(&m, 3).str().to_lowercase();
        let frac = value
            .clone()
            .str()
            .extract(lit(r",([0-9]+)$"), 1)
            .fill_null(lit(""));
        let num = concat_str(
            [value.str().extract(lit(r"^([0-9]+)"), 1), frac.clone()],
            "",
            false,
        )
        .cast(DataType::Int64);
        let den = lit(10i64).pow(frac.str().len_chars()).cast(DataType::Int64);
        let factor = when(unit.is_in(list_of("h", &HOURS), false))
            .then(lit(60i64))
            .otherwise(lit(1i64));
        let two = lit(2i64);
        Ok(((two.clone() * num * factor + den.clone()).floor_div(two * den)).cast(DataType::Int32))
    }

    // §6
    fn expand(&self, e: Expr) -> Result<Expr, Error> {
        let tok = element();
        let m = tok.clone().str().extract_groups(&self.token_re)?;
        let rep = g(&m, 2).str().to_lowercase().replace_strict(
            strs("k", &self.abbr_keys),
            strs("v", &self.abbr_vals),
            Some(null_str()),
            Some(DataType::String),
        );
        let new = when(rep.clone().is_not_null())
            .then(concat_str([g(&m, 1), rep, g(&m, 3)], "", false))
            .otherwise(tok);
        Ok(e.str()
            .split(lit(" "))
            .list()
            .eval(new)
            .list()
            .join(lit(" "), false))
    }

    // §7: longest dictionary match, ties → earliest position
    fn best_form(&self, lf: LazyFrame) -> LazyFrame {
        lf.select([
            col("meldung_id"),
            col("text_fold"),
            col("text_fold")
                .str()
                .extract_many(list_of("f", &self.forms), false, true, false)
                .alias("form"),
        ])
        .explode(
            cols(["form"]),
            ExplodeOptions {
                empty_as_null: false,
                keep_nulls: false,
            },
        )
        .with_columns([
            col("form").str().len_chars().alias("length"),
            col("text_fold")
                .str()
                .find_literal(col("form"))
                .alias("pos"),
        ])
        .group_by([col("meldung_id")])
        .agg([col("form")
            .sort_by(
                [col("length"), col("pos")],
                SortMultipleOptions::default().with_order_descending_multi([true, false]),
            )
            .first()])
        .select([
            col("meldung_id"),
            col("form")
                .replace_strict(
                    strs("f", &self.forms),
                    strs("n", &self.form_names),
                    None::<Expr>,
                    Some(DataType::String),
                )
                .alias("komponente_rule"),
        ])
    }

    fn transform(&self, lf: LazyFrame) -> Result<LazyFrame, Error> {
        let n = |c: &str| self.norm(col(c));
        let lf = lf
            .with_columns([
                n("meldung_id").alias("meldung_id"),
                n("werk").alias("werk"),
                n("anlage").alias("anlage"),
                Self::equipment(n("equipment_nr"))?.alias("equipment_nr"),
                Self::date(n("meldungsdatum"))?.alias("datum"),
                n("schicht").alias("schicht"),
                Self::prio(n("prioritaet")).alias("prioritaet"),
                Self::minutes(n("stillstand"))?.alias("stillstand_min"),
                Self::eur(n("kosten"))?.alias("kosten_eur"),
                n("status").alias("status"),
                self.expand(n("meldungstext"))?.alias("text_norm"),
            ])
            .with_columns([col("text_norm").str().to_lowercase().alias("text_lower")])
            .with_columns([col("text_lower")
                .str()
                .replace_many(
                    list_of("k", &self.fold_keys),
                    list_of("v", &self.fold_vals),
                    false,
                    false,
                )
                .alias("text_fold")])
            .with_columns([
                col("text_norm")
                    .str()
                    .extract(lit(RE_MATNR), 1)
                    .alias("materialnr"),
                col("text_fold")
                    .str()
                    .contains_any(list_of("t", &self.triggers), false)
                    .alias("sicherheitsrelevant_rule"),
                col("werk")
                    .replace_strict(
                        strs("w", &self.werke),
                        strs("r", &self.rb),
                        Some(null_str()),
                        Some(DataType::String),
                    )
                    .alias("regierungsbezirk"),
                col("werk")
                    .replace_strict(
                        strs("w", &self.werke),
                        strs("n", &self.nuts),
                        Some(null_str()),
                        Some(DataType::String),
                    )
                    .alias("nuts2"),
            ]);
        let lf = lf
            .clone()
            .left_join(self.best_form(lf), col("meldung_id"), col("meldung_id"));
        let part = |label: &str, v: Expr| {
            when(v.clone().is_not_null().and(v.clone().neq(lit(""))))
                .then(lit(format!("{label}: ")) + v)
                .otherwise(null_str())
        };
        let doc = concat_str(
            [
                part("Werk", col("werk")),
                part("Anlage", col("anlage")),
                part("Equipment", col("equipment_nr")),
                part("Datum", col("datum").dt().to_string("%Y-%m-%d")),
                part("Priorität", col("prioritaet").cast(DataType::String)),
                part("Meldung", col("text_norm")),
            ],
            " | ",
            true,
        )
        .fill_null(lit(""));
        Ok(lf
            .with_columns([doc.alias("doc_text")])
            .select(OUT_COLUMNS.iter().map(|c| col(*c)).collect::<Vec<_>>())
            .sort(["meldung_id"], SortMultipleOptions::default()))
    }
}

fn run(a: &args::Args) -> Result<(usize, Map<String, serde_json::Value>), Error> {
    if a.threads > 0 {
        // must be set before the Polars thread pool is first used
        unsafe { std::env::set_var("POLARS_MAX_THREADS", a.threads.to_string()) };
    }
    let rules = PolarsRules::new(Lexicon::load(&a.lexicon)?);
    let utf8 = {
        let _s = zjmark::Stage::new("decode")?;
        let raw = std::fs::read(&a.input)?;
        let (text, _, malformed) = encoding_rs::WINDOWS_1252.decode(&raw);
        if malformed {
            return Err("input is not valid windows-1252".into());
        }
        text.into_owned().into_bytes()
    };
    let df = {
        let _s = zjmark::Stage::new("read")?;
        CsvReadOptions::default()
            .with_has_header(true)
            .with_infer_schema_length(Some(0)) // all columns as strings, exactly as exported
            .map_parse_options(|p| {
                p.with_separator(b';')
                    .with_quote_char(Some(b'"'))
                    .with_missing_is_null(false)
            })
            .into_reader_with_file_handle(Cursor::new(utf8))
            .finish()?
    };
    let mut out = {
        let _s = zjmark::Stage::new("query")?;
        // Python Polars 2.x `collect()` (engine="auto", a10) runs the streaming engine; in the Rust crate
        // 0.55 `Auto` maps to the in-memory engine, so the like-for-like call names it explicitly.
        rules
            .transform(df.lazy())?
            .collect_with_engine(Engine::Streaming)?
            .unwrap_single()
    };
    {
        let _s = zjmark::Stage::new("write")?;
        ParquetWriter::new(File::create(&a.output)?)
            .with_compression(ParquetCompression::Zstd(None))
            .finish(&mut out)?;
    }
    let mut extra = Map::new();
    let threads = if a.threads > 0 {
        a.threads
    } else {
        std::thread::available_parallelism().map_or(1, usize::from)
    };
    extra.insert("threads".into(), json!(threads));
    extra.insert("polars_version".into(), json!(polars::VERSION));
    extra.insert("same_plan_as".into(), json!("a10_polars_lazy"));
    extra.insert("engine".into(), json!("streaming"));
    Ok((out.height(), extra))
}

fn main() -> ExitCode {
    let a = match args::parse(std::env::args().skip(1)) {
        Ok(a) => a,
        Err(e) => {
            eprintln!("a15_rs_polars: {e}");
            return ExitCode::from(2);
        }
    };
    match run(&a) {
        Ok((rows, extra)) => {
            zjmark::result(rows as u64, "Rust polars lazy", extra);
            ExitCode::SUCCESS
        }
        Err(e) => {
            eprintln!("a15_rs_polars: {e}");
            ExitCode::FAILURE
        }
    }
}
