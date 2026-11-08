import json
from pathlib import Path

import duckdb

from zj_analysis.load import build


def _rec(
    status: str, passed: bool | None, warmup: bool = False, track: str = "A", **kw: object
) -> dict[str, object]:
    r: dict[str, object] = {
        "run_id": f"{status}-{passed}-{warmup}-{track}",
        "contestant": "a09_polars_eager",
        "track": track,
        "status": status,
        "warmup": warmup,
        "verify": {"passed": passed, "detail": ""},
        "wall_s": 1.5,
        "energy_j": {"gpu": 10.0, "cpu_pkg": None, "cpu_core": None, "dram": None, "wall": None},
        "stages": [{"name": "parse", "t_s": 1.0, "gpu_j": 3.0}],
    }
    r.update(kw)
    return r


def test_build_runs_idle_and_ok_view(tmp_path: Path) -> None:
    res = tmp_path / "results"
    res.mkdir()
    recs = [
        _rec("ok", True),
        _rec("ok", True, warmup=True),
        _rec("ok", True, track="X"),
        _rec("disqualified", False),
        _rec("dnf", None, extra_field="only here"),  # a field present in one line only
    ]
    (res / "runs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs), encoding="utf-8")
    (res / "idle.jsonl").write_text(
        json.dumps({"session_id": "s", "power_w": {"gpu": 6.1}}) + "\n", encoding="utf-8"
    )

    assert build(tmp_path) == {"runs": 5, "idle": 1}
    con = duckdb.connect(str(res / "zj.duckdb"), read_only=True)
    assert con.execute("SELECT count(*) FROM ok_runs").fetchone() == (1,)
    # nested structs stay queryable; unmeasured stays NULL (rule 3)
    assert con.execute("SELECT energy_j.gpu, energy_j.cpu_pkg FROM ok_runs").fetchone() == (10.0, None)
    assert con.execute("SELECT stages[1].name FROM ok_runs").fetchone() == ("parse",)
    assert con.execute("SELECT count(extra_field) FROM runs").fetchone() == (1,)
    con.close()
    assert not (res / "zj.duckdb.tmp").exists()


def test_build_without_results(tmp_path: Path) -> None:
    (tmp_path / "results").mkdir()
    assert build(tmp_path) == {"runs": 0, "idle": 0}


def test_optional_fields_absent_everywhere(tmp_path: Path) -> None:
    """Real case: all runs ok → Go omits status_detail in every line; the column must still exist."""
    res = tmp_path / "results"
    res.mkdir()
    rec = _rec("ok", True)
    (res / "runs.jsonl").write_text(json.dumps(rec) + "\n", encoding="utf-8")
    build(tmp_path)
    con = duckdb.connect(str(res / "zj.duckdb"), read_only=True)
    assert con.execute("SELECT status_detail, signal, output_path FROM runs").fetchone() == (None, None, None)
    con.close()
