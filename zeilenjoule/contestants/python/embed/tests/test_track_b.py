"""Track B helpers: token-budget batching, I/O contract, the shape-only proof for CPU-placed ORT nodes."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from zj_embed.b03_st_fp16_sorted import batches
from zj_embed.track_b import common, onnx_prep


def test_batches_cover_every_row_once_within_budget() -> None:
    rng = np.random.default_rng(0)
    lengths = rng.integers(1, 513, size=5000)
    seen: list[int] = []
    for idx in batches(lengths, 4096):
        width = int(lengths[idx].max())
        assert width == lengths[idx[0]]  # first row is the longest (descending order)
        assert len(idx) * width <= 4096 or len(idx) == 1
        seen.extend(int(i) for i in idx)
    assert sorted(seen) == list(range(5000))


def test_batches_single_overlong_row() -> None:
    assert [list(b) for b in batches(np.array([9000, 3]), 4096)] == [[0], [1]]


def test_read_texts_rejects_nulls(tmp_path: Path) -> None:
    p = tmp_path / "d.parquet"
    pq.write_table(pa.table({"id": ["1", "2"], "doc_text": ["a", None]}), p)  # pyright: ignore[reportUnknownMemberType]
    with pytest.raises(ValueError, match="null doc_text"):
        common.read_texts(p)


def test_write_vectors_checks_shape_and_dtype(tmp_path: Path) -> None:
    v = np.ones((3, common.DIM), dtype=np.float32)
    common.write_vectors(tmp_path / "o.npy", v, 3)
    assert np.load(tmp_path / "o.npy").dtype == np.float16
    with pytest.raises(ValueError, match="expected shape"):
        common.write_vectors(tmp_path / "o.npy", v, 4)


def _graph(tmp_path: Path, data_dependent: bool) -> Path:
    import onnx  # noqa: PLC0415
    from onnx import TensorProto, helper  # noqa: PLC0415

    x = helper.make_tensor_value_info("x", TensorProto.INT64, ["b", "t"])
    y = helper.make_tensor_value_info("y", TensorProto.INT64, None)
    nodes = [
        helper.make_node("Shape", ["x"], ["s"], name="Shape"),
        helper.make_node(
            "Constant", [], ["c"], name="C", value=helper.make_tensor("v", TensorProto.INT64, [2], [1, 1])
        ),
        helper.make_node("Equal", ["x" if data_dependent else "s", "c"], ["e"], name="Eq"),
        helper.make_node("Cast", ["e"], ["y"], name="Cast", to=TensorProto.INT64),
    ]
    path = tmp_path / "g.onnx"
    onnx.save(helper.make_model(helper.make_graph(nodes, "g", [x], [y])), str(path))
    return path


def test_shape_only_accepts_shape_arithmetic(tmp_path: Path) -> None:
    p = _graph(tmp_path, data_dependent=False)
    assert onnx_prep.shape_only(p, ["Equal (Eq)", "Cast (Cast)"]) == ["Equal (Eq)", "Cast (Cast)"]


def test_shape_only_rejects_tensor_data_on_cpu(tmp_path: Path) -> None:
    p = _graph(tmp_path, data_dependent=True)
    with pytest.raises(RuntimeError, match="tensor data"):
        onnx_prep.shape_only(p, ["Equal (Eq)"])
