import json
from pathlib import Path

import pytest

from billauditor.cli import main

FIXTURE = Path(__file__).parent / "fixtures" / "placeholder.pdf"


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)  # so a developer's own .env is not picked up
    monkeypatch.setenv("BILLAUDITOR_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("BILLAUDITOR_DATA_DIR", str(tmp_path / "data"))
    return tmp_path


def test_run_success_prints_report_and_trace(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["run", str(FIXTURE)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["summary"]["status"] == "ok"
    assert Path(out["trace"]).exists()
    assert out["report"]["input"]["suffix"] == ".pdf"


def test_run_failure_exits_1_with_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["run", "missing.pdf"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["error"]["is_error"] is True and out["error"]["code"] == "input_not_found"


def test_verify_data_without_manifest_fails(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["verify-data"]) == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "data_missing"


def test_add_data_then_verify(isolated_dirs: Path, capsys: pytest.CaptureFixture[str]) -> None:
    raw = isolated_dirs / "data" / "raw"
    raw.mkdir(parents=True)
    (raw / "hcpcs.csv").write_text("code\nA0428\n", encoding="utf-8")

    assert main(["add-data", str(raw / "hcpcs.csv"), "--source", "https://cms.gov/hcpcs"]) == 0
    capsys.readouterr()
    assert main(["verify-data"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {
        "ok": True,
        "checked": 1,
        "failed": 0,
        "files": [{"ok": True, "relative_path": "hcpcs.csv"}],
    }
