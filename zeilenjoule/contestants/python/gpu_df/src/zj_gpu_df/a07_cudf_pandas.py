"""Track A `a.full` contestant a07_cudf_pandas: the *unchanged* a05 code under cudf.pandas (PLAN §8).

How "same code as a05" is guaranteed: a05's module tree (zj_df.a05_pandas_vectorized +
zj_df.track_a.pandas_impl/common) is imported from contestants/python/df/src. It only needs pandas,
numpy, pyarrow and zjmark, which this env has. cudf.pandas is installed *before* pandas is imported.

Fallback accounting (CLAUDE.md rule 6): cudf.pandas executes every proxied pandas call through
`_fast_slow_function_call`, which returns `(result, ran_on_gpu)`. A thin counting wrapper records
GPU and CPU (fallback) calls per function: a few hundred calls per run, so the overhead is negligible,
unlike the trace-based `--profile` (sys.settrace on every line). The counts go into the ZJRESULT extra:
`engine` = "gpu" only if no call fell back, else "gpu+cpu_fallback", plus `fallback_ops`.
"""

from __future__ import annotations

import sys
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

DF_SRC = Path(__file__).resolve().parents[3] / "df" / "src"


def _qual(x: Any) -> str:
    x = getattr(x, "_fsproxy_slow", x)  # proxies → the pandas object they stand for
    t = x if isinstance(x, type) else None
    return str(getattr(x, "__qualname__", None) or (t or type(x)).__qualname__)


def _op_name(func: Any, args: tuple[Any, ...]) -> str:
    """args = (transfer_block, *call args) as passed to _fast_slow_function_call."""
    fn = getattr(func, "__name__", "")
    if fn == "call_operator" and len(args) > 1:
        return _qual(args[1])  # the method/function proxy being called
    if fn == "getattr" and len(args) > 2:
        owner = args[1] if isinstance(args[1], type) else type(args[1])
        return f"{_qual(owner)}.{args[2]}"
    if fn == "<lambda>" and len(args) > 1 and isinstance(args[1], type):
        return f"{_qual(args[1])}.__init__"
    return str(getattr(func, "__qualname__", None) or type(func).__qualname__)


def _install_counters() -> tuple[Counter[str], Counter[str]]:
    import cudf.pandas  # noqa: PLC0415

    cudf.pandas.install()  # must happen before anything imports pandas
    from cudf.pandas import fast_slow_proxy  # noqa: PLC0415
    from cudf.pandas._wrappers import numpy as w_numpy  # noqa: PLC0415
    from cudf.pandas._wrappers import pandas as w_pandas  # noqa: PLC0415

    gpu: Counter[str] = Counter()
    cpu: Counter[str] = Counter()
    original: Callable[..., tuple[Any, bool]] = fast_slow_proxy._fast_slow_function_call  # pyright: ignore[reportPrivateUsage]

    def counted(func: Any, *args: Any, **kwargs: Any) -> tuple[Any, bool]:
        result, fast = original(func, *args, **kwargs)
        (gpu if fast else cpu)[_op_name(func, args)] += 1
        return result, fast

    for mod in (fast_slow_proxy, w_pandas, w_numpy):
        setattr(mod, "_fast_slow_function_call", counted)  # noqa: B010 (module globals, looked up per call)
    return gpu, cpu


def main(argv: list[str] | None = None) -> int:
    gpu, cpu = _install_counters()
    sys.path.insert(0, str(DF_SRC))
    import cudf  # noqa: PLC0415
    import pandas as pd  # noqa: PLC0415  (now the cudf.pandas proxy module)

    from zj_df import a05_pandas_vectorized  # noqa: PLC0415  (the exact a05 code)
    from zjmark import result  # noqa: PLC0415

    r = a05_pandas_vectorized.run(argv, "a07_cudf_pandas", "a05 code under cudf.pandas")
    extra: dict[str, object] = dict(cast("dict[str, object]", r["extra"]))
    extra.update(
        {
            "engine": "gpu" if not cpu else "gpu+cpu_fallback",
            "gpu_calls": sum(gpu.values()),
            "cpu_calls": sum(cpu.values()),
            "fallback_ops": sorted(cpu),
            "fallback_counts": dict(cpu.most_common()),
            "cudf": cudf.__version__,
            "pandas": pd.__version__,
            "same_code_as": "a05_pandas_vectorized",
        }
    )
    result(rows=r["rows"], notes=str(r["notes"]), extra=extra)  # pyright: ignore[reportArgumentType]
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
