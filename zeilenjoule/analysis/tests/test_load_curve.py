"""Phase 9 load curve: knee detection and the report section from a synthetic e.load run."""

from __future__ import annotations

import json

import duckdb

from zj_analysis import load_curve
from zj_analysis.load_curve import Step


def _s(ep: str, rate: float, p95: float, err: float = 0.0, first: float | None = None) -> Step:
    return Step(ep, rate, rate * 60, rate, err, p95 / 2, p95, p95 * 1.2, first, 1.0, 0.5)


def test_knee() -> None:
    steps = [_s("search", 5, 50), _s("search", 20, 90), _s("search", 40, 99), _s("search", 80, 900)]
    last, past = load_curve.knee(steps, "search")
    assert (last.rate if last else None, past.rate if past else None) == (40, 80)  # 900 > 2 × 50
    last, past = load_curve.knee([_s("ask", 1, 3000), _s("ask", 2, 3100, err=0.05)], "ask")
    assert (last.rate if last else None, past.rate if past else None) == (1, 2)  # errors > 1 %
    assert load_curve.knee([_s("ask", 1, 3000)], "ask") == (_s("ask", 1, 3000), None)
    assert load_curve.knee([], "ask") == (None, None)


def _con() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    steps = [
        {"stage": "search_20", "endpoint": "search", "rate_rps": 20, "planned": 1200, "ok": 1200,
         "ok_per_s": 20, "latency_ms": {"p50": 40, "p95": 80, "p99": 95, "max": 120}},
        {"stage": "ask_1", "endpoint": "ask", "rate_rps": 1, "planned": 90, "ok": 90, "ok_per_s": 1,
         "latency_ms": {"p50": 3000, "p95": 9000, "p99": 9500, "max": 9600},
         "first_event_ms": {"p50": 1000, "p95": 1400, "p99": 1450, "max": 1500}},
    ]  # fmt: skip
    stages = [{"name": "ingest", "depth": 0, "t_s": 100.0, "gpu_j": 1.0, "cpu_j": 1.0},
              {"name": "search_20", "depth": 0, "t_s": 60.0, "gpu_j": 600.0, "cpu_j": 600.0},
              {"name": "ask_1", "depth": 0, "t_s": 90.0, "gpu_j": 9000.0, "cpu_j": 900.0}]  # fmt: skip
    con.execute(
        "CREATE TABLE ok_runs AS SELECT 'e07_stack_load' contestant, 'M' scale, 'E' track, "
        "'e.load' task, 's1' session_id, now() ts, 100000 rows, ?::JSON stages, ?::JSON result, "
        "?::JSON idle_w, false anti_pattern",
        [
            json.dumps(stages),
            json.dumps({"extra": {"load_steps": steps}}),
            json.dumps({"gpu": 5.0, "cpu_pkg": 5.0}),
        ],
    )
    return con


def test_section_from_runs() -> None:
    con = _con()
    (cv,) = load_curve.curves(con)
    s20 = cv.steps[0]
    assert (cv.contestant, cv.ingest_s, s20.endpoint) == ("e07_stack_load", 100.0, "search")
    assert s20.j_req == 1.0  # (600 + 600) J / 1200 requests
    assert s20.j_req_net == 0.5  # minus 10 W × 60 s
    md = load_curve.section_md(con, "en")
    assert "`/v1/search` p95 < 100 ms at 20 req/s | p95 80 ms, 0.0 % | ✓" in md
    assert "met up to 1.0 req/s | ✓" in md
