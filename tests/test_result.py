from billauditor.result import Err, ErrorCode, Ok, err


def test_ok_and_err_are_distinguishable() -> None:
    assert Ok(5).ok is True
    assert err(ErrorCode.DATA_MISSING, "gone").ok is False


def test_err_to_dict_is_readable_and_flags_error() -> None:
    e = err(ErrorCode.LLM_BAD_OUTPUT, "not JSON", stage="extract", retryable=True, attempt=1)
    assert isinstance(e, Err)
    assert e.error.to_dict() == {
        "is_error": True,
        "code": "llm_bad_output",
        "message": "not JSON",
        "stage": "extract",
        "retryable": True,
        "details": {"attempt": 1},
    }
