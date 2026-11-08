"""Server contestants (b06–b08): docker command, ordered/normalised client fan-out, fail-loud GPU checks."""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pytest

from zj_embed import b06_tei, b07_vllm_pooling, b08_llamacpp_embed
from zj_embed.track_b import server as sv
from zj_embed.track_b.common import DIM


def test_docker_cmd_pins_digest_mounts_read_only_and_binds_localhost() -> None:
    for mod in (b06_tei, b07_vllm_pooling, b08_llamacpp_embed):
        s = mod.SERVER
        cmd = sv.docker_cmd(s)
        assert "@sha256:" in s.image  # rule 9
        assert cmd[cmd.index(s.image) + 1 :] == s.args
        assert "--runtime=nvidia" in cmd
        assert "127.0.0.1" in s.args  # never exposed on the LAN (--network host)
        ro = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-v" and cmd[i + 1].endswith(":ro")]
        assert len(ro) == len(s.mounts)


def test_embed_all_keeps_input_order_and_normalises() -> None:
    items = list(range(103))

    def request(part: list[int]) -> list[list[float]]:
        time.sleep(0.001 * (part[0] % 7))  # out-of-order completion
        return [[float(i + 1)] + [0.0] * (DIM - 1) for i in part]

    out = sv.embed_all(items, request, chunk=10, in_flight=4)
    assert out.dtype == np.float16
    assert out.shape == (103, DIM)
    np.testing.assert_allclose(out[:, 0], 1.0)  # each row normalised; order is checked below
    raw = sv.embed_all(items, lambda p: [[float(i + 1), 1.0] + [0.0] * (DIM - 2) for i in p], 10, 4)
    ratio = raw[:, 1].astype(np.float32) / raw[:, 0].astype(np.float32)
    np.testing.assert_allclose(ratio, 1 / (np.arange(103) + 1), rtol=2e-3)


def test_embed_all_fails_on_wrong_server_shape() -> None:
    with pytest.raises(RuntimeError, match="shape"):
        sv.embed_all(["a", "b"], lambda p: [[0.0] * DIM], chunk=2, in_flight=1)


def test_require_gpu_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    s = sv.Server(name="x", image="i@sha256:0", args=[], port=1, gpu_log=r"on Cuda", min_vram_mb=1000)
    vram, log = [10.0], ["Starting model on Cpu"]

    def fake_vram(_s: sv.Server) -> float:
        return vram[0]

    def fake_logs(_s: sv.Server) -> str:
        return log[0]

    monkeypatch.setattr(sv, "container_vram_mb", fake_vram)
    monkeypatch.setattr(sv, "logs", fake_logs)
    with pytest.raises(RuntimeError, match="CPU run"):
        sv.require_gpu(s)
    vram[0] = 1500.0
    with pytest.raises(RuntimeError, match="GPU marker"):
        sv.require_gpu(s)
    log[0] = "Starting FlashBert model on Cuda(0)"
    assert sv.require_gpu(s) == {"server_vram_mb": 1500, "gpu_log": "on Cuda"}


def test_llamacpp_client_truncates_token_ids_like_the_reference(tmp_path: Path) -> None:
    if not (b08_llamacpp_embed.HF512 / "tokenizer.json").exists():
        pytest.skip("models/derived/bge-m3/hf512 not prepared")
    b08_llamacpp_embed.setup()
    ids = b08_llamacpp_embed.prepare(["kurz", "lang " * 2000])
    assert len(ids[1]) == 512
    assert ids[1][0] == 0  # <s> kept, as sentence-transformers does
    assert ids[1][-1] == 2  # </s>
