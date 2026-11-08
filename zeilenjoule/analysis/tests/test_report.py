"""Report generation from synthetic run records (no real measurements needed)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from zj_analysis import report

REPO = Path(__file__).resolve().parents[2]


def _rec(c: str, rep: int, wall: float, gpu: float | None, cpu: float | None, **kw: Any) -> dict[str, Any]:
    r: dict[str, Any] = {
        "run_id": f"{c}-{rep}", "contestant": c, "track": "A", "scale": "M", "rows": 100_000, "rep": rep,
        "session_id": "s1", "ts": f"2026-11-07T00:00:{rep:02d}Z",
        "warmup": False, "anti_pattern": c.startswith("a02"), "status": "ok", "status_detail": "",
        "verify": {"passed": True, "detail": ""}, "wall_s": wall, "energy_confidence": "high",
        "energy_j": {"gpu": gpu, "cpu_pkg": cpu, "cpu_core": None, "dram": None, "wall": None},
        "energy_net_j": {"gpu": gpu, "cpu_pkg": cpu, "cpu_core": None, "dram": None, "wall": None},
    }  # fmt: skip
    r.update(kw)
    return r


def _repo(tmp_path: Path, recs: list[dict[str, Any]]) -> Path:
    (tmp_path / "results").mkdir()
    (tmp_path / "config").mkdir()
    shutil.copy(REPO / "config" / "bench.yaml", tmp_path / "config" / "bench.yaml")
    (tmp_path / "results" / "runs.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in recs), encoding="utf-8"
    )
    return tmp_path


def test_report_ranks_by_median_and_lists_problems(tmp_path: Path) -> None:
    recs = [_rec("a09_polars_eager", i, w, 10.0, 90.0) for i, w in enumerate([1.0, 1.2, 1.1], 1)]
    recs += [_rec("a02_pandas_iterrows", i, w, 50.0, 950.0) for i, w in enumerate([10.0, 10.0, 10.0], 1)]
    recs += [_rec("a99_wrong_tool", 1, 0.5, 1.0, 1.0, status="disqualified", status_detail="verify: mismatch",
                  verify={"passed": False, "detail": "mismatch"})]  # fmt: skip
    recs += [_rec("a09_polars_eager", 0, 0.1, 1.0, 1.0, warmup=True)]  # warm-ups never count
    repo = _repo(tmp_path, recs)
    assert report.main(["--repo", str(repo)]) == 0
    en = (repo / "reports" / "REPORT.md").read_text(encoding="utf-8")
    de = (repo / "reports" / "REPORT.de.md").read_text(encoding="utf-8")
    lines = [ln for ln in en.splitlines() if ln.startswith("| `")]
    assert lines[0].startswith("| `a09_polars_eager` | 3 | 1.100")  # median, ranked first, warm-up excluded
    assert "⚠anti" in lines[1]
    assert "| 1.00 |" in lines[0]  # best at both → ZJ score 1.00
    assert "a99_wrong_tool" in en  # listed, not ranked
    assert "disqualified" in en
    assert "| `a99_wrong_tool` | 3" not in en
    # 100 J for 100 000 rows → 1 mJ/row; € per 1M rows at 0.373 €/kWh = 1e-3 J × 1e6 / 3.6e6 × 0.373
    assert "| 1.000 |" in lines[0]
    assert "| 0.000104 |" in lines[0]  # 1e-3 J × 1e6 / 3.6e6 × 0.373 = 1.036e-4 → 3 significant digits
    assert "Generiert von `task report`" in de
    assert "1,100" in de  # decimal comma in German prose tables


def test_unmeasured_energy_is_reported_as_not_measured(tmp_path: Path) -> None:
    repo = _repo(tmp_path, [_rec("a09_polars_eager", i, 1.0, None, None) for i in range(1, 4)])
    report.main(["--repo", str(repo)])
    assert "not measured" in (repo / "reports" / "REPORT.md").read_text(encoding="utf-8")
    assert "nicht gemessen" in (repo / "reports" / "REPORT.de.md").read_text(encoding="utf-8")


def test_empty_results(tmp_path: Path) -> None:
    repo = _repo(tmp_path, [])
    (repo / "results" / "runs.jsonl").write_text("", encoding="utf-8")
    report.main(["--repo", str(repo)])
    assert "No data" in (repo / "reports" / "REPORT.md").read_text(encoding="utf-8")


def test_sig_formatting() -> None:
    assert report.fmt_sig(2.61e-5, 3, "en") == "0.0000261"
    assert report.fmt_sig(2.61e-5, 3, "de") == "0,0000261"
    assert report.fmt_sig(0.123456, 3, "en") == "0.123"
    assert report.fmt_sig(None, 3, "en") == "not measured"


def test_gpu_runs_with_cpu_fallback_are_labelled(tmp_path: Path) -> None:
    fb = {"rows": 100_000, "notes": "", "extra": {"engine": "gpu+cpu_fallback", "fallback_ops": ["read_csv"]}}
    gpu: dict[str, Any] = {"rows": 100_000, "notes": "", "extra": {"engine": "gpu", "fallback_ops": []}}
    recs = [_rec("a07_cudf_pandas", i, 1.0, 5.0, 5.0, result=fb) for i in range(1, 4)]
    recs += [_rec("a12_polars_gpu", i, 1.0, 5.0, 5.0, result=gpu) for i in range(1, 4)]
    recs += [_rec("a05_pandas_vectorized", i, 1.0, 5.0, 5.0) for i in range(1, 4)]  # no result at all
    repo = _repo(tmp_path, recs)
    report.main(["--repo", str(repo)])
    en = (repo / "reports" / "REPORT.md").read_text(encoding="utf-8")
    assert "`a07_cudf_pandas` ⚠CPU fallback" in en
    assert "`a12_polars_gpu` |" in en
    assert "`a05_pandas_vectorized` |" in en
    assert "⚠CPU-Fallback" in (repo / "reports" / "REPORT.de.md").read_text(encoding="utf-8")


def test_latest_session_per_contestant_and_scale(tmp_path: Path) -> None:
    old = [
        _rec("a10_polars_lazy", i, 9.0, 5.0, 5.0, session_id="s1", ts=f"2026-11-07T10:00:0{i}Z")
        for i in (1, 2, 3)
    ]
    new = [
        _rec("a10_polars_lazy", i, 3.0, 5.0, 5.0, session_id="s2", ts=f"2026-11-07T12:00:0{i}Z")
        for i in (1, 2, 3)
    ]
    repo = _repo(tmp_path, old + new)
    report.main(["--repo", str(repo)])
    en = (repo / "reports" / "REPORT.md").read_text(encoding="utf-8")
    assert "| `a10_polars_lazy` | 3 | 3.000 |" in en  # n = 3 from s2 only, never a mix of both sessions


def test_track_b_section_counts_documents(tmp_path: Path) -> None:
    recs = [_rec("a09_polars_eager", i, 1.0, 10.0, 90.0) for i in range(1, 4)]
    recs += [_rec("b04_ort_cuda", i, 20.0, 1000.0, 1000.0, track="B", scale="real", rows=10_000)
             for i in range(1, 4)]  # fmt: skip
    repo = _repo(tmp_path, recs)
    report.main(["--repo", str(repo)])
    en = (repo / "reports" / "REPORT.md").read_text(encoding="utf-8")
    de = (repo / "reports" / "REPORT.de.md").read_text(encoding="utf-8")
    assert en.index("## Track A") < en.index("## Track B")
    assert "### Scale real (10\u2009000 docs)" in en
    assert "| docs/s | E gross J | E net J | mJ/doc |" in en
    assert "| `b04_ort_cuda` | 3 | 20.000" in en
    assert "| 200.000 |" in en  # 2000 J / 10 000 docs = 200 mJ/doc
    assert "Skala real (10\u2009000 Dokumente)" in de


def test_track_b_omitted_without_runs(tmp_path: Path) -> None:
    repo = _repo(tmp_path, [_rec("a09_polars_eager", i, 1.0, 10.0, 90.0) for i in range(1, 4)])
    report.main(["--repo", str(repo)])
    assert "Track B" not in (repo / "reports" / "REPORT.md").read_text(encoding="utf-8")


def test_cpu_only_contestants_get_no_gpu_net_energy(tmp_path: Path) -> None:
    # ADR 0014: GPU idle drift made E_net negative for CPU-only runs; their GPU net share is 0
    recs = [_rec("a17_rs_handrolled", i, 10.0, 40.0, 100.0) for i in range(1, 4)]
    recs += [_rec("a23_polars_gpu_hybrid", i, 10.0, 40.0, 100.0) for i in range(1, 4)]
    for r in recs:
        r["energy_net_j"] = {"gpu": -20.0, "cpu_pkg": 80.0, "cpu_core": None, "dram": None, "wall": None}
    repo = _repo(tmp_path, recs)
    (repo / "config" / "contestants.yaml").write_text(
        "contestants:\n"
        "  - {id: a17_rs_handrolled, needs_gpu: false}\n"
        "  - {id: a23_polars_gpu_hybrid, needs_gpu: true}\n",
        encoding="utf-8",
    )
    report.main(["--repo", str(repo)])
    en = (repo / "reports" / "REPORT.md").read_text(encoding="utf-8")
    a17 = next(ln for ln in en.splitlines() if ln.startswith("| `a17"))
    a23 = next(ln for ln in en.splitlines() if ln.startswith("| `a23"))
    assert "| 140.0 | 80.0 |" in a17  # gross unchanged, GPU net share dropped
    assert "| 140.0 | 60.0 |" in a23  # GPU contestant keeps its (here negative) GPU net share


def test_track_c_details_table(tmp_path: Path) -> None:
    stages = [{"name": n, "depth": 0, "t_s": t, "gpu_j": 1.0, "cpu_j": 1.0, "dram_j": None}
              for n, t in (("load", 2.0), ("ingest", 5.0), ("index", 30.0), ("query", 4.0),
                           ("write", 0.1))]  # fmt: skip
    res = {"rows": 100_000, "notes": "", "extra": {"engine": "cpu (server)",
           "latency_ms": {"p50": 3.5, "p95": 9.25, "p99": 12.0}}}  # fmt: skip
    recs = [_rec("c03_pgvector", i, 42.0, 100.0, 900.0, track="C", task="c.dense", stages=stages, result=res,
                 quality={"f1": None, "recall_at_10": 0.9991, "ndcg_at_10": 0.8477})
            for i in range(1, 4)]  # fmt: skip
    repo = _repo(tmp_path, recs)
    report.main(["--repo", str(repo)])
    en = (repo / "reports" / "REPORT.md").read_text(encoding="utf-8")
    assert "## Track C: storage" in en
    assert "### Stages, latency, quality (medians)" in en
    # no throughput reported → QPS "–"; mJ/query c1 = (1 J GPU + 1 J CPU) / 1 000 queries
    assert ("| `c03_pgvector` | M | 2.0 | 5.0 | 30.0 | 4.0 | 3.50 | 9.25 | 12.00 | – | – | 2.00 | – | 0.9991 "
            "| 0.8477 | – |") in en  # fmt: skip
    assert "Stufen, Latenz, Qualität" in (repo / "reports" / "REPORT.de.md").read_text(encoding="utf-8")
    assert "c.hybrid" not in en  # no hybrid runs → no hybrid section


def test_track_c_sections_per_task(tmp_path: Path) -> None:
    stages = [{"name": n, "depth": 0, "t_s": t, "gpu_j": 0.5, "cpu_j": 1.5, "dram_j": None}
              for n, t in (("load", 1.0), ("ingest", 2.0), ("index", 3.0), ("query", 4.0),
                           ("legs", 4.0))]  # fmt: skip
    res = {"rows": 100_000, "notes": "", "extra": {"latency_ms": {"p50": 4.0, "p95": 5.0, "p99": 6.0},
           "throughput": {"qps_c1": 250.0, "qps_c16": 1999.6}}}  # fmt: skip
    hyb = {"ndcg_at_10": 0.83, "ndcg_at_10_kompositum": 1.0, "lex_ndcg_at_10": 0.77,
           "lex_ndcg_at_10_kompositum": 1.0, "dense_ndcg_at_10": 0.8476,
           "fusion_agreement": 0.99}  # fmt: skip
    recs = [_rec("c06_sqlite_hybrid", i, 20.0, 10.0, 90.0, track="C", task="c.hybrid", stages=stages,
                 result=res, quality=hyb) for i in range(1, 4)]  # fmt: skip
    recs += [_rec("c01_faiss_cpu", i, 15.0, 10.0, 90.0, track="C", task="c.dense", stages=stages, result=res,
                  quality={"recall_at_10": 0.9998}) for i in range(1, 4)]  # fmt: skip
    repo = _repo(tmp_path, recs)
    report.main(["--repo", str(repo)])
    en = (repo / "reports" / "REPORT.md").read_text(encoding="utf-8")
    dense, hybrid = en.split("## Track C: hybrid search")
    assert "`c01_faiss_cpu`" in dense
    assert "`c06_sqlite_hybrid`" not in dense
    assert "`c06_sqlite_hybrid`" in hybrid
    assert "`c01_faiss_cpu`" not in hybrid
    assert "| 250 | 2\u2009000 | 2.00 |" in dense  # QPS c1, QPS c16, mJ/query c1
    assert (
        "| `c06_sqlite_hybrid` | M | 1.0 | 2.0 | 3.0 | 4.0 | 4.00 | 5.00 | 6.00 | 2.00 | 0.8300 | 1.0000 "
        in hybrid
    )
    assert "lexikalische Suche" not in (repo / "reports" / "REPORT.de.md").read_text(encoding="utf-8")


def test_readme_results_block_is_generated_and_idempotent(tmp_path: Path) -> None:
    recs = [_rec("a09_polars_eager", i, 1.0, 10.0, 90.0, scale="L", rows=1_000_000) for i in (1, 2, 3)]
    recs += [_rec("a04_pandas_apply", i, 20.0, 10.0, 900.0, scale="L", rows=1_000_000) for i in (1, 2, 3)]
    repo = _repo(tmp_path, recs)
    (repo / "README.md").write_text("# X\n\n## Status\nok\n\n## Quick start\nrun\n", encoding="utf-8")
    (repo / "README.de.md").write_text("# X\n\n## Schnellstart\nlos\n", encoding="utf-8")
    report.main(["--repo", str(repo)])
    first = (repo / "README.md").read_text(encoding="utf-8")
    assert first.index("<!-- zj:results:begin -->") < first.index("## Quick start")
    assert "| 1 | `a09_polars_eager` | 1.00 | 1.00 | 100.0 |" in first
    assert "| 2 | `a04_pandas_apply` | 20.00 | 20 | 910.0 |" in first
    svg = (repo / "reports" / "figures" / "track_a_L.en.svg").read_text(encoding="utf-8")
    assert svg.startswith("<svg")
    assert "a04_pandas_apply" in svg
    assert "20,00" in (repo / "README.de.md").read_text(encoding="utf-8")
    report.main(["--repo", str(repo)])
    assert (repo / "README.md").read_text(encoding="utf-8") == first  # rewritten in place, not appended
