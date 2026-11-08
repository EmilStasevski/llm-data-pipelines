//! `zj-smoke --output <path> [--rows N] [--sleep SECONDS] [--threads N]`
use std::{fmt::Write as _, fs, path::PathBuf, process::ExitCode, thread, time::Duration};

#[derive(Debug, PartialEq)]
struct Args {
    output: PathBuf,
    rows: u64,
    sleep_s: f64,
    threads: u32,
}

fn parse_args(mut it: impl Iterator<Item = String>) -> Result<Args, String> {
    let (mut output, mut rows, mut sleep_s, mut threads) = (None, 0, 0.0, 1);
    while let Some(flag) = it.next() {
        let mut val = || it.next().ok_or_else(|| format!("{flag} needs a value"));
        match flag.as_str() {
            "--output" => output = Some(PathBuf::from(val()?)),
            "--rows" => rows = val()?.parse().map_err(|e| format!("--rows: {e}"))?,
            "--sleep" => sleep_s = val()?.parse().map_err(|e| format!("--sleep: {e}"))?,
            "--threads" => threads = val()?.parse().map_err(|e| format!("--threads: {e}"))?,
            other => return Err(format!("unknown argument {other}")),
        }
    }
    if !(0.0..=86_400.0).contains(&sleep_s) {
        return Err("--sleep must be within 0..=86400".into());
    }
    let output = output.ok_or("--output is required")?;
    Ok(Args {
        output,
        rows,
        sleep_s,
        threads,
    })
}

fn run(a: &Args) -> std::io::Result<()> {
    {
        let _s = zjmark::Stage::new("work").expect("valid stage name");
        thread::sleep(Duration::from_secs_f64(a.sleep_s));
    }
    {
        let _s = zjmark::Stage::new("write").expect("valid stage name");
        if let Some(dir) = a.output.parent() {
            fs::create_dir_all(dir)?;
        }
        let body = (0..a.rows).fold(String::new(), |mut s, i| {
            let _ = writeln!(s, "{i}"); // writing to a String cannot fail
            s
        });
        fs::write(&a.output, body)?;
    }
    let mut extra = serde_json::Map::new();
    extra.insert("sleep_s".into(), a.sleep_s.into());
    extra.insert("threads".into(), a.threads.into());
    zjmark::result(a.rows, "smoke", extra);
    Ok(())
}

fn main() -> ExitCode {
    match parse_args(std::env::args().skip(1)) {
        Ok(a) => match run(&a) {
            Ok(()) => ExitCode::SUCCESS,
            Err(e) => {
                eprintln!("zj-smoke: {e}");
                ExitCode::FAILURE
            }
        },
        Err(e) => {
            eprintln!("zj-smoke: {e}");
            ExitCode::from(2)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn args(s: &str) -> Result<Args, String> {
        parse_args(s.split_whitespace().map(String::from))
    }

    #[test]
    fn parses_full_contract() {
        let a = args("--output /x/o.txt --rows 5 --sleep 0.5 --threads 16").unwrap();
        assert_eq!(
            a,
            Args {
                output: "/x/o.txt".into(),
                rows: 5,
                sleep_s: 0.5,
                threads: 16
            }
        );
    }

    #[test]
    fn rejects_bad_input() {
        assert!(args("--rows 5").unwrap_err().contains("--output"));
        assert!(args("--output o --rows -1").is_err());
        assert!(args("--output o --sleep -1").is_err());
        assert!(
            args("--output o --bogus 1")
                .unwrap_err()
                .contains("unknown")
        );
        assert!(args("--output").unwrap_err().contains("needs a value"));
    }
}
