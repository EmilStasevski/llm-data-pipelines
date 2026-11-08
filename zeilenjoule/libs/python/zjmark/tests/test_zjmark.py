import io
from pathlib import Path

import pytest

import zjmark
from zjmark import smoke

GOLDEN = Path(__file__).resolve().parents[4] / "spec" / "zjmark_golden.txt"


def test_golden_sequence_matches_spec() -> None:
    buf = io.StringIO()
    with zjmark.stage("parse", buf):
        pass
    with zjmark.stage("embed", buf):
        pass
    zjmark.result(
        rows=3,
        notes="Prüfung <ok> & fertig",
        extra={"threads": 16, "engine": "cpu", "fallback_ops": ["Series.apply"]},
        stream=buf,
    )
    assert buf.getvalue() == GOLDEN.read_text(encoding="utf-8")


def test_end_marker_emitted_on_exception() -> None:
    buf = io.StringIO()
    with pytest.raises(RuntimeError), zjmark.stage("load", buf):
        raise RuntimeError("boom")
    assert buf.getvalue().splitlines()[-1] == 'ZJMARK {"ev":"end","stage":"load"}'


@pytest.mark.parametrize("name", ["", "Parse", "1parse", "pa rse", "größe", "a" * 33])
def test_invalid_stage_names_rejected(name: str) -> None:
    with pytest.raises(ValueError, match="invalid stage name"):
        zjmark.start(name, io.StringIO())


def test_negative_rows_rejected() -> None:
    with pytest.raises(ValueError, match="rows"):
        zjmark.result(rows=-1, stream=io.StringIO())


def test_smoke_contestant_contract(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "o" / "out.txt"
    assert smoke.main(["--output", str(out), "--rows", "5"]) == 0
    cap = capsys.readouterr()
    assert cap.out.splitlines()[-1].startswith('ZJRESULT {"extra":{"sleep_s":0.0,"threads":1}')
    assert [ln.split(" ", 1)[0] for ln in cap.err.splitlines()] == ["ZJMARK"] * 4
    assert out.read_text(encoding="utf-8").count("\n") == 5
