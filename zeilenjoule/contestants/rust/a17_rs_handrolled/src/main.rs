//! Track A `a.full` contestant `a17_rs_handrolled` (pure Rust, maximum effort, PLAN §8).
//!
//! mmap the cp1252 CSV → quote-aware chunk boundaries (memchr) → per chunk in parallel (rayon):
//! split records, decode cp1252 by hand, apply the shared rules with the hand-written field parsers,
//! build an Arrow batch and encode it into a compressed Parquet row group → append row groups in order.
//! The output must be sorted by `meldung_id`: the input already is, which is verified across all chunks;
//! otherwise a full sort fallback runs (never silently unsorted).

mod csv;

use arrow_array::{Array, RecordBatch};
use rayon::prelude::*;
use serde_json::{Map, json};
use std::borrow::Cow;
use std::fs::File;
use std::process::ExitCode;
use zj_track_a::fields::Hand;
use zj_track_a::out::{self, Builder, EncodeCtx, ParallelWriter};
use zj_track_a::{INPUT_COLUMNS, Lexicon, Rules, args, process};

type Error = Box<dyn std::error::Error + Send + Sync>;

/// jemalloc, like the Python Polars runtime: glibc malloc is slow for multithreaded string work (ADR 0010).
#[global_allocator]
static GLOBAL: tikv_jemallocator::Jemalloc = tikv_jemallocator::Jemalloc;

/// Column positions of the 11 input columns in the file header.
fn header_map(header: &[u8]) -> Result<[usize; 11], Error> {
    let mut fields = Vec::new();
    csv::split_fields(header, &mut fields);
    let names: Vec<String> = fields
        .iter()
        .map(|f| csv::decode(f).map(Cow::into_owned))
        .collect::<Result<_, _>>()?;
    let mut map = [0usize; 11];
    for (slot, col) in map.iter_mut().zip(INPUT_COLUMNS) {
        *slot = names
            .iter()
            .position(|n| n == col)
            .ok_or_else(|| format!("input lacks column {col}"))?;
    }
    Ok(map)
}

