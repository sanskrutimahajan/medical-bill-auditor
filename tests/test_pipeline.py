from pathlib import Path

import pytest

from billauditor import pipeline
from billauditor.pipeline import load_input, run_pipeline, run_stage
from billauditor.result import Err, ErrorCode, Ok, Result, err
from billauditor.tracing import Tracer, read_trace

FIXTURE = Path(__file__).parent / "fixtures" / "placeholder.pdf"


def kinds(trace: Path) -> list[tuple[str, str | None]]:
    return [(e["kind"], e.get("stage")) for e in read_trace(trace)]


def test_happy_path_runs_every_stage_and_traces_it(tmp_path: Path) -> None:
    run = run_pipeline(FIXTURE, Tracer(tmp_path, run_id="ok-run"))

    assert isinstance(run.result, Ok)
    report = run.result.value
    assert report.run_id == "ok-run"
    assert report.input.suffix == ".pdf" and len(report.input.sha256) == 64
    assert run.summary["status"] == "ok"
    assert set(run.summary["stage_latency_ms"]) == set(pipeline.STAGES)
    ends = [e for e in read_trace(run.trace_path) if e["kind"] == "stage_end"]
    assert [e["stage"] for e in ends] == list(pipeline.STAGES)
    assert all(e["status"] == "ok" and e["latency_ms"] >= 0 for e in ends)


def test_missing_file_stops_run_with_readable_error(tmp_path: Path) -> None:
    run = run_pipeline(tmp_path / "nope.pdf", Tracer(tmp_path / "runs", run_id="missing"))

    assert isinstance(run.result, Err)
    assert run.result.error.code is ErrorCode.INPUT_NOT_FOUND
    assert run.result.error.stage == "load_input"
    assert run.summary["status"] == "error" and run.summary["errors"] == 1
    assert kinds(run.trace_path)[-4:] == [
        ("stage_skipped", "extract"),
        ("stage_skipped", "audit"),
        ("stage_skipped", "report"),
        ("run_end", None),
    ]


@pytest.mark.parametrize(
    ("name", "content", "message"),
    [
        ("bill.txt", b"hello", "unsupported file type"),
        ("bill.pdf", b"", "empty"),
        ("bill.pdf", b"not really a pdf", "not a PDF"),
    ],
)
def test_bad_inputs_are_rejected(tmp_path: Path, name: str, content: bytes, message: str) -> None:
    path = tmp_path / name
    path.write_bytes(content)
    result = load_input(path)
    assert isinstance(result, Err)
    assert result.error.code is ErrorCode.INVALID_INPUT
    assert message in result.error.message


def test_directory_is_rejected(tmp_path: Path) -> None:
    result = load_input(tmp_path)
    assert isinstance(result, Err) and result.error.code is ErrorCode.INVALID_INPUT


def test_bug_in_a_stage_becomes_internal_error_not_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(_: object) -> Result[object]:
        raise KeyError("line_items")

    monkeypatch.setattr(pipeline, "extract", broken)
    run = run_pipeline(FIXTURE, Tracer(tmp_path, run_id="bug"))

    assert isinstance(run.result, Err)
    assert run.result.error.code is ErrorCode.INTERNAL
    assert run.result.error.stage == "extract"
    assert "Traceback" in run.result.error.details["traceback"]
    assert ("stage_skipped", "audit") in kinds(run.trace_path)


def test_run_stage_fills_in_stage_name(tmp_path: Path) -> None:
    tracer = Tracer(tmp_path, run_id="named")
    result: Result[int] = run_stage(
        tracer, "audit", lambda: err(ErrorCode.DATA_MISSING, "no table")
    )
    tracer.close("error")
    assert isinstance(result, Err) and result.error.stage == "audit"
