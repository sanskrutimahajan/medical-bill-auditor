"""Typed results for every pipeline stage.

Every stage returns either ``Ok(value)`` or ``Err(error)``. Expected failures (a missing file,
bad model output, missing reference data) never raise. They come back as an ``Err`` with an
``ErrorCode`` that the caller, the trace and the app can all read. That keeps one failure
from crashing the run or being replaced by a made-up value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal


class ErrorCode(StrEnum):
    """Stable codes for expected failures. Add a code here before using it anywhere."""

    INPUT_NOT_FOUND = "input_not_found"
    INVALID_INPUT = "invalid_input"
    DATA_MISSING = "data_missing"
    CHECKSUM_MISMATCH = "checksum_mismatch"
    SCHEMA_INVALID = "schema_invalid"
    LLM_UNAVAILABLE = "llm_unavailable"
    LLM_BAD_OUTPUT = "llm_bad_output"
    TIMEOUT = "timeout"
    INTERNAL = "internal"


@dataclass(frozen=True, slots=True)
class Error:
    """A failure that is safe to show, log and act on.

    ``retryable`` tells the caller whether trying the same thing again could help (for
    example, a model returning invalid JSON) or not (for example, a missing file).
    """

    code: ErrorCode
    message: str
    stage: str | None = None
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_error": True,
            "code": self.code.value,
            "message": self.message,
            "stage": self.stage,
            "retryable": self.retryable,
            "details": self.details,
        }


@dataclass(frozen=True, slots=True)
class Ok[T]:
    value: T
    ok: Literal[True] = True


@dataclass(frozen=True, slots=True)
class Err:
    error: Error
    ok: Literal[False] = False


type Result[T] = Ok[T] | Err


def err(
    code: ErrorCode,
    message: str,
    *,
    stage: str | None = None,
    retryable: bool = False,
    **details: Any,
) -> Err:
    """Shorthand for building an ``Err``."""
    return Err(Error(code=code, message=message, stage=stage, retryable=retryable, details=details))
