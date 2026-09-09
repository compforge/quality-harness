from __future__ import annotations

from dataclasses import dataclass

import pytest

from trajectory_harness import (
    DetectionResult,
    DetectorSpec,
    VerificationResult,
    VerifierSpec,
    ExecutionResult,
    ExecutionSuccessVerifier,
    Failure,
    Finding,
    MeasurementResult,
    RepeatedToolCallDetector,
    ToolSuccessVerifier,
    detect,
    verify,
)
from trajectory_harness.model import (
    make_atif_step as Step,
    make_atif_trajectory as Trajectory,
    step_failure,
    trajectory_from_dict,
    trajectory_to_dict,
)


def _tool_step(step_id: str, path: str, *, failure: Failure | None = None) -> Step:
    return Step(
        step_id=step_id,
        parent_step_id=None,
        operation="execute_tool",
        name="file_read",
        start_ms=0,
        duration_ms=1,
        status="error" if failure else "ok",
        failure=failure,
        input_messages=(
            {
                "role": "assistant",
                "parts": [
                    {
                        "type": "tool_call",
                        "name": "file_read",
                        "arguments": {"path": path},
                    }
                ],
            },
        ),
    )


def test_repeated_tool_call_detector_returns_finding_and_evidence():
    trajectory = Trajectory(
        trajectory_id="t1",
        steps=(
            _tool_step("s1", "a.go"),
            _tool_step("s2", "b.go"),
            _tool_step("s3", "a.go"),
        ),
    )

    detection = detect(trajectory, [RepeatedToolCallDetector()])

    result = detection.results[0]
    assert result.status == "analyzed"
    assert result.findings == (
        Finding(
            code="repeated_tool_call",
            severity="warning",
            summary="1 of 3 tool calls repeat an earlier tool name and arguments.",
            step_ids=("3",),
            hypotheses=(
                "The tool may be missing a batch operation.",
                "The tool description may not encourage batching or result reuse.",
                "The agent loop may lack an effective repeat-call stopping strategy.",
            ),
        ),
    )
    assert result.to_dict()["findings"][0]["step_ids"] == ["3"]


def test_detector_is_not_applicable_when_trajectory_has_no_tool_calls():
    trajectory = Trajectory(
        trajectory_id="t1",
        steps=(
            Step(
                step_id="s1",
                parent_step_id=None,
                operation="chat",
                name="model",
                start_ms=0,
                duration_ms=1,
            ),
        ),
    )

    detection = detect(trajectory, [RepeatedToolCallDetector()])

    assert detection.results[0].status == "not_applicable"
    assert detection.results[0].findings == ()


def test_model_round_tool_calls_are_fallback_when_tool_spans_are_absent():
    trajectory = Trajectory(
        trajectory_id="t1",
        steps=tuple(
            Step(
                step_id=f"s{index}",
                parent_step_id=None,
                operation="chat",
                name="model",
                start_ms=index,
                duration_ms=1,
                output_messages=(
                    {
                        "role": "assistant",
                        "parts": [
                            {
                                "type": "tool_call",
                                "name": "code_search",
                                "arguments": {"query": "foo"},
                            }
                        ],
                    },
                ),
            )
            for index in (1, 2)
        ),
    )

    result = RepeatedToolCallDetector().detect(trajectory)

    assert result.findings[0].step_ids == ("2",)


def test_common_execution_and_tool_verifiers_keep_failures_as_facts():
    failure = Failure(
        kind="tool",
        phase="prepare",
        error_type="dependency_missing",
        code="ENOENT",
    )
    trajectory = Trajectory(
        trajectory_id="t1",
        steps=(_tool_step("s1", "a.go", failure=failure),),
        execution=ExecutionResult(outcome="failed", duration_ms=25, failure=failure),
    )

    verification = verify(
        trajectory, [ExecutionSuccessVerifier(), ToolSuccessVerifier()]
    )

    execution, tool = verification.results
    assert execution.verdict == "fail"
    assert execution.score == 0
    assert tool.verdict == "fail"
    assert tool.score == 0
    assert tool.step_ids == ("1",)


