//! CLI contract (PLAN §5.3): `--input X --output Y [--threads N] [--lexicon PATH]`.

use std::path::PathBuf;

#[derive(Debug, PartialEq, Eq)]
pub struct Args {
    pub input: PathBuf,
    pub output: PathBuf,
    /// 0 = all logical CPUs
    pub threads: usize,
    pub lexicon: PathBuf,
}

/// # Errors
/// Unknown flags, missing values or missing required flags.
pub fn parse(mut it: impl Iterator<Item = String>) -> Result<Args, String> {
    let (mut input, mut output, mut threads, mut lexicon) =
        (None, None, 0, PathBuf::from("spec/track_a_lexicon.json"));
    while let Some(flag) = it.next() {
        let mut val = || it.next().ok_or_else(|| format!("{flag} needs a value"));
        match flag.as_str() {
            "--input" => input = Some(PathBuf::from(val()?)),
            "--output" => output = Some(PathBuf::from(val()?)),
            "--threads" => threads = val()?.parse().map_err(|e| format!("--threads: {e}"))?,
            "--lexicon" => lexicon = PathBuf::from(val()?),
            other => return Err(format!("unknown argument {other}")),
        }
    }
    Ok(Args {
        input: input.ok_or("--input is required")?,
        output: output.ok_or("--output is required")?,
        threads,
        lexicon,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn v(a: &[&str]) -> impl Iterator<Item = String> {
        a.iter()
            .map(|s| (*s).to_owned())
            .collect::<Vec<_>>()
            .into_iter()
    }

    #[test]
    fn parses_contract() {
        let a = parse(v(&[
            "--input",
            "a.csv",
            "--output",
            "o.parquet",
            "--threads",
            "4",
        ]))
        .unwrap();
        assert_eq!(a.threads, 4);
        assert_eq!(a.lexicon, PathBuf::from("spec/track_a_lexicon.json"));
        assert!(parse(v(&["--output", "o"])).is_err());
        assert!(parse(v(&["--input", "a", "--output", "o", "--bogus"])).is_err());
    }
}
