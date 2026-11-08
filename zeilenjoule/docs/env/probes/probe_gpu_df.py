"""Phase-0 probe: cudf op + polars GPU engine with raise_on_fail=True."""
import json
out = {}
try:
    import cudf
    out["cudf"] = cudf.__version__
    df = cudf.DataFrame({"werk": ["Duisburg", "Essen", "Duisburg"], "kosten": [1.5, 2.0, 3.5]})
    out["cudf_groupby"] = df.groupby("werk").kosten.sum().sort_index().to_pandas().to_dict()
    s = cudf.Series(["Leckage an Pumpe", "Lager ersetzt"]); out["cudf_str_contains"] = s.str.contains("Pumpe").to_arrow().to_pylist()
except Exception as e:
    out["cudf_error"] = repr(e)
try:
    import polars as pl
    out["polars"] = pl.__version__
    lf = pl.LazyFrame({"werk": ["Duisburg", "Essen", "Duisburg"], "kosten": [1.5, 2.0, 3.5], "txt": ["Leckage Pumpe", "Lager", "Pumpe defekt"]})
    q = lf.filter(pl.col("txt").str.contains("Pumpe")).group_by("werk").agg(pl.col("kosten").sum()).sort("werk")
    out["polars_gpu_result"] = q.collect(engine=pl.GPUEngine(raise_on_fail=True)).to_dicts()
    # an op that is expected to be unsupported on GPU -> must raise, not silently fall back
    try:
        lf.with_columns(pl.col("txt").map_elements(lambda s: s.upper(), return_dtype=pl.String)).collect(engine=pl.GPUEngine(raise_on_fail=True))
        out["polars_gpu_udf"] = "ran (unexpected)"
    except Exception as e:
        out["polars_gpu_udf_raised"] = type(e).__name__
except Exception as e:
    out["polars_gpu_error"] = repr(e)
print(json.dumps(out, indent=1, default=str))
