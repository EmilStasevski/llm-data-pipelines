"""a23: the GPU rewrites must give the spec results (golden cases) on the CPU and on the GPU engine."""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from zj_gpu_df import a23_polars_gpu_hybrid as a23

sys.path.insert(0, str(a23.DF_SRC))
from zj_df.track_a.common import INPUT_COLUMNS

REPO = Path(__file__).resolve().parents[4]
LEX = json.loads((REPO / "spec" / "track_a_lexicon.json").read_text(encoding="utf-8"))
CASES = [
    json.loads(x) for x in (REPO / "spec" / "track_a_cases.jsonl").read_text(encoding="utf-8").splitlines()
]
G = a23.gpu_rules_class()(LEX)


def _has_gpu() -> bool:
    try:
        pl.DataFrame({"x": [1]}).lazy().select(pl.col("x") + 1).collect(
            engine=pl.GPUEngine(device=0, raise_on_fail=True)
        )
    except Exception:  # any failure means "no usable GPU here"
        return False
    return True


ENGINES = ["cpu", pytest.param("gpu", marks=pytest.mark.skipif(not _has_gpu(), reason="no usable GPU"))]


def _engine(name: str) -> Any:
    return pl.GPUEngine(device=0, raise_on_fail=True) if name == "gpu" else "in-memory"


def _iso(d: Any) -> Any:
    return d.isoformat() if isinstance(d, dt.date) else d


def _field(engine: str, expr: Callable[[pl.Expr], pl.Expr], value: str) -> Any:
    normed = pl.DataFrame({"x": [value]}, schema={"x": pl.String}).select(G.norm(pl.col("x")))
    return normed.lazy().select(expr(pl.col("x"))).collect(engine=_engine(engine)).item()


def _row(engine: str, r: dict[str, str]) -> dict[str, Any]:
    raw = pl.DataFrame({c: [r[c]] for c in INPUT_COLUMNS})
    normed = G.cpu_normalise(raw.lazy()).collect()
    fields = G.gpu_fields(normed.lazy()).collect(engine=_engine(engine))
    out = G.cpu_komponente(fields.lazy()).collect().row(0, named=True)
    return {k: _iso(v) for k, v in out.items()}


FNS: dict[str, Callable[[str, Any], Any]] = {
    "equipment": lambda e, s: _field(e, G.equipment, s),
    "date": lambda e, s: _iso(_field(e, G.date, s)),
    "prio": lambda e, s: _field(e, G.prio, s),
    "eur": lambda e, s: _field(e, G.eur, s),
    "minutes": lambda e, s: _field(e, G.minutes, s),
    "row": _row,
}


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("case", [c for c in CASES if c["fn"] in FNS], ids=lambda c: f"{c['id']}-{c['fn']}")
def test_golden(engine: str, case: dict[str, Any]) -> None:
    got = FNS[case["fn"]](engine, case["input"])
    exp = case["expected"]
    if case["fn"] == "row":
        assert {k: got[k] for k in exp} == exp
    elif isinstance(exp, float):
        assert got == pytest.approx(exp, abs=1e-9)
    else:
        assert got == exp


def test_case_classes_match_python_ignorecase() -> None:
    letters = "".join(a23.CASE_CLASSES)
    any_letter = re.compile(f"[{letters}]", re.IGNORECASE)
    found: dict[str, set[str]] = {ch: set() for ch in letters}
    for cp in range(0x110000):
        if 0xD800 <= cp <= 0xDFFF:
            continue
        c = chr(cp)
        if any_letter.fullmatch(c):
            for ch in letters:
                if re.fullmatch(ch, c, re.IGNORECASE):
                    found[ch].add(c)
    assert {ch: set(v) for ch, v in a23.CASE_CLASSES.items()} == found


@pytest.mark.parametrize(
    "text",
    [
        "ca. 2-3,5 Std.",
        "45 min",
        "1,5 h",
        "2 STUNDEN",
        "3 \u017ftd.",
        "10 M\u0131nuten",
        "ca.5 Stunde",
        "5 sek",
        "x 3 h",
    ],
)
def test_duration_regex_equals_ignorecase_reference(text: str) -> None:
    want = re.fullmatch(
        r"(?:ca\. ?)?(?:([0-9]+(?:,[0-9]+)?) ?- ?)?([0-9]+(?:,[0-9]+)?) ?"
        r"(h|std\.|stunde|stunden|min|minuten)",
        text,
        re.IGNORECASE,
    )
    got = re.fullmatch(a23.RE_DURATION_GPU.removeprefix("^").removesuffix("$"), text)
    assert (want and want.groups()) == (got and got.groups())
