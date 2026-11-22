from pathlib import Path

import pytest

from billauditor.result import ErrorCode, err
from billauditor.tracing import Tracer, read_trace


class FakeClock:
    """Advances by a fixed step on every call, so latencies are deterministic."""

    def __init__(self, step: float = 0.5) -> None:
        self.now = 0.0
        self.step = step

    def __call__(self) -> float:
        self.now += self.step
        return self.now


def test_stage_events_have_run_id_order_and_latency(tmp_path: Path) -> None:
    tracer = Tracer(tmp_path, run_id="r1", clock=FakeClock())
    with tracer.stage("extract", pages=2):
        pass
    summary = tracer.close("ok")

    events = read_trace(tmp_path / "r1.jsonl")
    assert [e["kind"] for e in events] == ["run_start", "stage_start", "stage_end", "run_end"]
    assert {e["run_id"] for e in events} == {"r1"}
    assert [e["seq"] for e in events] == [1, 2, 3, 4]
    end = events[2]
    assert end["status"] == "ok" and end["latency_ms"] == 500.0 and end["pages"] == 2
    assert summary["stage_latency_ms"] == {"extract": 500.0}
    assert summary["errors"] == 0


def test_stage_marked_failed_records_error(tmp_path: Path) -> None:
    tracer = Tracer(tmp_path, run_id="r2", clock=FakeClock())
    with tracer.stage("audit") as span:
        span.error = err(ErrorCode.DATA_MISSING, "no NCCI table").error
    summary = tracer.close("error")

    end = [e for e in read_trace(tracer.path) if e["kind"] == "stage_end"][0]
    assert end["status"] == "error"
    assert end["error"]["code"] == "data_missing"
    assert summary["errors"] == 1


def test_unexpected_exception_is_recorded_then_raised(tmp_path: Path) -> None:
    tracer = Tracer(tmp_path, run_id="r3", clock=FakeClock())
    with pytest.raises(ZeroDivisionError), tracer.stage("report"):
        _ = 1 / 0
    tracer.close("error")
    end = [e for e in read_trace(tracer.path) if e["kind"] == "stage_end"][0]
    assert end["status"] == "error" and "ZeroDivisionError" in end["exception"]


def test_llm_call_tokens_and_latency_roll_up(tmp_path: Path) -> None:
    tracer = Tracer(tmp_path, run_id="r4", clock=FakeClock())
    for _ in range(2):
        tracer.llm_call(
            stage="extract",
            model="qwen2.5:7b-instruct",
            prompt_version="extract-v1",
            input="bill text",
            output='{"line_items": []}',
            input_tokens=100,
            output_tokens=40,
            latency_ms=1234.5,
        )
    summary = tracer.close("ok")

    calls = [e for e in read_trace(tracer.path) if e["kind"] == "llm_call"]
    assert len(calls) == 2
    assert calls[0]["prompt_version"] == "extract-v1" and calls[0]["latency_ms"] == 1234.5
    assert summary["llm_calls"] == 2
    assert summary["tokens"] == {"input": 200, "output": 80}


def test_long_values_are_truncated(tmp_path: Path) -> None:
    tracer = Tracer(tmp_path, run_id="r5", max_chars=10)
    tracer.event("blob", text="x" * 50, nested={"items": ["y" * 50]})
    tracer.close("ok")
    blob = [e for e in read_trace(tracer.path) if e["kind"] == "event"][0]
    assert blob["text"] == "x" * 10 + "... [truncated 40 chars]"
    assert blob["nested"]["items"][0].endswith("[truncated 40 chars]")


def test_writing_after_close_fails_loudly(tmp_path: Path) -> None:
    tracer = Tracer(tmp_path, run_id="r6")
    tracer.close("ok")
    with pytest.raises(RuntimeError):
        tracer.event("late")
