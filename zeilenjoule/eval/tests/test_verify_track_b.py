from __future__ import annotations

from pathlib import Path

import numpy as np

from zj_eval import verify_track_b as v


def _unit(rng: np.random.Generator, n: int) -> np.ndarray:
    x = rng.standard_normal((n, v.DIM)).astype(np.float32)
    return x / np.linalg.norm(x, axis=1, keepdims=True)


def _save(tmp: Path, name: str, a: np.ndarray) -> Path:
    p = tmp / name
    np.save(p, a)
    return p


def test_fp16_copy_passes(tmp_path: Path) -> None:
    ref = _unit(np.random.default_rng(0), 50)
    r = v.check(_save(tmp_path, "o.npy", ref.astype(np.float16)), _save(tmp_path, "r.npy", ref))
    assert r["passed"], r
    assert r["mean_cos"] > 0.9999


def test_wrong_dtype_shape_and_order_fail(tmp_path: Path) -> None:
    ref = _unit(np.random.default_rng(1), 50)
    rp = _save(tmp_path, "r.npy", ref)
    assert not v.check(_save(tmp_path, "a.npy", ref), rp)["passed"]  # float32
    assert not v.check(_save(tmp_path, "b.npy", ref[:49].astype(np.float16)), rp)["passed"]
    shuffled = ref[::-1].astype(np.float16)  # rows not in input order
    r = v.check(_save(tmp_path, "c.npy", shuffled), rp)
    assert not r["passed"]
    assert "mean cosine" in r["detail"]


def test_unnormalised_and_nan_fail(tmp_path: Path) -> None:
    ref = _unit(np.random.default_rng(2), 20)
    rp = _save(tmp_path, "r.npy", ref)
    r = v.check(_save(tmp_path, "a.npy", (ref * 2).astype(np.float16)), rp)
    assert not r["passed"]
    assert "norm" in r["detail"]
    bad = ref.astype(np.float16)
    bad[3, 0] = np.nan
    assert "NaN" in v.check(_save(tmp_path, "b.npy", bad), rp)["detail"]


def test_missing_output_fails(tmp_path: Path) -> None:
    ref = _unit(np.random.default_rng(3), 5)
    assert not v.check(tmp_path / "nope.npy", _save(tmp_path, "r.npy", ref))["passed"]
