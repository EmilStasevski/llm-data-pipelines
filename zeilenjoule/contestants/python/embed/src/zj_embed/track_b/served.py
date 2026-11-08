"""Shared `main` of the server contestants b06–b08 (ADR 0015): stages load (container start until healthy and
on the GPU), read, embed (HTTP), write; the container is removed afterwards."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import ExitStack
from dataclasses import dataclass, field
from typing import Any

from zj_embed.track_b.common import DIM, MAX_TOKENS, parse_args, read_texts, write_vectors
from zj_embed.track_b.server import Server, embed_all, image_digest, running


@dataclass(frozen=True)
class Client:
    request: Callable[[list[Any]], list[list[float]]]  # one HTTP request for a chunk of inputs
    chunk: int  # inputs per request
    in_flight: int  # concurrent requests
    # texts → request inputs (identity, or client-side token ids where the server cannot truncate)
    prepare: Callable[[list[str]], Sequence[Any]] = field(default=lambda texts: texts)
    setup: Callable[[], None] = field(default=lambda: None)  # extra load-stage work (e.g. tokenizer)


def main(  # noqa: PLR0913
    argv: list[str] | None, prog: str, server: Server, client: Client, *, notes: str, extra: dict[str, object]
) -> int:
    from zjmark import result, stage  # noqa: PLC0415

    a = parse_args(argv, prog)
    chunk = a.batch_size or client.chunk
    with ExitStack() as stack:
        with stage("load"):
            evidence = stack.enter_context(running(server))
            client.setup()
        with stage("read"):
            texts = read_texts(a.input)
        with stage("embed"):
            vecs = embed_all(client.prepare(texts), client.request, chunk, client.in_flight)
        with stage("write"):
            write_vectors(a.output, vecs, len(texts))
    result(rows=len(texts), notes=notes, extra={
        "engine": "cuda (server)", "image": image_digest(server.image), "server_args": server.args,
        "chunk": chunk, "in_flight": client.in_flight, "dim": DIM, "max_tokens": MAX_TOKENS,
        **evidence, **extra,
    })  # fmt: skip
    return 0
