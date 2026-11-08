//! Shared Track A logic for the Rust contestants (`spec/track_a.md` v1.0).
//!
//! - [`Rules`]: the text rules (§1 normalisation, §6 abbreviations/lower/fold, §6 safety, §7 component)
//! - [`fields`]: two implementations of the field parsers (§2–5 + material number):
//!   [`fields::Regex`] (regex crate, a16) and [`fields::Hand`] (hand-written, a17)
//! - [`out::Builder`]: columnar output with the exact schema; [`out::write_parallel`] encodes row groups
//!   in parallel; [`process`] turns one input record into one output row
//!
//! Every function is checked against `spec/track_a_cases.jsonl` (tests/golden.rs).

pub mod args;
pub mod fields;
pub mod out;

use aho_corasick::{AhoCorasick, MatchKind};
use serde::Deserialize;
use std::collections::HashMap;
use std::path::Path;
use unicode_normalization::{UnicodeNormalization, is_nfc_quick};

/// Input columns in contract order (header names of the CSV).
pub const INPUT_COLUMNS: [&str; 11] = [
    "meldung_id",
    "werk",
    "anlage",
    "equipment_nr",
    "meldungsdatum",
    "schicht",
    "prioritaet",
    "meldungstext",
    "stillstand",
    "kosten",
    "status",
];

#[derive(Deserialize, Clone, Debug)]
pub struct Werk {
    pub regierungsbezirk: String,
    pub nuts2: String,
}

#[derive(Deserialize, Debug)]
pub struct Lexicon {
    pub mojibake: HashMap<String, String>,
    pub whitespace: String,
    pub strip_leading: String,
    pub strip_trailing: String,
    pub fold: HashMap<String, String>,
    pub abbreviations: HashMap<String, String>,
    pub safety_triggers_fold: Vec<String>,
    pub komponenten_fold: HashMap<String, String>,
    pub werke: HashMap<String, Werk>,
}

impl Lexicon {
    /// # Errors
    /// I/O or JSON errors.
    pub fn load(path: &Path) -> Result<Self, Box<dyn std::error::Error + Send + Sync>> {
        Ok(serde_json::from_slice(&std::fs::read(path)?)?)
    }
}

/// Text rules bound to one compiled lexicon.
pub struct Rules {
    moji: Vec<(String, String)>, // longest key first, applied sequentially like the reference
    moji_first: Vec<char>,       // fast path: skip unless one of these characters occurs
    ws: Vec<char>,
    lead: Vec<char>,
    trail: Vec<char>,
    abbr: HashMap<String, String>,
    abbr_max_chars: usize,
    fold: HashMap<char, String>,
    triggers: AhoCorasick,
    forms: AhoCorasick,
    form_names: Vec<String>, // by pattern id
    pub werke: HashMap<String, Werk>,
}

impl Rules {
    /// # Panics
    /// If the lexicon cannot be compiled (empty patterns, multi-char fold keys).
    #[must_use]
    pub fn new(lex: Lexicon) -> Self {
        let mut moji: Vec<(String, String)> = lex.mojibake.into_iter().collect();
        moji.sort_by(|a, b| {
            b.0.chars()
                .count()
                .cmp(&a.0.chars().count())
                .then(a.0.cmp(&b.0))
        });
        let mut moji_first: Vec<char> = moji.iter().filter_map(|(k, _)| k.chars().next()).collect();
        moji_first.dedup();
        let fold = lex
            .fold
            .into_iter()
            .map(|(k, v)| {
                let mut c = k.chars();
                let ch = c.next().expect("empty fold key");
                assert!(c.next().is_none(), "fold keys must be single characters");
                (ch, v)
            })
            .collect();
        let triggers = AhoCorasick::new(&lex.safety_triggers_fold).expect("safety triggers");
        let (forms, form_names): (Vec<String>, Vec<String>) =
            lex.komponenten_fold.into_iter().unzip();
        let forms = AhoCorasick::builder()
            .match_kind(MatchKind::Standard) // required for overlapping search
            .build(&forms)
            .expect("component forms");
        Self {
            moji,
            moji_first,
            ws: lex.whitespace.chars().collect(),
            lead: lex.strip_leading.chars().collect(),
            trail: lex.strip_trailing.chars().collect(),
            abbr_max_chars: lex
                .abbreviations
                .keys()
                .map(|k| k.chars().count())
                .max()
                .unwrap_or(0),
            abbr: lex.abbreviations,
            fold,
            triggers,
            forms,
            form_names,
            werke: lex.werke,
        }
    }

