//! Field parsers for spec §2–5 and the material number, in two flavours with identical results:
//! [`Regex`] uses the spec's regular expressions (regex crate), [`Hand`] is hand-written byte logic.

use crate::{Date, Fields};

const HOURS: [&str; 4] = ["h", "std.", "stunde", "stunden"];
const UNITS: [&str; 6] = ["h", "std.", "stunde", "stunden", "min", "minuten"];

/// Exact decimal `whole,frac` × factor, rounded half up (non-negative): `(2·num·f + den) div (2·den)`.
///
/// # Panics
/// If the value does not fit Int32 (the reference cannot write such a value either).
fn minutes_value(whole: &str, frac: &str, hours: bool) -> i32 {
    let digits = format!("{whole}{frac}");
    let num: u128 = digits.parse().expect("duration digits overflow u128");
    let den = 10u128
        .checked_pow(u32::try_from(frac.len()).expect("fraction length"))
        .expect("fraction too long");
    let factor: u128 = if hours { 60 } else { 1 };
    let v = (2 * num * factor + den) / (2 * den);
    i32::try_from(v).expect("duration exceeds Int32")
}

fn eur_value(neg: bool, int_digits: &str, frac: &str) -> f64 {
    let mut s = String::with_capacity(int_digits.len() + frac.len() + 2);
    if neg {
        s.push('-');
    }
    s.extend(int_digits.chars().filter(char::is_ascii_digit));
    s.push('.');
    s.push_str(if frac.is_empty() { "0" } else { frac });
    s.parse().expect("validated decimal") // correctly rounded, like Python float()
}

// ------------------------------------------------------------------------------------- regex
pub struct Regex {
    equipment: regex::Regex,
    date_de: regex::Regex,
    date_iso: regex::Regex,
    number: regex::Regex,
    duration: regex::Regex,
    matnr: regex::Regex,
}

impl Default for Regex {
    fn default() -> Self {
        let re = |p: &str| regex::Regex::new(p).expect("spec regex");
        Self {
            equipment: re(r"^([A-Z])[ -]?([0-9]{4})(-[A-Z])?$"),
            date_de: re(r"^([0-9]{1,2})\.([0-9]{1,2})\.([0-9]{2}|[0-9]{4})$"),
            date_iso: re(r"^([0-9]{4})-([0-9]{2})-([0-9]{2})$"),
            number: re(r"^(-)?([0-9]{1,3}(?:[. ][0-9]{3})+|[0-9]+)(?:,([0-9]+))?(?: ?(?:€|EUR))?$"),
            duration: re(
                r"(?i)^(?:ca\. ?)?(?:[0-9]+(?:,[0-9]+)? ?- ?)?([0-9]+)(?:,([0-9]+))? ?(h|std\.|stunde|stunden|min|minuten)$",
            ),
            matnr: re(r"(?:^|[^0-9])(10[0-9]{6})(?:[^0-9]|$)"),
        }
    }
}

impl Fields for Regex {
    fn equipment(&self, s: &str) -> Option<String> {
        let c = self.equipment.captures(s)?;
        Some(format!(
            "{}-{}{}",
            &c[1],
            &c[2],
            c.get(3).map_or("", |m| m.as_str())
        ))
    }

    fn date(&self, s: &str) -> Option<Date> {
        if let Some(c) = self.date_de.captures(s) {
            let y: i32 = c[3].parse().ok()?;
            let y = if c[3].len() == 2 { y + 2000 } else { y };
            return Date::new(y, c[2].parse().ok()?, c[1].parse().ok()?);
        }
        let c = self.date_iso.captures(s)?;
        Date::new(c[1].parse().ok()?, c[2].parse().ok()?, c[3].parse().ok()?)
    }

    fn prio(&self, s: &str) -> Option<i8> {
        prio_digits(s)
    }

    fn eur(&self, s: &str) -> Option<f64> {
        let c = self.number.captures(s)?;
        Some(eur_value(
            c.get(1).is_some(),
            &c[2],
            c.get(3).map_or("", |m| m.as_str()),
        ))
    }

    fn minutes(&self, s: &str) -> Option<i32> {
        let c = self.duration.captures(s)?;
        let unit = c[3].to_lowercase();
        Some(minutes_value(
            &c[1],
            c.get(2).map_or("", |m| m.as_str()),
            HOURS.contains(&unit.as_str()),
        ))
    }

    fn materialnr<'a>(&self, text_norm: &'a str) -> Option<&'a str> {
        self.matnr
            .captures(text_norm)
            .and_then(|c| c.get(1))
            .map(|m| m.as_str())
    }
}

/// `^[0-9]+$` and 1 ≤ value ≤ 4 (leading zeros allowed; huge values are simply out of range).
fn prio_digits(s: &str) -> Option<i8> {
    if s.is_empty() || !s.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    let t = s.trim_start_matches('0');
    match t {
        "1" => Some(1),
        "2" => Some(2),
        "3" => Some(3),
        "4" => Some(4),
        _ => None,
    }
}

// -------------------------------------------------------------------------------------- hand
/// Hand-written parsers (byte level; every pattern is ASCII except "€").
#[derive(Default)]
pub struct Hand;

fn digits_len(b: &[u8]) -> usize {
    b.iter().take_while(|c| c.is_ascii_digit()).count()
}

fn num2(b: &[u8]) -> u32 {
    b.iter().fold(0, |a, &c| a * 10 + u32::from(c - b'0'))
}

