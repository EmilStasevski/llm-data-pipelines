# zjmark: stage markers and result line (PLAN §5.3/§5.4)

Contestants report progress on **stderr** and their result on **stdout**, one line each:

```
ZJMARK <json>      # stderr, flushed immediately
ZJRESULT <json>    # stdout, exactly one, the last line
```

## Canonical JSON (all languages must produce identical bytes)
- compact separators (`,` `:`), no whitespace
- **all object keys sorted lexicographically** (at every level)
- UTF-8 output, **no** `\uXXXX` escaping of non-ASCII, **no** HTML escaping of `< > &`
- integers, strings, booleans, null, arrays, objects only. Floats are allowed in `extra`, but they're excluded from the golden test because float formatting differs by language. The harness compares by value.

## ZJMARK
`{"ev":"start"|"end","stage":"<name>"}` with `name` matching `^[a-z][a-z0-9_]{0,31}$`.
The harness timestamps each line **on receipt** (monotonic clock); contestants send no timestamps.
Rules: stages may nest but must not interleave (LIFO); every `start` needs a matching `end`.

## ZJRESULT
`{"extra":{…},"notes":"…","rows":N}`. All three keys are always present (`extra` may be `{}`, `notes` may be `""`).

The golden sequence in `zjmark_golden.txt` is produced by every helper library's test
(`libs/python/zjmark`, `libs/rust/zjmark`, `harness/pkg/zjmark`):
start parse → end parse → start embed → end embed →
result(rows=3, notes="Prüfung <ok> & fertig", extra={threads:16, engine:"cpu", fallback_ops:["Series.apply"]}).
