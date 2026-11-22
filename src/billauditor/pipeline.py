"""The audit pipeline: a fixed sequence of stages (see ADR-001, "a pipeline, not an agent").

    load_input -> extract -> audit -> report

Phase 0 builds only the skeleton. ``load_input`` is real; ``extract`` and ``audit`` are
placeholders that return empty results until Phases 4 and 5. What Phase 0 proves is the
plumbing: every stage returns a ``Result``, is timed and traced, and a failure stops the
run with a readable error while the later stages are recorded as skipped.
"""

from __future__ import annotations

import hashlib
import traceback
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from billauditor.result import Err, ErrorCode, Ok, Result, err
from billauditor.tracing import Tracer

STAGES: tuple[str, ...] = ("load_input", "extract", "audit", "report")

# Bills are PDFs or photos. Anything else is rejected before any processing happens.
ALLOWED_SUFFIXES = frozenset({".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"})
MAX_INPUT_BYTES = 25 * 1024 * 1024


class LoadedInput(BaseModel):
    path: str
    suffix: str
    size_bytes: int
    sha256: str


class ExtractionResult(BaseModel):
    line_items: list[dict[str, Any]] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class AuditResult(BaseModel):
    findings: list[dict[str, Any]] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class AuditReport(BaseModel):
    run_id: str
    input: LoadedInput
    line_items: list[dict[str, Any]]
    findings: list[dict[str, Any]]
    notes: list[str]


@dataclass(frozen=True)
class PipelineRun:
    result: Result[AuditReport]
    trace_path: Path
    summary: dict[str, Any]


def run_stage[T](tracer: Tracer, name: str, fn: Callable[[], Result[T]]) -> Result[T]:
    """Run one stage inside a trace span.

    Expected failures come back from ``fn`` as ``Err``. An unexpected exception (a bug) is
    caught here and turned into an ``INTERNAL`` error with its traceback, so the run ends
    with a readable error instead of a crash, and the trace shows exactly where it happened.
    """
    with tracer.stage(name) as span:
        try:
            result = fn()
        except Exception as exc:
            result = err(
                ErrorCode.INTERNAL,
                f"unexpected {type(exc).__name__}: {exc}",
                traceback=traceback.format_exc(),
            )
        if isinstance(result, Err):
            if result.error.stage is None:
                result = Err(replace(result.error, stage=name))
            span.error = result.error
    return result


# -- stages --------------------------------------------------------------------------------


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_input(path: Path) -> Result[LoadedInput]:
    if not path.exists():
        return err(ErrorCode.INPUT_NOT_FOUND, f"no file at {path}", path=str(path))
    if not path.is_file():
        return err(ErrorCode.INVALID_INPUT, f"{path} is not a file", path=str(path))
    suffix = path.suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        return err(
            ErrorCode.INVALID_INPUT,
            f"unsupported file type {suffix or '(none)'}; expected a PDF or an image",
            allowed=sorted(ALLOWED_SUFFIXES),
        )
    size = path.stat().st_size
    if size == 0:
        return err(ErrorCode.INVALID_INPUT, "the file is empty")
    if size > MAX_INPUT_BYTES:
        return err(
            ErrorCode.INVALID_INPUT,
            f"the file is {size} bytes; the limit is {MAX_INPUT_BYTES}",
        )
    if suffix == ".pdf":
        with path.open("rb") as fh:
            if not fh.read(5).startswith(b"%PDF-"):
                return err(ErrorCode.INVALID_INPUT, "the file ends in .pdf but is not a PDF")
    return Ok(LoadedInput(path=str(path), suffix=suffix, size_bytes=size, sha256=_sha256(path)))


def extract(loaded: LoadedInput) -> Result[ExtractionResult]:
    # Placeholder: real extraction (pdfplumber, OCR, local LLM) arrives in Phase 4.
    return Ok(ExtractionResult(notes=["extraction not implemented yet (Phase 4)"]))


def audit(extraction: ExtractionResult) -> Result[AuditResult]:
    # Placeholder: the rule engine arrives in Phase 5.
    return Ok(AuditResult(notes=["audit rules not implemented yet (Phase 5)"]))


def build_report(
    run_id: str, loaded: LoadedInput, extraction: ExtractionResult, audited: AuditResult
) -> Result[AuditReport]:
    return Ok(
        AuditReport(
            run_id=run_id,
            input=loaded,
            line_items=extraction.line_items,
            findings=audited.findings,
            notes=extraction.notes + audited.notes,
        )
    )


# -- orchestration -------------------------------------------------------------------------


def _skip_rest(tracer: Tracer, failed_stage: str) -> None:
    for stage in STAGES[STAGES.index(failed_stage) + 1 :]:
        tracer.skipped(stage, reason=f"{failed_stage} failed")


def run_pipeline(path: Path, tracer: Tracer) -> PipelineRun:
    """Run every stage in order, stopping at the first failure. Always closes the trace."""
    tracer.event("input", path=str(path))

    def finish(result: Result[AuditReport]) -> PipelineRun:
        summary = tracer.close("ok" if isinstance(result, Ok) else "error")
        return PipelineRun(result=result, trace_path=tracer.path, summary=summary)

    loaded = run_stage(tracer, "load_input", lambda: load_input(path))
    if isinstance(loaded, Err):
        _skip_rest(tracer, "load_input")
        return finish(loaded)

    extraction = run_stage(tracer, "extract", lambda: extract(loaded.value))
    if isinstance(extraction, Err):
        _skip_rest(tracer, "extract")
        return finish(extraction)

    audited = run_stage(tracer, "audit", lambda: audit(extraction.value))
    if isinstance(audited, Err):
        _skip_rest(tracer, "audit")
        return finish(audited)

    report = run_stage(
        tracer,
        "report",
        lambda: build_report(tracer.run_id, loaded.value, extraction.value, audited.value),
    )
    return finish(report)
