//! Hand-written cp1252 CSV reading: decoder, record splitter, quote-aware chunk boundaries.
//!
//! Dialect = what the generator writes and Python's `csv` reads: `;` separator, `"` quoting with `""`
//! escapes, CRLF (LF accepted), strict cp1252 (the five undefined bytes are errors).

use std::borrow::Cow;

/// cp1252 0x80–0x9F → Unicode; 0 marks the five undefined bytes. 0xA0–0xFF equal Latin-1.
const HIGH: [u16; 32] = [
    0x20AC, 0, 0x201A, 0x0192, 0x201E, 0x2026, 0x2020, 0x2021, 0x02C6, 0x2030, 0x0160, 0x2039,
    0x0152, 0, 0x017D, 0, 0, 0x2018, 0x2019, 0x201C, 0x201D, 0x2022, 0x2013, 0x2014, 0x02DC,
    0x2122, 0x0161, 0x203A, 0x0153, 0, 0x017E, 0x0178,
];

#[derive(Debug)]
pub struct DecodeError(pub u8);

impl std::fmt::Display for DecodeError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "byte 0x{:02X} is undefined in cp1252", self.0)
    }
}

impl std::error::Error for DecodeError {}

/// Decode cp1252 bytes; ASCII-only input is borrowed without copying.
///
/// # Errors
/// On one of the five undefined bytes (Python's strict cp1252 codec fails there too).
pub fn decode(b: &[u8]) -> Result<Cow<'_, str>, DecodeError> {
    if b.is_ascii() {
        // ASCII is valid UTF-8
        return Ok(Cow::Borrowed(
            std::str::from_utf8(b).expect("ASCII is UTF-8"),
        ));
    }
    let mut s = String::with_capacity(b.len() + b.len() / 4);
    for &c in b {
        let ch = match c {
            0x80..=0x9F => {
                let u = HIGH[usize::from(c - 0x80)];
                if u == 0 {
                    return Err(DecodeError(c));
                }
                char::from_u32(u32::from(u)).expect("valid code point")
            }
            _ => char::from(c), // ASCII and the Latin-1 range 0xA0–0xFF
        };
        s.push(ch);
    }
    Ok(Cow::Owned(s))
}

/// Split one record (line without terminator) into raw field byte slices with quotes resolved.
/// Quoted fields containing `""` are unescaped into an owned buffer.
#[allow(clippy::many_single_char_names)] // i/j/k/q = byte cursors
pub fn split_fields<'a>(line: &'a [u8], out: &mut Vec<Cow<'a, [u8]>>) {
    out.clear();
    let mut i = 0;
    loop {
        if line.get(i) == Some(&b'"') {
            // quoted field
            let mut j = i + 1;
            let mut owned: Option<Vec<u8>> = None;
            let mut seg_start = j;
            loop {
                let Some(k) = memchr::memchr(b'"', &line[j..]) else {
                    // unterminated quote: rest of line (Python csv would read on; never generated)
                    let mut v = owned.unwrap_or_default();
                    v.extend_from_slice(&line[seg_start..]);
                    out.push(Cow::Owned(v));
                    return;
                };
                let q = j + k;
                if line.get(q + 1) == Some(&b'"') {
                    // escaped quote: keep one
                    owned
                        .get_or_insert_with(Vec::new)
                        .extend_from_slice(&line[seg_start..=q]);
                    j = q + 2;
                    seg_start = j;
                } else {
                    let field = match owned {
                        Some(mut v) => {
                            v.extend_from_slice(&line[seg_start..q]);
                            Cow::Owned(v)
                        }
                        None => Cow::Borrowed(&line[seg_start..q]),
                    };
                    out.push(field);
                    // after the closing quote: separator or end (anything else is appended
                    // verbatim by Python's csv; the generator never writes that)
                    i = q + 1;
                    break;
                }
            }
            match line.get(i) {
                Some(&b';') => i += 1,
                None => return,
                Some(_) => {
                    // tolerate stray characters up to the next separator (appended like Python's csv)
                    let end = memchr::memchr(b';', &line[i..]).map_or(line.len(), |k| i + k);
                    if let Some(Cow::Owned(v)) = out.last_mut() {
                        v.extend_from_slice(&line[i..end]);
                    } else if let Some(last) = out.pop() {
                        let mut v = last.into_owned();
                        v.extend_from_slice(&line[i..end]);
                        out.push(Cow::Owned(v));
                    }
                    if end == line.len() {
                        return;
                    }
                    i = end + 1;
                }
            }
        } else {
            let Some(k) = memchr::memchr(b';', &line[i..]) else {
                out.push(Cow::Borrowed(&line[i..]));
                return;
            };
            out.push(Cow::Borrowed(&line[i..i + k]));
            i += k + 1;
        }
    }
}

