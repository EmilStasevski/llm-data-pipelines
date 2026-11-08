"""c02 helpers that need no GPU."""

from __future__ import annotations

import pyarrow as pa

from zj_gpu_vs.c02_cuvs_cagra import _codes  # pyright: ignore[reportPrivateUsage]


def test_codes_map_nulls_to_minus_one() -> None:
    idx, lookup = _codes(pa.chunked_array([["Essen", None, "Bonn", "Essen"]]))
    assert idx.tolist() == [lookup["Essen"], -1, lookup["Bonn"], lookup["Essen"]]
    assert set(lookup) == {"Essen", "Bonn"}
