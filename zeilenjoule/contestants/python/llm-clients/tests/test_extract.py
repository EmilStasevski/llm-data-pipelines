"""Track D client: model registry, request bodies, reasoning switches (no server needed)."""

from __future__ import annotations

import re
import time
from pathlib import Path

from zj_llm import extract
from zj_llm.server import REPO


def test_registry_matches_the_download_script() -> None:
    script = (REPO / "scripts" / "fetch_models_d.sh").read_text("utf-8")
    for key, m in extract.MODELS.items():
        if m.gguf:
            assert m.gguf.split("/")[1] in script, key
        if m.vllm:
            assert re.search(rf"\b{re.escape(m.vllm)}\b", script), key


def test_reasoning_switches() -> None:
    q = extract.openai_extras(extract.MODELS["qwen35_9b_q4"])
    assert q == {"chat_template_kwargs": {"enable_thinking": False}}
    g = extract.openai_extras(extract.MODELS["gptoss_20b_mxfp4"])
    assert g["reasoning_effort"] == "low"
    assert extract.openai_extras(extract.MODELS["eurollm_9b_q8"]) == {}


def test_schema_is_the_spec_file() -> None:
    assert extract.SCHEMA["required"][0] == "komponente"
    assert extract.SCHEMA["additionalProperties"] is False


def test_run_all_keeps_input_order() -> None:
    rows = [{"meldung_id": str(i), "messages": [{"role": "user", "content": str(i)}]} for i in range(20)]

    def request(messages: list[dict[str, str]]) -> dict[str, object]:
        time.sleep(0.001 * (20 - int(messages[0]["content"])))  # later rows finish first
        return {"text": messages[0]["content"], "completion_tokens": 1, "finish_reason": "stop"}

    out = extract.run_all(rows, request, 8)
    assert [o["meldung_id"] for o in out] == [str(i) for i in range(20)]
    assert all(o["text"] == o["meldung_id"] for o in out)


def test_read_prompts(tmp_path: Path) -> None:
    (tmp_path / "prompts.0shot.jsonl").write_text('{"meldung_id": "1", "messages": []}\n', "utf-8")
    assert extract.read_prompts(tmp_path / "rows.parquet", 0) == [{"meldung_id": "1", "messages": []}]
