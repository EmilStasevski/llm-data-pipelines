"""Phase-0 probe: DuckDB vss + fts extensions."""
import json, duckdb
out = {"duckdb": duckdb.__version__}
c = duckdb.connect()
for ext in ["vss", "fts"]:
    try:
        c.install_extension(ext); c.load_extension(ext); out[ext] = "loaded"
    except Exception as e:
        out[ext] = repr(e)
try:
    c.execute("CREATE TABLE t(id INT, v FLOAT[3]); INSERT INTO t VALUES (1,[1,0,0]),(2,[0,1,0]),(3,[0.9,0.1,0]);")
    c.execute("CREATE INDEX i ON t USING HNSW(v) WITH (metric='cosine')")
    out["vss_query"] = c.execute("SELECT id FROM t ORDER BY array_cosine_distance(v,[1,0,0]::FLOAT[3]) LIMIT 2").fetchall()
    out["hnsw_persistence_setting"] = c.execute("SELECT current_setting('hnsw_enable_experimental_persistence')").fetchone()[0]
except Exception as e:
    out["vss_query_error"] = repr(e)
try:
    c.execute("CREATE TABLE d(id INT, txt VARCHAR); INSERT INTO d VALUES (1,'Leckage an der Druckluftleitung'),(2,'Lager ersetzt'),(3,'Druckluftleitungsleckage');")
    c.execute("PRAGMA create_fts_index('d','id','txt', stemmer='german')")
    out["fts_query"] = c.execute("SELECT id, s FROM (SELECT id, fts_main_d.match_bm25(id,'Leckage') AS s FROM d) WHERE s IS NOT NULL ORDER BY s DESC").fetchall()
except Exception as e:
    out["fts_error"] = repr(e)
print(json.dumps(out, indent=1, default=str))