    /// §1: mojibake repair → NFC → whitespace runs to one space → strip spaces.
    #[must_use]
    pub fn norm(&self, s: &str) -> String {
        let mut cur: std::borrow::Cow<'_, str> = std::borrow::Cow::Borrowed(s);
        if cur.chars().any(|c| self.moji_first.contains(&c)) {
            for (bad, good) in &self.moji {
                if cur.contains(bad.as_str()) {
                    cur = std::borrow::Cow::Owned(cur.replace(bad.as_str(), good));
                }
            }
        }
        if !cur.is_ascii() && is_nfc_quick(cur.chars()) != unicode_normalization::IsNormalized::Yes
        {
            cur = std::borrow::Cow::Owned(cur.nfc().collect());
        }
        let mut out = String::with_capacity(cur.len());
        let mut pending = false;
        for c in cur.chars() {
            if self.ws.contains(&c) {
                pending = true;
            } else {
                if pending && !out.is_empty() {
                    out.push(' ');
                }
                pending = false;
                out.push(c);
            }
        }
        out
    }

    /// §6: replace abbreviation tokens (split on " ", strip leading/trailing punctuation, lower-case lookup).
    #[must_use]
    pub fn expand(&self, text: &str) -> String {
        let mut out = String::with_capacity(text.len() + 32);
        for (k, tok) in text.split(' ').enumerate() {
            if k > 0 {
                out.push(' ');
            }
            let core_start = tok
                .char_indices()
                .find(|(_, c)| !self.lead.contains(c))
                .map_or(tok.len(), |(i, _)| i);
            let rest = &tok[core_start..];
            let core_end = rest
                .char_indices()
                .rev()
                .find(|(_, c)| !self.trail.contains(c))
                .map_or(0, |(i, c)| i + c.len_utf8());
            let core = &rest[..core_end];
            // lower-casing never reduces the number of characters → longer cores cannot match
            let rep = if !core.is_empty() && core.chars().count() <= self.abbr_max_chars {
                self.abbr.get(&core.to_lowercase())
            } else {
                None
            };
            match rep {
                Some(r) => {
                    out.push_str(&tok[..core_start]);
                    out.push_str(r);
                    out.push_str(&rest[core_end..]);
                }
                None => out.push_str(tok),
            }
        }
        out
    }

    /// §6: ä→ae, ö→oe, ü→ue, ß→ss on the lower-cased text.
    #[must_use]
    pub fn fold(&self, lower: &str) -> String {
        if lower.is_ascii() {
            return lower.to_owned();
        }
        let mut out = String::with_capacity(lower.len() + 8);
        for c in lower.chars() {
            match self.fold.get(&c) {
                Some(r) => out.push_str(r),
                None => out.push(c),
            }
        }
        out
    }

    /// §6: any safety trigger is a substring of the folded text.
    #[must_use]
    pub fn safety(&self, text_fold: &str) -> bool {
        self.triggers.is_match(text_fold)
    }

    /// §7: longest dictionary form contained in the folded text; ties → earliest position.
    #[must_use]
    pub fn komponente(&self, text_fold: &str) -> Option<&str> {
        let mut best: Option<(usize, usize, usize)> = None; // (len, start, pattern)
        for m in self.forms.find_overlapping_iter(text_fold) {
            let len = text_fold[m.start()..m.end()].chars().count();
            let better = match best {
                None => true,
                Some((bl, bs, _)) => len > bl || (len == bl && m.start() < bs),
            };
            if better {
                best = Some((len, m.start(), m.pattern().as_usize()));
            }
        }
        best.map(|(_, _, p)| self.form_names[p].as_str())
    }
}

/// A calendar date (validated).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Date {
    pub y: i32,
    pub m: u32,
    pub d: u32,
}

impl Date {
    /// Valid Gregorian date with year 1–9999 (Python `datetime.date` range), else `None`.
    #[must_use]
    pub fn new(y: i32, m: u32, d: u32) -> Option<Self> {
        let leap = (y % 4 == 0 && y % 100 != 0) || y % 400 == 0;
        let dim = match m {
            1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
            4 | 6 | 9 | 11 => 30,
            2 if leap => 29,
            2 => 28,
            _ => return None,
        };
        ((1..=9999).contains(&y) && (1..=dim).contains(&d)).then_some(Self { y, m, d })
    }

