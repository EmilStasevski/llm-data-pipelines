import json
import sys
from pathlib import Path

import pytest

from zjmark import envinfo, wrap


def test_wrap_passes_exit_code_and_writes_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "out.txt"
    code = wrap.main(["--output", str(out), "--", sys.executable, "-c", "raise SystemExit(3)"])
    assert code == 3
    assert out.read_text(encoding="utf-8") == "exit=3\n"
    cap = capsys.readouterr()
    assert '"exit":3' in cap.out.splitlines()[-1]
    assert cap.err.splitlines() == [
        'ZJMARK {"ev":"start","stage":"work"}',
        'ZJMARK {"ev":"end","stage":"work"}',
    ]


def test_wrap_requires_command(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        wrap.main(["--output", str(tmp_path / "o"), "--"])


def test_envinfo_main_prints_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert envinfo.main(["zjmark"]) == 0
    info = json.loads(capsys.readouterr().out)
    assert info["packages"] == {"zjmark": "0.1.0"}
