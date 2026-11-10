"""Track D `d.extract` contestants (spec/track_d.md): one runner, three servers (Ollama, llama.cpp, vLLM).

`python -m zj_llm.extract --engine llamacpp --model qwen35_9b_q4 --concurrency 16 --shots 0 \
     --input … --output …`

Stages: `load` (container start, model load, GPU proof, one untimed request that also warms the prefix
cache), `extract` (all rows, `concurrency` requests in flight, rows in input order in the output), `write`,
`ttft` (20 rows streamed one at a time: time to first token, outside the comparison). Every engine gets the
same messages (data/track_d/<scale>/prompts.<k>shot.jsonl), temperature 0, seed 0, max_tokens 256 and the
JSON schema as constrained decoding; reasoning is switched off the way each model family documents it.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zj_llm.server import REPO, Server, exec_in, image_digest, post, post_stream, running, uid_gid

MAX_TOKENS = 256
SEED = 0
TTFT_ROWS = 20
MIN_VRAM_MB = 1500  # smallest model (4B Q4) ≈ 2.7 GB of weights
SCHEMA = json.loads((REPO / "spec" / "schema_stoermeldung.json").read_text("utf-8"))
GGUF = REPO / "models" / "gguf"
VLLM = REPO / "models" / "vllm"

LLAMACPP_IMAGE = (
    "ghcr.io/ggml-org/llama.cpp:server-cuda-b12169"
    "@sha256:5bb779a449a18d8f4333e9814e887746342fe555c528f5eabc92b2093e32673a"
)
VLLM_IMAGE = (
    "vllm/vllm-openai:v0.33.0@sha256:d825baf9ce4b1b6bfc5ecc7b79880c7e6748c65a72d697ce3d47607aafa84c6e"
)
OLLAMA_IMAGE = "ollama/ollama:0.40.15@sha256:0e59999e0e8f9fd4e704bd788a89b7e4cad399367fb1365f8c102ac662288761"


def response_format(schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "stoermeldung", "schema": schema, "strict": True},
        }
    }


# Qwen 3.5 is multimodal: text-only rows need no vision encoder memory profiling
TEXT_ONLY = ("--limit-mm-per-prompt", '{"image": 0, "video": 0}')


@dataclass(frozen=True)
class Model:
    gguf: str | None = None  # path under models/gguf
    vllm: str | None = None  # directory under models/vllm
    ollama: str | None = None  # Ollama registry tag (pulled into models/ollama)
    family: str = "plain"  # qwen | gemma | gptoss | plain (how reasoning is switched off)
    ctx: int = 2048  # context per sequence: 3-shot prompt ≈ 1 400 tokens + 256 output
    vllm_mem: float = 0.88  # vLLM --gpu-memory-utilization (larger weights need more of the 16 GB)
    vllm_args: tuple[str, ...] = ()  # extra vLLM flags
    grammar: bool = True  # False: llama.cpp cannot build a grammar for this vocabulary → unconstrained


MODELS: dict[str, Model] = {
    "qwen35_9b_q4": Model(gguf="Qwen3.5-9B-GGUF/Qwen3.5-9B-Q4_K_M.gguf", ollama="qwen3.5:9b", family="qwen"),
    "qwen35_9b_q8": Model(
        gguf="Qwen3.5-9B-GGUF/Qwen3.5-9B-Q8_0.gguf", ollama="qwen3.5:9b-q8_0", family="qwen"
    ),
    "qwen35_9b_awq": Model(vllm="Qwen3.5-9B-AWQ-4bit", family="qwen", vllm_args=TEXT_ONLY),
    "qwen35_9b_fp8": Model(vllm="Qwen3.5-9B-FP8-dynamic", family="qwen", vllm_mem=0.95, vllm_args=TEXT_ONLY),
    "qwen35_4b_q4": Model(gguf="Qwen3.5-4B-GGUF/Qwen3.5-4B-Q4_K_M.gguf", ollama="qwen3.5:4b", family="qwen"),
    "gemma4_12b_q4": Model(gguf="gemma-4-12b-it-GGUF/gemma-4-12b-it-Q4_K_M.gguf", family="gemma"),
    "gemma4_12b_q8": Model(gguf="gemma-4-12b-it-GGUF/gemma-4-12b-it-Q8_0.gguf", family="gemma"),
    "gemma4_e4b_q4": Model(gguf="gemma-4-E4B-it-qat-q4_0-gguf/gemma-4-E4B_q4_0-it.gguf", family="gemma"),
    "eurollm_9b_q8": Model(
        gguf="EuroLLM-9B-Instruct-2512-GGUF/EuroLLM-9B-Instruct-2512.Q8_0.gguf", grammar=False
    ),
    "teuken_7b_q8": Model(
        gguf="Teuken-7B-instruct-commercial-v0.4-GGUF/Teuken-7B-instruct-commercial-v0.4.Q8_0.gguf",
        grammar=False,
    ),
    "ministral3_14b_q4": Model(
        gguf="Ministral-3-14B-Instruct-2512-GGUF/Ministral-3-14B-Instruct-2512-Q4_K_M.gguf"
    ),
    "gptoss_20b_mxfp4": Model(  # vLLM: OOM on 16 GB (docs/COMPAT.md)
        gguf="gpt-oss-20b-GGUF/gpt-oss-20b-MXFP4.gguf",
        vllm="gpt-oss-20b",
        ollama="gpt-oss:20b",
        vllm_mem=0.95,
        family="gptoss",
    ),
}


def openai_extras(m: Model) -> dict[str, Any]:
    """Request fields that switch reasoning off (or to its minimum) for the OpenAI-compatible servers."""
    if m.family in {"qwen", "gemma"}:
        return {"chat_template_kwargs": {"enable_thinking": False}}
    if m.family == "gptoss":  # no off switch: lowest documented effort
        return {"reasoning_effort": "low", "chat_template_kwargs": {"reasoning_effort": "low"}}
    return {}


@dataclass(frozen=True)
class Engine:
    server: Server
    request: Callable[[list[dict[str, str]]], dict[str, Any]]  # messages → {text, completion_tokens, finish}
    ttft: Callable[[list[dict[str, str]]], float]
    setup: Callable[[], None]
    info: dict[str, Any]
    # after the first request: proof that the model runs fully on the GPU (rule 6); returns evidence
    gpu_check: Callable[[], dict[str, Any]] = lambda: {}


def _openai(
    port: int, name: str, m: Model, schema: dict[str, Any]
) -> tuple[Callable[..., dict[str, Any]], Callable[..., float]]:
    def body(messages: list[dict[str, str]]) -> dict[str, Any]:
        return {
            "model": name,
            "messages": messages,
            "temperature": 0,
            "seed": SEED,
            "max_tokens": MAX_TOKENS,
            **(response_format(schema) if m.grammar else {}),
            **openai_extras(m),
        }

    def request(messages: list[dict[str, str]]) -> dict[str, Any]:
        r = post(port, "/v1/chat/completions", body(messages))
        c = r["choices"][0]
        return {
            "text": c["message"].get("content") or "",
            "completion_tokens": r["usage"]["completion_tokens"],
            "prompt_tokens": r["usage"]["prompt_tokens"],
            "finish_reason": c.get("finish_reason"),
        }

    def ttft(messages: list[dict[str, str]]) -> float:
        return post_stream(port, "/v1/chat/completions", {**body(messages), "stream": True})

    return request, ttft


def llamacpp(m: Model, n: int, schema: dict[str, Any] = SCHEMA) -> Engine:
    if m.gguf is None:
        raise SystemExit("model has no GGUF file")
    port = 18191
    args = [
        "-m",
        f"/models/{m.gguf}",
        "-ngl",
        "99",
        "-dev",
        "CUDA0",
        "--fit",
        "off",
        "-fa",
        "on",
        "-np",
        str(n),
        "-c",
        str(m.ctx * n),
        "--jinja",
        *(
            () if m.family == "gptoss" else ("--reasoning-budget", "0")
        ),  # no thinking (Gemma 4 thinks under a grammar)
        "--seed",
        str(SEED),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]
    srv = Server(
        name="zj-d02-llamacpp",
        image=LLAMACPP_IMAGE,
        args=args,
        port=port,
        min_vram_mb=1500,
        mounts=[(GGUF, "/models")],
        extra_docker=["--user", uid_gid()],
    )
    request, ttft = _openai(port, "model", m, schema)
    return Engine(srv, request, ttft, lambda: None, {"gguf": m.gguf, "slots": n, "constrained": m.grammar})


def vllm(m: Model, n: int, schema: dict[str, Any] = SCHEMA) -> Engine:
    if m.vllm is None:
        raise SystemExit("model has no vLLM (HF-format) directory")
    port = 18192
    args = [
        "--model",
        "/model",
        "--served-model-name",
        "model",
        "--max-model-len",
        str(m.ctx),
        "--max-num-seqs",
        str(n),
        "--enable-prefix-caching",
        "--gpu-memory-utilization",
        str(m.vllm_mem),
        "--seed",
        str(SEED),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        *m.vllm_args,
    ]
    srv = Server(
        name="zj-d03-vllm",
        image=VLLM_IMAGE,
        args=args,
        port=port,
        min_vram_mb=3000,
        mounts=[(VLLM / m.vllm, "/model")],
        rw_mounts=[(REPO / "models" / "cache" / "vllm", "/cache")],
        env={"HOME": "/cache", "HF_HUB_OFFLINE": "1", "VLLM_NO_USAGE_STATS": "1", "DO_NOT_TRACK": "1"},
        extra_docker=["--user", uid_gid(), "--ipc=host"],
    )
    request, ttft = _openai(port, "model", m, schema)
    return Engine(srv, request, ttft, lambda: None, {"hf_dir": m.vllm, "max_num_seqs": n})


def ollama(m: Model, n: int, schema: dict[str, Any] = SCHEMA) -> Engine:
    if m.ollama is None:
        raise SystemExit("model has no Ollama tag")
    port = 18193
    srv = Server(
        name="zj-d01-ollama",
        image=OLLAMA_IMAGE,
        args=[],
        port=port,
        min_vram_mb=0,
        health="/",
        rw_mounts=[(REPO / "models" / "ollama", "/root/.ollama")],
        env={
            "OLLAMA_HOST": f"127.0.0.1:{port}",
            "OLLAMA_NUM_PARALLEL": str(n),
            "OLLAMA_FLASH_ATTENTION": "1",
            "OLLAMA_KV_CACHE_TYPE": "f16",
            "OLLAMA_CONTEXT_LENGTH": str(m.ctx),
            "OLLAMA_KEEP_ALIVE": "-1",
            "OLLAMA_NOPRUNE": "1",
        },
    )
    think: dict[str, Any] = {"think": "low"} if m.family == "gptoss" else {"think": False}

    def body(messages: list[dict[str, str]], stream: bool) -> dict[str, Any]:
        return {
            "model": m.ollama,
            "messages": messages,
            "stream": stream,
            "format": schema,
            **think,
            "options": {"temperature": 0, "seed": SEED, "num_predict": MAX_TOKENS, "num_ctx": m.ctx},
        }

    def request(messages: list[dict[str, str]]) -> dict[str, Any]:
        r = post(port, "/api/chat", body(messages, False))
        return {
            "text": r["message"].get("content") or "",
            "completion_tokens": r.get("eval_count"),
            "prompt_tokens": r.get("prompt_eval_count"),
            "finish_reason": r.get("done_reason"),
        }

    def setup() -> None:  # model must already be pulled (task trackD:pull); load it onto the GPU
        if (m.ollama or "") not in exec_in(srv, "ollama", "list"):
            raise RuntimeError(f"{m.ollama} not pulled into models/ollama (task trackD:pull)")

    def gpu_check() -> dict[str, Any]:
        """Ollama silently splits a model across CPU and GPU when the KV cache does not fit (e.g. 64 slots ×
        4 096 tokens of Qwen3.5-9B: 25 %/75 % CPU/GPU) → fail loudly unless `ollama ps` says 100 % GPU."""
        ps = exec_in(srv, "ollama", "ps")
        line = next((x for x in ps.splitlines() if x.startswith(str(m.ollama))), "")
        if "100% GPU" not in line:
            raise RuntimeError(
                f"Ollama does not run {m.ollama} fully on the GPU (rule 6): {line.strip() or ps}"
            )
        return {"ollama_ps": " ".join(line.split())}

    return Engine(
        srv,
        request,
        lambda msgs: post_stream(port, "/api/chat", body(msgs, True)),
        setup,
        {"ollama_tag": m.ollama, "num_parallel": n},
        gpu_check,
    )


ENGINES = {"ollama": ollama, "llamacpp": llamacpp, "vllm": vllm}


def read_prompts(input_path: Path, shots: int, variant: str = "") -> list[dict[str, Any]]:
    path = input_path.parent / f"prompts.{variant or f'{shots}shot'}.jsonl"
    return [json.loads(x) for x in path.read_text("utf-8").splitlines() if x]


def run_all(
    rows: list[dict[str, Any]], request: Callable[..., dict[str, Any]], n: int
) -> list[dict[str, Any]]:
    def one(row: dict[str, Any]) -> dict[str, Any]:
        t0 = time.perf_counter()
        r = request(row["messages"])
        return {
            "meldung_id": row["meldung_id"],
            **r,
            "latency_ms": round((time.perf_counter() - t0) * 1e3, 1),
        }

    with ThreadPoolExecutor(max_workers=n) as ex:
        return list(ex.map(one, rows))


def main(argv: list[str] | None = None) -> int:
    from zjmark import result, stage  # noqa: PLC0415

    p = argparse.ArgumentParser(prog="python -m zj_llm.extract")
    p.add_argument("--engine", choices=sorted(ENGINES), required=True)
    p.add_argument("--model", choices=sorted(MODELS), required=True)
    p.add_argument("--concurrency", type=int, default=16)
    p.add_argument("--shots", type=int, choices=[0, 3], default=0)
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--threads", type=int, default=0)  # CLI contract; the server decides
    p.add_argument(
        "--rows", type=int, default=0, help="debug: only the first N rows (never in measured runs)"
    )
    a = p.parse_args(argv)
    m = MODELS[a.model]
    eng = ENGINES[a.engine](m, a.concurrency)
    rows = read_prompts(a.input, a.shots)
    rows = rows[: a.rows] if a.rows else rows
    with ExitStack() as stack:
        with stage("load"):
            evidence = stack.enter_context(running(eng.server))
            eng.setup()
            eng.request(rows[0]["messages"])  # model on the GPU, kernels compiled, shared prefix cached
            evidence = {**evidence, **eng.gpu_check()}
            loaded = vram_mb(eng.server)
            if loaded < MIN_VRAM_MB:  # rule 6: no silent CPU run (Ollama loads lazily, so check after use)
                raise RuntimeError(f"server holds only {loaded} MiB on the GPU after loading the model")
            evidence = {**evidence, "server_vram_mb_loaded": loaded}
        with stage("extract"):
            t0 = time.perf_counter()
            out = run_all(rows, eng.request, a.concurrency)
            dt = time.perf_counter() - t0
        with stage("write"):
            a.output.write_text("".join(json.dumps(o, ensure_ascii=False) + "\n" for o in out), "utf-8")
        with stage("ttft"):
            ttft = [eng.ttft(r["messages"]) * 1e3 for r in rows[1 : 1 + TTFT_ROWS]]
    toks = sum(int(o["completion_tokens"] or 0) for o in out)
    lat = sorted(o["latency_ms"] for o in out)
    result(
        rows=len(rows),
        notes=f"{a.engine} {a.model} N={a.concurrency} {a.shots}-shot, JSON schema constrained",
        extra={
            "engine": "cuda (server)",
            "image": image_digest(eng.server.image),
            "server_args": eng.server.args,
            "model": a.model,
            "concurrency": a.concurrency,
            "shots": a.shots,
            "max_tokens": MAX_TOKENS,
            "throughput": {
                "rows_per_s": round(len(rows) / dt, 3),
                "output_tok_per_s": round(toks / dt, 1),
                "completion_tokens": toks,
            },
            "latency_ms": {
                "p50": lat[len(lat) // 2],
                "p95": lat[int(len(lat) * 0.95)],
                "p99": lat[int(len(lat) * 0.99)],
            },
            "ttft_ms": {"median": round(statistics.median(ttft), 1), "n": len(ttft)} if ttft else None,
            **evidence,
            **eng.info,
        },
    )
    return 0


def vram_mb(s: Server) -> int:
    from zj_llm.server import container_vram_mb  # noqa: PLC0415

    return round(container_vram_mb(s))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
