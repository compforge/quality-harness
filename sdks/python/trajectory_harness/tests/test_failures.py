from trajectory_harness import llm_failure, llm_timeout


def test_llm_failure_uses_common_dimensions():
    failure = llm_failure(
        "request",
        "timeout",
        code="deadline_exceeded",
        message="provider deadline exceeded",
    )

    assert failure.key == "llm.request.timeout"
    assert failure.to_dict() == {
        "kind": "llm",
        "phase": "request",
        "error_type": "timeout",
        "code": "deadline_exceeded",
        "message": "provider deadline exceeded",
    }


def test_llm_timeout_records_observed_progress_boundary():
    failure = llm_timeout(
        "first_chunk",
        code="deadline_exceeded",
        message="no response chunk before deadline",
    )

    assert failure.key == "llm.first_chunk.timeout"
    assert failure.to_dict() == {
        "kind": "llm",
        "phase": "first_chunk",
        "error_type": "timeout",
        "code": "deadline_exceeded",
        "message": "no response chunk before deadline",
    }