/// Parse + transform one chunk into an output batch.
fn chunk_batch(rules: &Rules, buf: &[u8], cols: &[usize; 11]) -> Result<RecordBatch, Error> {
    let mut b = Builder::with_capacity(buf.len() / 170 + 16);
    let mut raw = Vec::with_capacity(16);
    let mut err: Option<Error> = None;
    csv::for_each_line(buf, |line| {
        if err.is_some() {
            return;
        }
        csv::split_fields(line, &mut raw);
        let need = cols.iter().max().copied().unwrap_or(0);
        if raw.len() <= need {
            err = Some(format!("record has {} fields, expected > {need}", raw.len()).into());
            return;
        }
        let mut dec: [Cow<'_, str>; 11] = Default::default();
        for (d, &c) in dec.iter_mut().zip(cols) {
            match csv::decode(&raw[c]) {
                Ok(s) => *d = s,
                Err(e) => {
                    err = Some(e.to_string().into());
                    return;
                }
            }
        }
        let rec: [&str; 11] = std::array::from_fn(|i| dec[i].as_ref());
        process(rules, &Hand, &rec, &mut b);
    });
    if let Some(e) = err {
        return Err(e);
    }
    Ok(b.finish()?)
}

struct Encoded {
    chunks: Vec<parquet::arrow::arrow_writer::ArrowColumnChunk>,
    rows: usize,
    first: Option<String>,
    last: Option<String>,
    sorted: bool,
}

fn encode_chunk(
    rules: &Rules,
    ctx: &EncodeCtx,
    buf: &[u8],
    cols: &[usize; 11],
    idx: usize,
) -> Result<Encoded, Error> {
    let batch = chunk_batch(rules, buf, cols)?;
    let ids = out::ids(&batch);
    let sorted = out::strictly_sorted(std::slice::from_ref(&batch));
    let first = (!ids.is_empty()).then(|| ids.value(0).to_owned());
    let last = (!ids.is_empty()).then(|| ids.value(ids.len() - 1).to_owned());
    Ok(Encoded {
        chunks: ctx.encode(&batch, idx)?,
        rows: batch.num_rows(),
        first,
        last,
        sorted,
    })
}

fn run(a: &args::Args) -> Result<(usize, Map<String, serde_json::Value>), Error> {
    if a.threads > 0 {
        rayon::ThreadPoolBuilder::new()
            .num_threads(a.threads)
            .build_global()?;
    }
    let rules = Rules::new(Lexicon::load(&a.lexicon)?);
    let (mmap, cols, bounds) = {
        let _s = zjmark::Stage::new("map")?;
        let file = File::open(&a.input)?;
        // SAFETY: the input file is not modified while the benchmark runs (harness contract).
        let mmap = unsafe { memmap2::Mmap::map(&file)? };
        let _ = mmap.advise(memmap2::Advice::Sequential);
        let hdr_end = memchr::memchr(b'\n', &mmap).map_or(mmap.len(), |i| i + 1);
        let header = mmap[..hdr_end]
            .strip_suffix(b"\n")
            .unwrap_or(&mmap[..hdr_end]);
        let cols = header_map(header.strip_suffix(b"\r").unwrap_or(header))?;
        let threads = rayon::current_num_threads();
        let target = (mmap.len() / (32 << 20)).max(threads * 4);
        let bounds = csv::chunk_bounds(&mmap, hdr_end, target);
        (mmap, cols, bounds)
    };
    let mut writer = ParallelWriter::create(&a.output)?;
    let ctx = writer.ctx();
    let encoded: Vec<Encoded> = {
        let _s = zjmark::Stage::new("process")?;
        bounds
            .par_iter()
            .enumerate()
            .map(|(i, &(s, e))| encode_chunk(&rules, &ctx, &mmap[s..e], &cols, i))
            .collect::<Result<_, _>>()?
    };
    let globally_sorted = encoded.iter().all(|e| e.sorted)
        && encoded.windows(2).all(|w| match (&w[0].last, &w[1].first) {
            (Some(l), Some(f)) => f > l,
            _ => true,
        });
    let rows: usize = encoded.iter().map(|e| e.rows).sum();
    {
        let _s = zjmark::Stage::new("write")?;
        if globally_sorted {
            for e in encoded {
                writer.append(e.chunks)?;
            }
        } else {
            drop(encoded);
            sort_fallback(&rules, &ctx, &mut writer, &mmap, &cols, &bounds)?;
        }
        writer.close()?;
    }
    let mut extra = Map::new();
    extra.insert("threads".into(), json!(rayon::current_num_threads()));
    extra.insert("chunks".into(), json!(bounds.len()));
    extra.insert("input_sorted".into(), json!(globally_sorted));
    extra.insert(
        "parquet".into(),
        json!("arrow-rs 60.1.0, zstd(1), parallel row-group encoding"),
    );
    Ok((rows, extra))
}

/// Unsorted input: materialise all batches, sort by `meldung_id`, re-encode in parallel slices.
fn sort_fallback(
    rules: &Rules,
    ctx: &EncodeCtx,
    writer: &mut ParallelWriter,
    mmap: &[u8],
    cols: &[usize; 11],
    bounds: &[(usize, usize)],
) -> Result<(), Error> {
    let batches: Vec<RecordBatch> = bounds
        .par_iter()
        .map(|&(s, e)| chunk_batch(rules, &mmap[s..e], cols))
        .collect::<Result<_, _>>()?;
    let all = arrow_select::concat::concat_batches(&out::SCHEMA, &batches)?;
    drop(batches);
    let idx = arrow_ord::sort::sort_to_indices(all.column(0), None, None)?;
    let sorted = arrow_select::take::take_record_batch(&all, &idx)?;
    drop(all);
    let n = sorted.num_rows();
    let step = n.div_ceil(rayon::current_num_threads() * 4).max(1);
    let parts: Vec<_> = (0..n)
        .step_by(step)
        .collect::<Vec<_>>()
        .par_iter()
        .enumerate()
        .map(|(i, &off)| ctx.encode(&sorted.slice(off, step.min(n - off)), i))
        .collect::<Result<_, _>>()?;
    for p in parts {
        writer.append(p)?;
    }
    Ok(())
}

fn main() -> ExitCode {
    let a = match args::parse(std::env::args().skip(1)) {
        Ok(a) => a,
        Err(e) => {
            eprintln!("a17_rs_handrolled: {e}");
            return ExitCode::from(2);
        }
    };
    match run(&a) {
        Ok((rows, extra)) => {
            zjmark::result(
                rows as u64,
                "pure Rust: mmap + memchr + rayon + hand parsers",
                extra,
            );
            ExitCode::SUCCESS
        }
        Err(e) => {
            eprintln!("a17_rs_handrolled: {e}");
            ExitCode::FAILURE
        }
    }
}