    /// Days since 1970-01-01 (Arrow date32), H. Hinnant's `days_from_civil`.
    #[must_use]
    #[allow(clippy::cast_possible_wrap, clippy::cast_possible_truncation)]
    pub fn days(self) -> i32 {
        let y = if self.m <= 2 { self.y - 1 } else { self.y };
        let era = y.div_euclid(400);
        let yoe = y - era * 400;
        let m = self.m as i32;
        let doy = (153 * (if m > 2 { m - 3 } else { m + 9 }) + 2) / 5 + self.d as i32 - 1;
        let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
        era * 146_097 + doe - 719_468
    }
}

impl std::fmt::Display for Date {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{:04}-{:02}-{:02}", self.y, self.m, self.d)
    }
}

/// Field parsers (§2–5 + material number). Inputs are already normalised (§1).
pub trait Fields: Sync {
    fn equipment(&self, s: &str) -> Option<String>;
    fn date(&self, s: &str) -> Option<Date>;
    fn prio(&self, s: &str) -> Option<i8>;
    fn eur(&self, s: &str) -> Option<f64>;
    fn minutes(&self, s: &str) -> Option<i32>;
    /// First 8-digit run starting with "10" in the normalised text, bounded by non-digits.
    fn materialnr<'a>(&self, text_norm: &'a str) -> Option<&'a str>;
}

/// `doc_text` (spec §8): `label: value` for present, non-empty parts, joined with " | ".
#[must_use]
pub fn doc_text(
    werk: &str,
    anlage: &str,
    equipment: Option<&str>,
    datum: Option<Date>,
    prio: Option<i8>,
    text_norm: &str,
) -> String {
    use std::fmt::Write as _;
    let mut out = String::with_capacity(text_norm.len() + 96);
    let mut part = |label: &str, v: &dyn std::fmt::Display, empty: bool| {
        if !empty {
            if !out.is_empty() {
                out.push_str(" | ");
            }
            let _ = write!(out, "{label}: {v}");
        }
    };
    part("Werk", &werk, werk.is_empty());
    part("Anlage", &anlage, anlage.is_empty());
    part(
        "Equipment",
        &equipment.unwrap_or(""),
        equipment.is_none_or(str::is_empty),
    );
    match datum {
        Some(d) => part("Datum", &d, false),
        None => part("Datum", &"", true),
    }
    match prio {
        Some(p) => part("Priorität", &p, false),
        None => part("Priorität", &"", true),
    }
    part("Meldung", &text_norm, text_norm.is_empty());
    out
}

/// One input record (fields in [`INPUT_COLUMNS`] order) → one output row in `b`.
pub fn process<F: Fields>(rules: &Rules, f: &F, r: &[&str; 11], b: &mut out::Builder) {
    let text_norm = rules.expand(&rules.norm(r[7]));
    let text_lower = text_norm.to_lowercase();
    let text_fold = rules.fold(&text_lower);
    let werk = rules.norm(r[1]);
    let anlage = rules.norm(r[2]);
    let equipment = f.equipment(&rules.norm(r[3]));
    let datum = f.date(&rules.norm(r[4]));
    let prio = f.prio(&rules.norm(r[6]));
    let region = rules.werke.get(&werk);
    let doc = doc_text(
        &werk,
        &anlage,
        equipment.as_deref(),
        datum,
        prio,
        &text_norm,
    );
    b.push(&out::Row {
        meldung_id: &rules.norm(r[0]),
        werk: &werk,
        anlage: &anlage,
        equipment_nr: equipment.as_deref(),
        datum,
        schicht: &rules.norm(r[5]),
        prioritaet: prio,
        stillstand_min: f.minutes(&rules.norm(r[8])),
        kosten_eur: f.eur(&rules.norm(r[9])),
        status: &rules.norm(r[10]),
        text_norm: &text_norm,
        text_lower: &text_lower,
        text_fold: &text_fold,
        materialnr: f.materialnr(&text_norm),
        sicherheitsrelevant_rule: rules.safety(&text_fold),
        komponente_rule: rules.komponente(&text_fold),
        regierungsbezirk: region.map(|w| w.regierungsbezirk.as_str()),
        nuts2: region.map(|w| w.nuts2.as_str()),
        doc_text: &doc,
    });
}
