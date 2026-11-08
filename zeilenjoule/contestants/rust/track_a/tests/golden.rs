//! `spec/track_a_cases.jsonl` against the shared rules and both field-parser flavours (regex, hand).
#![allow(clippy::many_single_char_names)] // r = rules, f = fields, s/n = helpers

use arrow_array::{
    Array, BooleanArray, Date32Array, Float64Array, Int8Array, Int32Array, StringArray,
};
use serde_json::{Map, Value, json};
use std::path::PathBuf;
use zj_track_a::fields::{Hand, Regex};
use zj_track_a::{Date, Fields, INPUT_COLUMNS, Lexicon, Rules, doc_text, out, process};

fn repo() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../..")
}

fn rules() -> Rules {
    Rules::new(Lexicon::load(&repo().join("spec/track_a_lexicon.json")).unwrap())
}

fn cases() -> Vec<Value> {
    std::fs::read_to_string(repo().join("spec/track_a_cases.jsonl"))
        .unwrap()
        .lines()
        .map(|l| serde_json::from_str(l).unwrap())
        .collect()
}

fn opt<T: Into<Value>>(v: Option<T>) -> Value {
    v.map_or(Value::Null, Into::into)
}

fn iso(days: i32) -> String {
    // inverse of Date::days for the test only (civil_from_days)
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = yoe + era * 400 + i32::from(m <= 2);
    format!("{y:04}-{m:02}-{d:02}")
}

fn row_json<F: Fields>(r: &Rules, f: &F, input: &Map<String, Value>) -> Map<String, Value> {
    let fields: Vec<&str> = INPUT_COLUMNS
        .iter()
        .map(|c| input[*c].as_str().unwrap())
        .collect();
    let rec: [&str; 11] = fields.try_into().unwrap();
    let mut b = out::Builder::with_capacity(1);
    process(r, f, &rec, &mut b);
    let batch = b.finish().unwrap();
    let mut m = Map::new();
    for (i, field) in out::SCHEMA.fields().iter().enumerate() {
        let c = batch.column(i);
        let v = if c.is_null(0) {
            Value::Null
        } else if let Some(a) = c.as_any().downcast_ref::<StringArray>() {
            json!(a.value(0))
        } else if let Some(a) = c.as_any().downcast_ref::<Date32Array>() {
            json!(iso(a.value(0)))
        } else if let Some(a) = c.as_any().downcast_ref::<Int8Array>() {
            json!(a.value(0))
        } else if let Some(a) = c.as_any().downcast_ref::<Int32Array>() {
            json!(a.value(0))
        } else if let Some(a) = c.as_any().downcast_ref::<Float64Array>() {
            json!(a.value(0))
        } else if let Some(a) = c.as_any().downcast_ref::<BooleanArray>() {
            json!(a.value(0))
        } else {
            panic!("unexpected type for {}", field.name());
        };
        m.insert(field.name().clone(), v);
    }
    m
}

fn eval<F: Fields>(r: &Rules, f: &F, fname: &str, input: &Value) -> Value {
    let s = || input.as_str().unwrap();
    let n = |x: &str| r.norm(x);
    match fname {
        "norm" => json!(n(s())),
        "equipment" => opt(f.equipment(&n(s()))),
        "date" => opt(f.date(&n(s())).map(|d| d.to_string())),
        "prio" => opt(f.prio(&n(s()))),
        "eur" => opt(f.eur(&n(s()))),
        "minutes" => opt(f.minutes(&n(s()))),
        "expand" => json!(r.expand(s())),
        "fold" => json!(r.fold(&s().to_lowercase())),
        "lower" => json!(s().to_lowercase()),
        "materialnr" => opt(f.materialnr(&r.expand(&n(s()))).map(str::to_owned)),
        "safety" => json!(r.safety(s())),
        "komponente" => opt(r.komponente(s()).map(str::to_owned)),
        "doc_text" => {
            let d = input.as_object().unwrap();
            let datum = d["datum"].as_str().map(|x| {
                let p: Vec<i32> = x.split('-').map(|t| t.parse().unwrap()).collect();
                Date::new(
                    p[0],
                    u32::try_from(p[1]).unwrap(),
                    u32::try_from(p[2]).unwrap(),
                )
                .unwrap()
            });
            let prio = d["prioritaet"].as_i64().map(|p| i8::try_from(p).unwrap());
            json!(doc_text(
                d["werk"].as_str().unwrap(),
                d["anlage"].as_str().unwrap(),
                d["equipment_nr"].as_str(),
                datum,
                prio,
                d["text_norm"].as_str().unwrap(),
            ))
        }
        "row" => Value::Object(row_json(r, f, input.as_object().unwrap())),
        other => panic!("unknown fn {other}"),
    }
}

fn check<F: Fields>(flavour: &str, f: &F) {
    let r = rules();
    let mut failures = Vec::new();
    let all = cases();
    assert!(all.len() >= 150);
    for c in &all {
        let (id, fname, exp) = (
            c["id"].as_str().unwrap(),
            c["fn"].as_str().unwrap(),
            &c["expected"],
        );
        let got = eval(&r, f, fname, &c["input"]);
        let ok = match (fname, exp) {
            ("row", Value::Object(e)) => e.iter().all(|(k, v)| match (&got[k], v) {
                (Value::Number(a), Value::Number(b)) => {
                    (a.as_f64().unwrap() - b.as_f64().unwrap()).abs() < 1e-9
                }
                (a, b) => a == b,
            }),
            (_, Value::Number(e)) if e.is_f64() => got
                .as_f64()
                .is_some_and(|g| (g - e.as_f64().unwrap()).abs() < 1e-9),
            _ => &got == exp,
        };
        if !ok {
            failures.push(format!(
                "{id} {fname}: input={} expected={exp} got={got}",
                c["input"]
            ));
        }
    }
    assert!(
        failures.is_empty(),
        "{flavour}: {} failures\n{}",
        failures.len(),
        failures.join("\n")
    );
}

#[test]
fn golden_regex() {
    check("regex", &Regex::default());
}

#[test]
fn golden_hand() {
    check("hand", &Hand);
}
