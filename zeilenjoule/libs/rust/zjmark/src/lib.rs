//! Stage markers (stderr) and result line (stdout), byte-identical to the Python and Go helpers.
//! See `spec/zjmark.md`.
//!
//! ```no_run
//! let _parse = zjmark::Stage::new("parse").unwrap(); // `end` marker is emitted on drop
//! zjmark::result(42, "", serde_json::Map::new());
//! ```

use serde_json::{Map, Value, json};
use std::fmt;

/// Stage name violates `^[a-z][a-z0-9_]{0,31}$`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct InvalidStage(pub String);

impl fmt::Display for InvalidStage {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "invalid stage name {:?}: must match ^[a-z][a-z0-9_]{{0,31}}$",
            self.0
        )
    }
}

impl std::error::Error for InvalidStage {}

fn check_stage(name: &str) -> Result<(), InvalidStage> {
    let mut chars = name.chars();
    let ok = name.len() <= 32
        && chars.next().is_some_and(|c| c.is_ascii_lowercase())
        && chars.all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_');
    if ok {
        Ok(())
    } else {
        Err(InvalidStage(name.to_owned()))
    }
}

/// Canonical JSON: compact, keys sorted (`serde_json::Map` is a `BTreeMap` by default),
/// UTF-8 without `\u` or HTML escaping.
#[must_use]
pub fn canonical_json(v: &Value) -> String {
    v.to_string()
}

/// `ZJMARK {"ev":"start","stage":…}`
///
/// # Errors
/// Returns [`InvalidStage`] if `name` violates the stage-name pattern.
pub fn start_line(name: &str) -> Result<String, InvalidStage> {
    check_stage(name)?;
    Ok(format!(
        "ZJMARK {}",
        canonical_json(&json!({"ev": "start", "stage": name}))
    ))
}

/// `ZJMARK {"ev":"end","stage":…}`
///
/// # Errors
/// Returns [`InvalidStage`] if `name` violates the stage-name pattern.
pub fn end_line(name: &str) -> Result<String, InvalidStage> {
    check_stage(name)?;
    Ok(format!(
        "ZJMARK {}",
        canonical_json(&json!({"ev": "end", "stage": name}))
    ))
}

/// `ZJRESULT {"extra":…,"notes":…,"rows":…}`
#[must_use]
pub fn result_line(rows: u64, notes: &str, extra: Map<String, Value>) -> String {
    let v = json!({"extra": Value::Object(extra), "notes": notes, "rows": rows});
    format!("ZJRESULT {}", canonical_json(&v))
}

/// RAII stage guard: prints the start marker on creation and the end marker on drop
/// (also during unwinding). `eprintln!` writes to unbuffered stderr.
#[must_use = "the stage ends when the guard is dropped"]
pub struct Stage {
    end: String,
}

impl Stage {
    /// # Errors
    /// Returns [`InvalidStage`] if `name` violates the stage-name pattern.
    pub fn new(name: &str) -> Result<Self, InvalidStage> {
        let end = end_line(name)?;
        eprintln!("{}", start_line(name)?);
        Ok(Self { end })
    }
}

impl Drop for Stage {
    fn drop(&mut self) {
        eprintln!("{}", self.end);
    }
}

/// Print the single final ZJRESULT line on stdout.
pub fn result(rows: u64, notes: &str, extra: Map<String, Value>) {
    println!("{}", result_line(rows, notes, extra));
}

#[cfg(test)]
mod tests {
    use super::*;

    const GOLDEN: &str = include_str!("../../../../spec/zjmark_golden.txt");

    #[test]
    fn golden_sequence_matches_spec() {
        let mut extra = Map::new();
        extra.insert("threads".into(), json!(16));
        extra.insert("engine".into(), json!("cpu"));
        extra.insert("fallback_ops".into(), json!(["Series.apply"]));
        let lines = [
            start_line("parse").unwrap(),
            end_line("parse").unwrap(),
            start_line("embed").unwrap(),
            end_line("embed").unwrap(),
            result_line(3, "Prüfung <ok> & fertig", extra),
        ];
        assert_eq!(lines.join("\n") + "\n", GOLDEN);
    }

    #[test]
    fn invalid_stage_names_rejected() {
        let long = "a".repeat(33);
        for bad in ["", "Parse", "1parse", "pa rse", "größe", long.as_str()] {
            assert_eq!(
                start_line(bad),
                Err(InvalidStage(bad.to_owned())),
                "{bad:?}"
            );
        }
        assert!(start_line(&"a".repeat(32)).is_ok());
    }
}