/// Iterate records (lines outside quotes) of `buf`; `\r\n` and `\n` terminators; empty lines skipped.
pub fn for_each_line<'a>(buf: &'a [u8], mut f: impl FnMut(&'a [u8])) {
    let mut start = 0;
    let mut pos = 0;
    let mut in_quotes = false;
    while let Some(k) = memchr::memchr2(b'"', b'\n', &buf[pos..]) {
        let i = pos + k;
        if buf[i] == b'"' {
            in_quotes = !in_quotes;
        } else if !in_quotes {
            let line = &buf[start..i];
            let line = line.strip_suffix(b"\r").unwrap_or(line);
            if !line.is_empty() {
                f(line);
            }
            start = i + 1;
        }
        pos = i + 1;
    }
    if start < buf.len() {
        let line = &buf[start..];
        let line = line.strip_suffix(b"\r").unwrap_or(line);
        if !line.is_empty() {
            f(line);
        }
    }
}

/// Split `data[start..]` into about `n` chunks that each begin at a record start.
/// Quote parity before each candidate cut is computed from parallel quote counts, so a cut never lands
/// inside a quoted field (even one containing line breaks).
#[must_use]
pub fn chunk_bounds(data: &[u8], start: usize, n: usize) -> Vec<(usize, usize)> {
    use rayon::prelude::*;
    let len = data.len();
    let body = len - start;
    let n = n.max(1).min(body.max(1));
    let mut edges: Vec<usize> = (0..=n).map(|k| start + body * k / n).collect();
    edges.dedup();
    let counts: Vec<usize> = edges
        .par_windows(2)
        .map(|w| memchr::memchr_iter(b'"', &data[w[0]..w[1]]).count())
        .collect();
    let mut cuts = vec![start];
    let mut quotes_before = 0usize;
    for (k, &cand) in edges
        .iter()
        .enumerate()
        .skip(1)
        .take(edges.len().saturating_sub(2))
    {
        quotes_before += counts[k - 1];
        let mut in_quotes = quotes_before % 2 == 1;
        let mut pos = cand;
        let mut cut = len;
        while let Some(off) = memchr::memchr2(b'"', b'\n', &data[pos..]) {
            let i = pos + off;
            if data[i] == b'"' {
                in_quotes = !in_quotes;
            } else if !in_quotes {
                cut = i + 1;
                break;
            }
            pos = i + 1;
        }
        if cut > *cuts.last().expect("non-empty") {
            cuts.push(cut);
        }
    }
    if *cuts.last().expect("non-empty") < len {
        cuts.push(len);
    }
    cuts.windows(2)
        .map(|w| (w[0], w[1]))
        .filter(|(a, b)| b > a)
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cp1252_table_matches_encoding_rs() {
        for b in 0u8..=255 {
            let byte = [b];
            let ours = decode(&byte);
            let (theirs, _, _) = encoding_rs::WINDOWS_1252.decode(&byte);
            match b {
                0x81 | 0x8D | 0x8F | 0x90 | 0x9D => {
                    assert!(ours.is_err(), "0x{b:02X} must be undefined");
                }
                _ => assert_eq!(ours.unwrap(), theirs, "byte 0x{b:02X}"),
            }
        }
    }

    #[test]
    fn splits_quoted_fields() {
        let mut v = Vec::new();
        split_fields(br#"1;"a;b";"x""y";;"""q""""#, &mut v);
        let got: Vec<&[u8]> = v.iter().map(AsRef::as_ref).collect();
        assert_eq!(got, vec![&b"1"[..], b"a;b", b"x\"y", b"", b"\"q\""]);
        split_fields(b"", &mut v);
        assert_eq!(v.len(), 1);
        split_fields(b"a;", &mut v);
        assert_eq!(v.len(), 2);
    }

    #[test]
    fn lines_respect_quotes() {
        let mut lines = Vec::new();
        for_each_line(b"h;x\r\n1;\"a\r\nb\"\r\n\r\n2;c\r\n", |l| {
            lines.push(l.to_vec());
        });
        assert_eq!(
            lines,
            vec![b"h;x".to_vec(), b"1;\"a\r\nb\"".to_vec(), b"2;c".to_vec()]
        );
    }

    #[test]
    fn chunks_never_split_quoted_newlines() {
        let mut data = b"h;t\r\n".to_vec();
        for i in 0..500 {
            data.extend_from_slice(format!("{i};\"x\r\ny \"\"q\"\" {i}\"\r\n").as_bytes());
        }
        for n in [1, 2, 3, 7, 64, 1000] {
            let bounds = chunk_bounds(&data, 5, n);
            let mut count = 0;
            let mut covered = 5;
            for (a, b) in bounds {
                assert_eq!(a, covered);
                covered = b;
                for_each_line(&data[a..b], |l| {
                    assert!(
                        l.ends_with(b"\""),
                        "split inside a record: {:?}",
                        String::from_utf8_lossy(l)
                    );
                    count += 1;
                });
            }
            assert_eq!(covered, data.len());
            assert_eq!(count, 500, "n={n}");
        }
    }
}
