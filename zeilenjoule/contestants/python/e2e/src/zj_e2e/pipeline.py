"""Track E pipelines (spec/track_e.md, ADR 0020): raw CSV → index → 300 routed questions.

    python -m zj_e2e.pipeline --pipeline e02_modern_embedded --input <csv> --output <out.jsonl>

Stages: ingest (a Track A contestant's CLI) → embed (a Track B contestant's CLI, documents + the 300 question
texts in one run) → label (LLM labels 2 000 sampled rows' schadensart, a softmax classifier on the vectors
labels the rest) → index (store) → answer (router call per question, SQL or hybrid search, answer call for
text lookups). e05_best_of_tracks reads the fastest counted contestant per track from results/zj.duckdb.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import yaml

from zj_e2e import sqlsafe
from zj_e2e.stores import COLUMNS, DuckDBStore, FaissSqliteStore, PostgresStore, QdrantDuckStore, Store
from zj_llm import extract as llm
from zj_llm.server import image_digest, post, running

REPO = Path(__file__).resolve().parents[5]
DIM = 1024


@dataclass(frozen=True)
class Pipeline:
    name: str
    track_a: str  # contestant id (config/contestants.yaml)
    track_b: str
    store: str  # faiss_sqlite | duckdb | postgres | qdrant_duckdb
    engine: str  # ollama | llamacpp | vllm
    model: str  # zj_llm.extract.MODELS key
    concurrency: int  # best N of the Track D sweep for the engine


PIPELINES = {
    "e01_classic_python": Pipeline(
        "e01_classic_python",
        "a05_pandas_vectorized",
        "b01_st_fp32",
        "faiss_sqlite",
        "ollama",
        "qwen35_9b_q4",
        16,  # 64 slots × 4 096 tokens do not fit: Ollama would split CPU/GPU (ADR 0020)
    ),
    "e02_modern_embedded": Pipeline(
        "e02_modern_embedded", "a10_polars_lazy", "b04_ort_cuda", "duckdb", "llamacpp", "qwen35_9b_q4", 16
    ),
    "e03_postgres_only": Pipeline(
        "e03_postgres_only", "a16_rs_arrow_rayon", "b06_tei", "postgres", "vllm", "qwen35_9b_awq", 64
    ),
}
SERVED_B = {"b06_tei": "TEI", "b07_vllm_pooling": "vLLM (pooling)", "b08_llamacpp_embed": "llama.cpp (embed)"}
STORE_SERVER = {"postgres": "PostgreSQL", "qdrant_duckdb": "Qdrant"}
LLM_SERVER = {"ollama": "Ollama", "llamacpp": "llama.cpp", "vllm": "vLLM"}


def services(p: Pipeline) -> list[str]:
    """Long-running server processes a production deployment of the pipeline needs (PLAN §11.1 rule 5)."""
    out = [STORE_SERVER[p.store]] if p.store in STORE_SERVER else []
    if p.track_b in SERVED_B:
        out.append(SERVED_B[p.track_b])
    return [*out, LLM_SERVER[p.engine]]


STORE_OF_C = {
    "c08_qdrant_hybrid": "qdrant_duckdb",
    "c05_duckdb_hybrid": "duckdb",
    "c03_pgvector_hybrid": "postgres",
}
ENGINE_OF_D = {
    "d01": ("ollama", "qwen35_9b_q4", 16),
    "d02": ("llamacpp", "qwen35_9b_q4", 16),
    "d03": ("vllm", "qwen35_9b_awq", 64),
}


def fastest(con: Any, track: str, task: str, scale: str, allowed: Callable[[str], bool]) -> str:
    rows = con.execute(
        "WITH latest AS (SELECT contestant, arg_max(session_id, ts) AS sid FROM ok_runs "
        "WHERE track = ? AND task = ? AND scale = ? GROUP BY ALL) "
        "SELECT contestant, median(wall_s) AS w FROM ok_runs JOIN latest USING (contestant) "
        "WHERE track = ? AND task = ? AND scale = ? AND session_id = sid AND NOT anti_pattern "
        "GROUP BY contestant ORDER BY w",
        [track, task, scale, track, task, scale],
    ).fetchall()
    for c, _ in rows:
        if allowed(str(c)):
            return str(c)
    raise RuntimeError(f"no counted contestant for {track}/{task}/{scale}")


def best_of_tracks(repo: Path) -> tuple[Pipeline, dict[str, str]]:
    """e5: fastest counted contestant per track (Track D: fastest passing the winner gate with Qwen3.5-9B)."""
    import duckdb  # noqa: PLC0415

    con = duckdb.connect(str(repo / "results" / "zj.duckdb"), read_only=True)
    a = fastest(con, "A", "a.full", "L", lambda c: True)
    b = fastest(con, "B", "b.embed", "M", lambda c: True)
    c = fastest(con, "C", "c.hybrid", "M", lambda c: c in STORE_OF_C)
    d = fastest(con, "D", "d.extract", "D2k", lambda c: c[:3] in ENGINE_OF_D and "qwen35_9b" in c)
    con.close()
    engine, model, n = ENGINE_OF_D[d[:3]]
    p = Pipeline("e05_best_of_tracks", a, b, STORE_OF_C[c], engine, model, n)
    return p, {"track_a": a, "track_b": b, "track_c": c, "track_d": d}


# ------------------------------------------------------------------ contestant CLIs
def contestant_cmd(cid: str, inp: Path, out: Path, threads: int) -> list[str]:
    cfg = cast("dict[str, Any]", yaml.safe_load((REPO / "config" / "contestants.yaml").read_text("utf-8")))
    entry = next(c for c in cfg["contestants"] if c["id"] == cid)
    subst = {"{input}": str(inp), "{output}": str(out), "{threads}": str(threads)}
    return [*entry["cmd"], *(subst.get(a, a) for a in entry["args"])]


def run_cli(cmd: list[str]) -> dict[str, Any]:
    """Run a contestant CLI (same contract as in its own track) and return its wall time and notes."""
    t0 = time.perf_counter()
    p = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, check=False)
    # the child's ZJMARK lines would reach the harness only now (wrong timestamps): drop them, the outer
    # stage (ingest / embed) times the whole call
    sys.stderr.write(
        "\n".join(x for x in p.stderr[-20_000:].splitlines() if not x.startswith("ZJMARK")) + "\n"
    )
    if p.returncode:
        raise RuntimeError(f"{shlex.join(cmd)} exited {p.returncode}:\n{p.stdout[-3000:]}{p.stderr[-3000:]}")
    res = next(
        (json.loads(x.split(" ", 1)[1]) for x in p.stdout.splitlines() if x.startswith("ZJRESULT ")), {}
    )
    return {"wall_s": round(time.perf_counter() - t0, 2), "notes": res.get("notes")}


# ------------------------------------------------------------------ LLM
class LLM:
    def __init__(self, p: Pipeline, prompts: dict[str, Any]) -> None:
        self.m = replace(llm.MODELS[p.model], ctx=4096)  # router prompt + examples ≈ 1 600 tokens
        self.engine = p.engine
        self.n = p.concurrency
        self.eng = llm.ENGINES[p.engine](self.m, p.concurrency)
        self.max_tokens = int(prompts["max_tokens"])
        self.tokens = 0

    def __call__(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> tuple[str, float]:
        t0 = time.perf_counter()
        port = self.eng.server.port
        if self.engine == "ollama":
            r = post(
                port,
                "/api/chat",
                {
                    "model": self.m.ollama,
                    "messages": messages,
                    "stream": False,
                    "format": schema,
                    "think": False,
                    "options": {
                        "temperature": 0,
                        "seed": 0,
                        "num_predict": self.max_tokens,
                        "num_ctx": self.m.ctx,
                    },
                },
            )
            text, toks = r["message"].get("content") or "", int(r.get("eval_count") or 0)
        else:
            r = post(
                port,
                "/v1/chat/completions",
                {
                    "model": "model",
                    "messages": messages,
                    "temperature": 0,
                    "seed": 0,
                    "max_tokens": self.max_tokens,
                    **llm.response_format(schema),
                    **llm.openai_extras(self.m),
                },
            )
            text, toks = r["choices"][0]["message"].get("content") or "", int(r["usage"]["completion_tokens"])
        self.tokens += toks
        return text, (time.perf_counter() - t0) * 1e3

    def map(self, items: list[Any], fn: Callable[[Any], Any]) -> list[Any]:
        with ThreadPoolExecutor(max_workers=self.n) as ex:
            return list(ex.map(fn, items))


def loads(text: str) -> dict[str, Any] | None:
    try:
        obj = json.loads(text.strip())
    except (json.JSONDecodeError, ValueError):
        return None
    return cast("dict[str, Any]", obj) if isinstance(obj, dict) else None


# ------------------------------------------------------------------ label (distillation)
def softmax_fit(x: np.ndarray, y: np.ndarray, classes: int, l2: float = 1e-4, iters: int = 400) -> np.ndarray:
    """Multinomial logistic regression (bias via a constant column), full-batch Adam; returns W (d+1, C)."""
    xb = np.hstack([x, np.ones((len(x), 1), dtype=np.float32)])
    w = np.zeros((xb.shape[1], classes), dtype=np.float64)
    m, v = np.zeros_like(w), np.zeros_like(w)
    onehot = np.eye(classes)[y]
    for t in range(1, iters + 1):
        z = xb @ w
        z -= z.max(axis=1, keepdims=True)
        prob = np.exp(z)
        prob /= prob.sum(axis=1, keepdims=True)
        g = xb.T @ (prob - onehot) / len(xb) + 2 * l2 * w
        m = 0.9 * m + 0.1 * g
        v = 0.999 * v + 0.001 * g * g
        w -= 0.05 * (m / (1 - 0.9**t)) / (np.sqrt(v / (1 - 0.999**t)) + 1e-8)
    return w


def predict(x: np.ndarray, w: np.ndarray, chunk: int = 100_000) -> np.ndarray:
    out = np.empty(len(x), dtype=np.int64)
    for s in range(0, len(x), chunk):
        xs = np.asarray(x[s : s + chunk], dtype=np.float32)
        out[s : s + len(xs)] = (xs @ w[:-1].astype(np.float32) + w[-1].astype(np.float32)).argmax(axis=1)
    return out


def label(
    table: pa.Table, vecs: np.ndarray, ask: LLM, prompts: dict[str, Any]
) -> tuple[list[str], dict[str, Any]]:
    cfg = prompts["label"]
    classes = list(cfg["schema"]["properties"]["schadensart"]["enum"])
    rng = np.random.default_rng(int(cfg["seed"]))
    sample = rng.choice(table.num_rows, size=int(cfg["sample"]), replace=False)
    docs = table.column("doc_text").to_pylist()

    def one(i: int) -> str | None:
        msgs = [{"role": "system", "content": cfg["system"]}, {"role": "user", "content": docs[i]}]
        o = loads(ask(msgs, cfg["schema"])[0])
        return o.get("schadensart") if o and o.get("schadensart") in classes else None

    labels = ask.map([int(i) for i in sample], one)
    keep = [(int(i), classes.index(lab)) for i, lab in zip(sample, labels, strict=True) if lab is not None]
    idx = np.array([i for i, _ in keep])
    y = np.array([c for _, c in keep])
    n_hold = len(keep) // 10
    x = np.asarray(vecs[idx], dtype=np.float32)
    w = softmax_fit(x[n_hold:], y[n_hold:], len(classes))
    hold_acc = float((predict(x[:n_hold], w) == y[:n_hold]).mean()) if n_hold else 0.0
    w = softmax_fit(x, y, len(classes))  # final model on all labelled rows
    pred = predict(vecs, w)
    out = [classes[int(c)] for c in pred]
    for i, c in zip(idx, y, strict=True):  # the LLM's own labels win on the sampled rows
        out[int(i)] = classes[int(c)]
    return out, {
        "label_sample": len(sample),
        "label_valid": len(keep),
        "label_classifier_holdout_acc": round(hold_acc, 4),
    }


# ------------------------------------------------------------------ answer
def answer_all(  # noqa: PLR0912  (one branch per route outcome)
    questions: list[dict[str, str]], qvecs: np.ndarray, store: Store, ask: LLM, prompts: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    r, a = prompts["router"], prompts["answer"]
    k = int(prompts["search"]["k"])
    log: dict[str, Any] = {
        "sql_executed": 0,
        "sql_rejected": 0,
        "sql_failed": 0,
        "sql_unsafe_executed": 0,
        "sql_log": [],
    }
    routed = ask.map(
        questions,
        lambda q: ask(
            [{"role": "system", "content": r["system"]}, *r["shots"], {"role": "user", "content": q["text"]}],
            r["schema"],
        ),
    )
    out: list[dict[str, Any]] = []
    second: list[tuple[int, list[dict[str, str]], list[str]]] = []
    for j, (q, (text, ms)) in enumerate(zip(questions, routed, strict=True)):
        t0 = time.perf_counter()
        o = loads(text) or {}
        rec: dict[str, Any] = {
            "qid": q["qid"],
            "route": o.get("route"),
            "sql": o.get("sql"),
            "answer": None,
            "cited_ids": [],
            "router_ms": round(ms, 1),
        }
        if o.get("route") == "sql" and o.get("sql"):
            chk = sqlsafe.check(str(o["sql"]), store.dialect)
            entry: dict[str, Any] = {"qid": q["qid"], "sql": o["sql"], "ok": chk.ok, "reason": chk.reason}
            if not chk.ok:
                log["sql_rejected"] += 1
            else:
                try:
                    cols, rows = store.sql(chk.sql)
                    log["sql_executed"] += 1
                    if o.get("antwort_aus") == "text" and ("meldungstext" in cols or "meldung_id" in cols):
                        top = rows[: int(a["max_rows"])]
                        mid = cols.index("meldung_id") if "meldung_id" in cols else None
                        ids = [str(row[mid]) for row in top] if mid is not None else []
                        ctx = "\n".join(
                            f"[{row[mid] if mid is not None else j + 1}] "
                            + " | ".join(str(v) for c, v in zip(cols, row, strict=True) if c != "meldung_id")
                            for j, row in enumerate(top)
                        )
                        rec["cited_ids"] = ids
                        if top:
                            second.append(
                                (
                                    j,
                                    [
                                        {"role": "system", "content": a["system"]},
                                        {
                                            "role": "user",
                                            "content": f"Frage: {q['text']}\n\nMeldungen:\n{ctx}",
                                        },
                                    ],
                                    ids,
                                )
                            )
                    else:
                        rec["answer"] = rows
                except Exception as e:
                    log["sql_failed"] += 1
                    entry["error"] = str(e)[:300]
            log["sql_log"].append(entry)
        elif o.get("route") == "search":
            f = {key: o.get(key) for key in ("datum_von", "datum_bis")}
            for key, allowed in prompts["catalog"].items():  # self-query filters: catalogue values only
                f[key] = o.get(key) if o.get(key) in allowed else None
            rec["cited_ids"] = store.search(str(o.get("suchtext") or q["text"]), qvecs[j], f, k)
        rec["exec_ms"] = round((time.perf_counter() - t0) * 1e3, 1)
        out.append(rec)

    def ans(
        item: tuple[int, list[dict[str, str]], list[str]],
    ) -> tuple[int, dict[str, Any] | None, float, list[str]]:
        j, msgs, ids = item
        text, ms = ask(msgs, a["schema"])
        return j, loads(text), ms, ids

    for j, o, ms, ids in ask.map(second, ans):
        out[j]["answer_ms"] = round(ms, 1)
        if o:
            out[j]["answer"] = o.get("antwort")
            cited = [str(x) for x in (o.get("zitiert") or []) if str(x) in ids]
            out[j]["cited_ids"] = cited or ids  # no id selected by the SQL → nothing to cite
    for rec in out:
        rec["latency_ms"] = round(rec["router_ms"] + rec["exec_ms"] + rec.get("answer_ms", 0.0), 1)
    return out, log


# ------------------------------------------------------------------ main
def make_store(p: Pipeline, work: Path, threads: int) -> Store:
    if p.store == "duckdb":
        return DuckDBStore(work / "e2e.duckdb")
    if p.store == "faiss_sqlite":
        return FaissSqliteStore()
    if p.store == "postgres":
        return PostgresStore(threads)
    return QdrantDuckStore(work / "e2e_sql.duckdb", threads)


def main(argv: list[str] | None = None) -> int:  # noqa: PLR0915
    from zjmark import result, stage  # noqa: PLC0415

    ap = argparse.ArgumentParser(prog="python -m zj_e2e.pipeline")
    ap.add_argument("--pipeline", choices=[*PIPELINES, "e05_best_of_tracks"], required=True)
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--threads", type=int, default=0)
    ap.add_argument("--questions", type=int, default=0, help="debug: first N questions only")
    a = ap.parse_args(argv)
    threads = a.threads or os.cpu_count() or 1
    composed: dict[str, str] = {}
    if a.pipeline == "e05_best_of_tracks":
        p, composed = best_of_tracks(REPO)
    else:
        p = PIPELINES[a.pipeline]
    scale = a.input.parent.name
    edir = REPO / "data" / "track_e" / scale
    prompts = json.loads((edir / "prompts.json").read_text("utf-8"))
    questions = [json.loads(x) for x in (edir / "questions.jsonl").read_text("utf-8").splitlines() if x]
    questions = questions[: a.questions] if a.questions else questions
    work = a.output.parent
    info: dict[str, Any] = {"pipeline": p.name, "composition": composed or None}
    with stage("ingest"):
        info["ingest"] = run_cli(contestant_cmd(p.track_a, a.input, work / "track_a.parquet", threads))
        table = pq.read_table(work / "track_a.parquet")
        table = table.rename_columns(
            [
                {
                    "komponente_rule": "komponente",
                    "sicherheitsrelevant_rule": "sicherheitsrelevant",
                    "text_norm": "meldungstext",
                }.get(c, c)
                for c in table.column_names
            ]
        )
    with stage("embed"):
        ids = table.column("meldung_id").to_pylist() + [q["qid"] for q in questions]
        texts = table.column("doc_text").to_pylist() + [q["text"] for q in questions]
        pq.write_table(pa.table({"id": ids, "doc_text": texts}), work / "docs.parquet")
        info["embed"] = run_cli(contestant_cmd(p.track_b, work / "docs.parquet", work / "vec.npy", threads))
        allvec = np.load(work / "vec.npy", mmap_mode="r")
        vecs, qvecs = allvec[: table.num_rows], np.asarray(allvec[table.num_rows :], dtype=np.float32)
    ask = LLM(p, prompts)
    store = make_store(p, work, threads)
    with ExitStack() as stack:
        with stage("label"):
            evidence = stack.enter_context(running(ask.eng.server))
            ask.eng.setup()
            ask([{"role": "user", "content": "ok"}], prompts["label"]["schema"])  # model on the GPU
            evidence = {**evidence, **ask.eng.gpu_check()}
            sa, info["label"] = label(table, vecs, ask, prompts)
            table = table.append_column("schadensart", pa.array(sa))
        with stage("index"):
            if hasattr(store, "start"):
                cast("Any", store).start()
            stack.callback(store.close)
            info["index"] = store.build(table.select([*COLUMNS, "doc_text"]), vecs)
        with stage("answer"):
            tokens0 = ask.tokens
            out, log = answer_all(questions, qvecs, store, ask, prompts)
            info["answer_tokens"] = ask.tokens - tokens0
        with stage("write"):
            a.output.write_text(
                "".join(json.dumps(o, ensure_ascii=False, default=str) + "\n" for o in out), "utf-8"
            )
            info |= {k: v for k, v in log.items()}
            (work / "ingest.json").write_text(
                json.dumps(info, ensure_ascii=False, indent=1, default=str), "utf-8"
            )
    lat = sorted(o["latency_ms"] for o in out)
    result(
        rows=table.num_rows,
        notes=f"{p.name}: {p.track_a} → {p.track_b} → {p.store} → {p.engine} {p.model}",
        extra={
            "engine": "cuda (server)",
            "pipeline": p.name,
            "composition": {
                "track_a": p.track_a,
                "track_b": p.track_b,
                "store": p.store,
                "engine": p.engine,
                "model": p.model,
                "concurrency": p.concurrency,
                **({"from_results": composed} if composed else {}),
            },
            "services": services(p),
            "llm_image": image_digest(ask.eng.server.image),
            **evidence,
            "latency_ms": {
                "p50": lat[len(lat) // 2],
                "p95": lat[int(len(lat) * 0.95)],
                "p99": lat[int(len(lat) * 0.99)],
            },
            "questions": len(out),
            "answer_tokens": info["answer_tokens"],
            **info["label"],
            "sql": {k: log[k] for k in ("sql_executed", "sql_rejected", "sql_failed", "sql_unsafe_executed")},
        },
    )
    for f in ("track_a.parquet", "docs.parquet", "vec.npy", "e2e.duckdb", "e2e_sql.duckdb"):
        (work / f).unlink(missing_ok=True)  # GBs at L; the harness keeps only out.jsonl + ingest.json
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