/// `[0-9]+(?:,[0-9]+)?` at the start of `b` → (whole, frac, consumed bytes).
fn decimal(b: &[u8]) -> Option<(usize, usize, usize)> {
    let w = digits_len(b);
    if w == 0 {
        return None;
    }
    if b.get(w) == Some(&b',') {
        let f = digits_len(&b[w + 1..]);
        if f > 0 {
            return Some((w, f, w + 1 + f));
        }
    }
    Some((w, 0, w))
}

fn eq_ignore_ascii_case(a: &[u8], b: &str) -> bool {
    a.eq_ignore_ascii_case(b.as_bytes())
}

#[allow(clippy::many_single_char_names)] // b = bytes, i/j = cursors, d/m/y = date parts
impl Fields for Hand {
    fn equipment(&self, s: &str) -> Option<String> {
        let b = s.as_bytes();
        let letter = *b.first()?;
        if !letter.is_ascii_uppercase() {
            return None;
        }
        let mut i = 1;
        if matches!(b.get(1), Some(b' ' | b'-')) && b.len() > 2 && b[2].is_ascii_digit() {
            i = 2;
        }
        if b.len() < i + 4 || digits_len(&b[i..i + 4]) != 4 {
            return None;
        }
        let num = &s[i..i + 4];
        let rest = &b[i + 4..];
        let suffix = match rest {
            [] => "",
            [b'-', c] if c.is_ascii_uppercase() => &s[i + 4..],
            _ => return None,
        };
        Some(format!("{}-{num}{suffix}", letter as char))
    }

    fn date(&self, s: &str) -> Option<Date> {
        let b = s.as_bytes();
        // ISO yyyy-mm-dd
        if b.len() == 10 && b[4] == b'-' && b[7] == b'-' {
            let (y, m, d) = (&b[0..4], &b[5..7], &b[8..10]);
            if [y, m, d].iter().all(|p| p.iter().all(u8::is_ascii_digit)) {
                return Date::new(i32::try_from(num2(y)).ok()?, num2(m), num2(d));
            }
            return None;
        }
        // German d.m.yy / dd.mm.yyyy
        let d = digits_len(b);
        if !(1..=2).contains(&d) || b.get(d) != Some(&b'.') {
            return None;
        }
        let r = &b[d + 1..];
        let m = digits_len(r);
        if !(1..=2).contains(&m) || r.get(m) != Some(&b'.') {
            return None;
        }
        let yb = &r[m + 1..];
        let y = digits_len(yb);
        if y != yb.len() || !(y == 2 || y == 4) {
            return None;
        }
        let year = i32::try_from(num2(yb)).ok()? + if y == 2 { 2000 } else { 0 };
        Date::new(year, num2(&r[..m]), num2(&b[..d]))
    }

    fn prio(&self, s: &str) -> Option<i8> {
        prio_digits(s)
    }

    fn eur(&self, s: &str) -> Option<f64> {
        let b = s.as_bytes();
        let neg = b.first() == Some(&b'-');
        let start = usize::from(neg);
        let d1 = digits_len(&b[start..]);
        if d1 == 0 {
            return None;
        }
        let mut i = start + d1;
        if d1 <= 3 {
            // greedy thousands groups: [. ] + exactly three digits not followed by a digit
            while i + 4 <= b.len()
                && matches!(b[i], b'.' | b' ')
                && digits_len(&b[i + 1..i + 4]) == 3
                && !b.get(i + 4).is_some_and(u8::is_ascii_digit)
            {
                i += 4;
            }
        }
        let int_part = &s[start..i];
        let mut frac = "";
        if b.get(i) == Some(&b',') {
            let f = digits_len(&b[i + 1..]);
            if f == 0 {
                return None;
            }
            frac = &s[i + 1..i + 1 + f];
            i += 1 + f;
        }
        // `(?: ?(?:€|EUR))?$`
        if !matches!(&s[i..], "" | "€" | "EUR" | " €" | " EUR") {
            return None;
        }
        Some(eur_value(neg, int_part, frac))
    }

    fn minutes(&self, s: &str) -> Option<i32> {
        let b = s.as_bytes();
        let mut i = 0;
        if b.len() >= 3 && eq_ignore_ascii_case(&b[..3], "ca.") {
            i = 3;
            if b.get(i) == Some(&b' ') {
                i += 1;
            }
        }
        let (mut w, mut f, n) = decimal(&b[i..])?;
        let mut vstart = i;
        i += n;
        // optional "lower - " range: the first number was the lower bound
        let mut j = i;
        if b.get(j) == Some(&b' ') {
            j += 1;
        }
        if b.get(j) == Some(&b'-') {
            j += 1;
            if b.get(j) == Some(&b' ') {
                j += 1;
            }
            let (w2, f2, n2) = decimal(&b[j..])?;
            (w, f, vstart) = (w2, f2, j);
            i = j + n2;
        }
        let whole = &s[vstart..vstart + w];
        let frac = if f > 0 {
            &s[vstart + w + 1..vstart + w + 1 + f]
        } else {
            ""
        };
        if b.get(i) == Some(&b' ') {
            i += 1;
        }
        let unit = &b[i..];
        let u = UNITS.iter().find(|u| eq_ignore_ascii_case(unit, u))?;
        Some(minutes_value(whole, frac, HOURS.contains(u)))
    }

    fn materialnr<'a>(&self, text_norm: &'a str) -> Option<&'a str> {
        let b = text_norm.as_bytes();
        let mut i = 0;
        while i < b.len() {
            if b[i].is_ascii_digit() {
                let n = digits_len(&b[i..]);
                if n == 8 && b[i] == b'1' && b[i + 1] == b'0' {
                    return Some(&text_norm[i..i + 8]);
                }
                i += n;
            } else {
                i += 1;
            }
        }
        None
    }
}
