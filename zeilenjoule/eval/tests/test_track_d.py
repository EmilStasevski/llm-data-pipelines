"""Track D reference (schema, prompt, scoring) and the d.extract verifier."""

from __future__ import annotations

import json
from pathlib import Path

from zj_eval import verify_track_d
from zj_reference import track_d as ref

GOLD = {"komponente": "Regelventil", "schadensart": "Leckage", "ursache": "Verschmutzung",
        "massnahme": "Bauteil getauscht", "sicherheitsrelevant": False, "stillstand_minuten": 420,
        "ersatzteil_materialnr": None}  # fmt: skip


def test_parse_is_strict() -> None:
    good = json.dumps(GOLD, ensure_ascii=False)
    assert ref.parse(f"  {good}\n") == GOLD
    assert ref.parse(f"```json\n{good}\n```") is None  # no markdown
    assert ref.parse(json.dumps({**GOLD, "schadensart": "Leck"})) is None  # enum
    assert ref.parse(json.dumps({**GOLD, "extra": 1})) is None  # no extra keys
    assert ref.parse(json.dumps({**GOLD, "stillstand_minuten": 7.5})) is None
    assert ref.parse(json.dumps({**GOLD, "stillstand_minuten": True})) is None
    assert ref.parse("{") is None


def test_scoring() -> None:
    assert ref.score_row(GOLD, GOLD) == dict.fromkeys(ref.FIELDS, 1.0)
    s = ref.score_row(
        {**GOLD, "massnahme": "Bauteil ersetzt", "ursache": None, "ersatzteil_materialnr": "x"}, GOLD
    )
    assert s["massnahme"] == 0.5  # token-F1: 1 of 2 tokens
    assert s["ursache"] == 0.0  # null vs. value
    assert s["ersatzteil_materialnr"] == 0.0
    assert ref.token_f1(None, None) == 1.0
    assert ref.token_f1("SPS-Baugruppe", "sps baugruppe") == 1.0
    q = ref.score([GOLD, None], [GOLD, GOLD])
    assert q["json_validity"] == 0.5
    assert q["macro_f1"] == 0.5


def test_schema_file_matches_reference() -> None:
    spec = json.loads(ref.SCHEMA_PATH.read_text("utf-8"))
    assert spec == ref.schema()
    assert spec["required"] == list(ref.FIELDS)


def test_verifier(tmp_path: Path) -> None:
    gold = tmp_path / "gold.jsonl"
    gold.write_text("".join(json.dumps({"meldung_id": str(i), **GOLD}) + "\n" for i in range(3)), "utf-8")
    out = tmp_path / "out.jsonl"

    def write(texts: list[str], tokens: int = 60) -> None:
        out.write_text("".join(json.dumps({"meldung_id": str(i), "text": t, "completion_tokens": tokens,
                                           "finish_reason": "stop"}) + "\n" for i, t in enumerate(texts)),
                       "utf-8")  # fmt: skip

    ok = json.dumps(GOLD, ensure_ascii=False)
    write([ok] * 3)
    res = verify_track_d.check(out, gold)
    assert res["passed"], res["detail"]
    assert res["quality"]["macro_f1"] == 1.0
    write([ok, ok, "kein JSON"])
    assert not verify_track_d.check(out, gold)["passed"]  # validity 0.67 < 0.995
    write([f"<think>…</think>{ok}"] + [ok] * 2)
    assert not verify_track_d.check(out, gold)["passed"]
    write([ok] * 3, tokens=900)
    assert "reasoning" in verify_track_d.check(out, gold)["detail"]
    write([ok] * 2)
    assert "order" in verify_track_d.check(out, gold)["detail"]