def test_failure_and_execution_round_trip_with_trajectory():
    failure = Failure(
        kind="llm",
        phase="request",
        error_type="rate_limit",
        code="429",
        message="too many requests",
    )
    original = Trajectory(
        trajectory_id="t1",
        steps=(
            Step(
                step_id="s1",
                parent_step_id=None,
                operation="chat",
                name="model",
                start_ms=1,
                duration_ms=2,
                status="error",
                failure=failure,
            ),
        ),
        execution=ExecutionResult(outcome="failed", duration_ms=2, failure=failure),
    )

    restored = trajectory_from_dict(trajectory_to_dict(original))

    assert restored == original
    restored_failure = step_failure(restored.steps[0])
    assert restored_failure is not None
    assert restored_failure.key == "llm.request.rate_limit"


def test_verifier_exception_is_health_data_not_zero_score():
    @dataclass(frozen=True)
    class BrokenVerifier:
        spec: VerifierSpec = VerifierSpec(
            verifier_id="broken",
            title="Broken",
            description="Test verifier failure.",
            category="effect",
        )

        def verify(self, trajectory, *, measurements=(), reference=None):
            del trajectory, measurements, reference
            raise RuntimeError("judge unavailable")

    verification = verify(Trajectory(trajectory_id="t1", steps=()), [BrokenVerifier()])

    assert verification.results == (
        VerificationResult(
            verifier_id="broken",
            status="error",
            explanation="RuntimeError: judge unavailable",
        ),
    )


def test_detector_exception_is_health_data():
    @dataclass(frozen=True)
    class BrokenDetector:
        spec: DetectorSpec = DetectorSpec(
            detector_id="broken",
            title="Broken",
            description="Test detector failure.",
            category="cost",
        )

        def detect(self, trajectory, *, measurements=()):
            del trajectory, measurements
            raise RuntimeError("detector unavailable")

    detection = detect(Trajectory(trajectory_id="t1", steps=()), [BrokenDetector()])

    assert detection.results == (
        DetectionResult(
            detector_id="broken",
            status="error",
            explanation="RuntimeError: detector unavailable",
        ),
    )


def test_verified_result_requires_a_judgment():
    with pytest.raises(ValueError, match="verdict, a score, or both"):
        VerificationResult(verifier_id="empty", status="verified")


def test_detector_and_verifier_specs_reject_noncanonical_axes():
    with pytest.raises(ValueError, match="category must be 'cost' or 'effect'"):
        DetectorSpec("legacy", "Legacy", "", category="behavior")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="rule_type must be 'hard' or 'soft'"):
        VerifierSpec("legacy", "Legacy", "", category="effect", rule_type="judge")  # type: ignore[arg-type]


def test_detector_and_verifier_receive_measurements_and_keep_axes_orthogonal():
    derived = (
        MeasurementResult(
            measurer_id="model_usage",
            status="measured",
            measurements={"total_tokens": 120},
        ),
    )

    @dataclass(frozen=True)
    class EffectDiscovery:
        spec: DetectorSpec = DetectorSpec(
            detector_id="effect_discovery",
            title="Effect discovery",
            description="Find an effect pattern with a soft rule.",
            category="effect",
            rule_type="soft",
        )

        def detect(self, trajectory, *, measurements=()):
            del trajectory
            assert measurements == derived
            return DetectionResult(detector_id=self.spec.detector_id, status="analyzed")

    @dataclass(frozen=True)
    class CostBudget:
        spec: VerifierSpec = VerifierSpec(
            verifier_id="cost_budget",
            title="Cost budget",
            description="Verify a cost budget with a hard rule.",
            category="cost",
            rule_type="hard",
        )

        def verify(self, trajectory, *, measurements=(), reference=None):
            del trajectory, reference
            total = measurements[0].measurements["total_tokens"]
            return VerificationResult(
                verifier_id=self.spec.verifier_id,
                status="verified",
                verdict="pass" if total <= 128 else "fail",
            )

    trajectory = Trajectory(trajectory_id="t1", steps=())
    detection = detect(trajectory, [EffectDiscovery()], measurements=derived)
    verification = verify(trajectory, [CostBudget()], measurements=derived)

    assert detection.results[0].status == "analyzed"
    assert verification.results[0].verdict == "pass"
    assert DetectorSpec.from_dict(EffectDiscovery().spec.to_dict()).rule_type == "soft"
    restored = VerifierSpec.from_dict(CostBudget().spec.to_dict())
    assert (restored.category, restored.rule_type) == ("cost", "hard")
