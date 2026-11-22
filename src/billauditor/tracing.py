"""Structured tracing: one JSON-lines file per run.

Every stage, LLM call and error is written as an event under a single ``run_id``, so when a
result is wrong you can see which step went wrong. Events are flushed as soon as they're
written, so a crash still leaves a partial trace behind.

Trace files can contain bill contents (patient information). They're written only to the
local runs directory, which git ignores.

Event kinds:
    run_start     the run began
    stage_start   a stage began
    stage_end     a stage finished, with status "ok" or "error", latency_ms and any error
    stage_skipped a stage did not run because an earlier stage failed
    llm_call      model, prompt version, input, output, tokens, latency
    event         anything else worth recording (for example, a rule hit)
    run_end       totals: status, stage latencies, tokens, error count
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

from billauditor.result import Error


def _truncate(value: Any, max_chars: int) -> Any:
    """Cut long strings so traces stay readable; record how much was removed."""
    if isinstance(value, str) and len(value) > max_chars:
        return value[:max_chars] + f"... [truncated {len(value) - max_chars} chars]"
    if isinstance(value, dict):
        return {k: _truncate(v, max_chars) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_truncate(v, max_chars) for v in value]
    return value


@dataclass
class StageSpan:
    """Handle for a running stage. Set ``error`` to mark the stage as failed."""

    name: str
    error: Error | None = None
    attrs: dict[str, Any] = field(default_factory=dict)


class Tracer:
    """Writes one run's events to ``<runs_dir>/<run_id>.jsonl``.

    ``clock`` returns seconds (``time.perf_counter`` by default) and can be replaced in tests
    so latencies are deterministic.
    """

    def __init__(
        self,
        runs_dir: Path,
        *,
        run_id: str | None = None,
        max_chars: int = 4000,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.run_id = run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8]
        self.max_chars = max_chars
        self._clock = clock
        self._seq = 0
        self._run_started = clock()
        self._stage_latencies: dict[str, float] = {}
        self._tokens = {"input": 0, "output": 0}
        self._llm_calls = 0
        self._errors = 0
        runs_dir.mkdir(parents=True, exist_ok=True)
        self.path = runs_dir / f"{self.run_id}.jsonl"
        self._fh: IO[str] | None = self.path.open("a", encoding="utf-8")
        self._write("run_start")

    # -- writing ---------------------------------------------------------------------------

    def _write(self, kind: str, **data: Any) -> None:
        if self._fh is None:
            raise RuntimeError(f"trace {self.run_id} is already closed")
        self._seq += 1
        record = {
            "run_id": self.run_id,
            "seq": self._seq,
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "kind": kind,
            **_truncate(data, self.max_chars),
        }
        self._fh.write(json.dumps(record, default=str) + "\n")
        self._fh.flush()

    # -- public API ------------------------------------------------------------------------

    @contextmanager
    def stage(self, name: str, **attrs: Any) -> Iterator[StageSpan]:
        """Time a stage. Unexpected exceptions are recorded, then re-raised."""
        span = StageSpan(name=name, attrs=dict(attrs))
        self._write("stage_start", stage=name, **attrs)
        start = self._clock()
        try:
            yield span
        except BaseException as exc:
            latency_ms = (self._clock() - start) * 1000
            self._record_stage_end(span, latency_ms, exception=repr(exc))
            raise
        latency_ms = (self._clock() - start) * 1000
        self._record_stage_end(span, latency_ms)

    def _record_stage_end(
        self, span: StageSpan, latency_ms: float, exception: str | None = None
    ) -> None:
        failed = span.error is not None or exception is not None
        if failed:
            self._errors += 1
        self._stage_latencies[span.name] = round(latency_ms, 3)
        data: dict[str, Any] = {
            "stage": span.name,
            "status": "error" if failed else "ok",
            "latency_ms": round(latency_ms, 3),
            **span.attrs,
        }
        if span.error is not None:
            data["error"] = span.error.to_dict()
        if exception is not None:
            data["exception"] = exception
        self._write("stage_end", **data)

    def skipped(self, stage: str, reason: str) -> None:
        self._write("stage_skipped", stage=stage, reason=reason)

    def llm_call(
        self,
        *,
        stage: str,
        model: str,
        prompt_version: str,
        input: Any,
        output: Any,
        input_tokens: int,
        output_tokens: int,
        latency_ms: float,
        error: Error | None = None,
    ) -> None:
        self._llm_calls += 1
        self._tokens["input"] += input_tokens
        self._tokens["output"] += output_tokens
        if error is not None:
            self._errors += 1
        self._write(
            "llm_call",
            stage=stage,
            model=model,
            prompt_version=prompt_version,
            input=input,
            output=output,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=round(latency_ms, 3),
            status="error" if error else "ok",
            error=error.to_dict() if error else None,
        )

    def event(self, name: str, **data: Any) -> None:
        self._write("event", name=name, **data)

    def close(self, status: str) -> dict[str, Any]:
        """Write the run summary and close the file. Returns the summary."""
        summary = {
            "status": status,
            "total_latency_ms": round((self._clock() - self._run_started) * 1000, 3),
            "stage_latency_ms": dict(self._stage_latencies),
            "llm_calls": self._llm_calls,
            "tokens": dict(self._tokens),
            "errors": self._errors,
        }
        self._write("run_end", **summary)
        if self._fh is not None:
            self._fh.close()
            self._fh = None
        return summary


def read_trace(path: Path) -> list[dict[str, Any]]:
    """Load a trace file back into a list of events."""
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]
