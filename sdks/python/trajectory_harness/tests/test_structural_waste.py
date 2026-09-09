from __future__ import annotations

from trajectory_harness import (
    CacheRetentionBloatDetector,
    ContextBloatWithoutCompactDetector,
    ContextUsageMeasurer,
    Failure,
    MeasurementThresholdVerifier,
    ModelUsageMeasurer,
    OversizedToolObservationDetector,
    RetryUsageMeasurer,
    ShortDecisionChurnDetector,
    TrajectoryAnalysisRunner,
    TrajectoryDataset,
    UnchangedToolRetryDetector,
)
from trajectory_harness.model import (
    make_atif_step as Step,
    make_atif_trajectory as Trajectory,
    step_failure,
)


def _model_step(
    step_id: str,
    start_ms: float,
    *,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int | None = None,
) -> Step:
    attributes = {
        "gen_ai.usage.input_tokens": input_tokens,
        "gen_ai.usage.output_tokens": output_tokens,
    }
    if cached_tokens is not None:
        attributes["gen_ai.usage.cached_input_tokens"] = cached_tokens
    return Step(
        step_id=step_id,
        parent_step_id=None,
        operation="chat",
        name="model",
        start_ms=start_ms,
        duration_ms=1,
        attributes=attributes,
    )


def _tool_step(
    step_id: str,
    start_ms: float,
    *,
    arguments: dict,
    failure: Failure | None = None,
    output: str | None = None,
) -> Step:
    return Step(
        step_id=step_id,
        parent_step_id=None,
        operation="execute_tool",
        name="execute_tool read",
        start_ms=start_ms,
        duration_ms=1,
        status="error" if failure else "ok",
        failure=failure,
        input_messages=(
            {
                "role": "assistant",
                "parts": [
                    {
                        "type": "tool_call",
                        "name": "read",
                        "arguments": arguments,
                    }
                ],
            },
        ),
        output_messages=(
            (
                {
                    "role": "tool",
                    "parts": [{"type": "tool_call_response", "content": output}],
                },
            )
            if output is not None
            else ()
        ),
    )


def test_retry_usage_and_unchanged_retry_finding_preserve_argument_delta():
    failure = Failure(kind="tool", phase="execute", error_type="invalid_argument")
    trajectory = Trajectory(
        trajectory_id="retry",
        steps=(
            _tool_step("failed-1", 0, arguments={"path": "bad"}, failure=failure),
            _tool_step("failed-2", 1, arguments={"path": "bad"}, failure=failure),
            _tool_step("recovered", 2, arguments={"path": "good"}, output="ok"),
        ),
    )

    measurement = RetryUsageMeasurer().measure(trajectory)
    detection = UnchangedToolRetryDetector().detect(trajectory)

    assert measurement.measurements == {
        "failed_tool_call_count": 2,
        "retried_failed_tool_call_count": 2,
        "retried_failure_ratio": 1.0,
        "unchanged_retry_count": 1,
        "changed_retry_count": 1,
        "recovered_retry_count": 1,
    }
    assert measurement.step_ids == ("1", "2", "3")
    assert detection.findings[0].code == "unchanged_tool_retry"
    assert detection.findings[0].step_ids == ("1", "2")


def test_retry_usage_does_not_pair_parallel_calls_in_the_same_failed_step():
    failure = Failure(kind="tool", phase="execute", error_type="invalid_argument")
    trajectory = Trajectory(
        trajectory_id="parallel-failure",
        steps=(
            Step(
                step_id="parallel",
                parent_step_id=None,
                operation="execute_tool",
                name="execute_tool read",
                start_ms=0,
                duration_ms=1,
                status="error",
                failure=failure,
                input_messages=(
                    {
                        "role": "assistant",
                        "parts": [
                            {
                                "type": "tool_call",
                                "name": "read",
                                "arguments": {"path": "same"},
                            },
                            {
                                "type": "tool_call",
                                "name": "read",
                                "arguments": {"path": "same"},
                            },
                        ],
                    },
                ),
            ),
        ),
    )

    measurement = RetryUsageMeasurer().measure(trajectory)
    detection = UnchangedToolRetryDetector().detect(trajectory)

    assert measurement.measurements["retried_failed_tool_call_count"] == 0
    assert measurement.measurements["unchanged_retry_count"] == 0
    assert measurement.measurements["changed_retry_count"] == 0
    assert measurement.measurements["recovered_retry_count"] == 0
    assert detection.status == "analyzed"
    assert not detection.findings


def test_oversized_tool_observation_is_a_finding_not_a_failure():
    trajectory = Trajectory(
        trajectory_id="large-result",
        steps=(
            _tool_step("read", 0, arguments={"path": "large"}, output="x" * 65_536),
        ),
    )

    result = OversizedToolObservationDetector().detect(trajectory)

    assert result.status == "analyzed"
    assert result.findings[0].code == "oversized_tool_observation"
    assert result.findings[0].step_ids == ("1",)
    assert step_failure(trajectory.steps[0]) is None


def test_short_decision_churn_requires_covered_repeated_short_outputs():
    trajectory = Trajectory(
        trajectory_id="short-decisions",
        steps=tuple(
            _model_step(
                f"model-{index}",
                index,
                input_tokens=1000 + index,
                output_tokens=100,
            )
            for index in range(4)
        ),
    )

    measurement = ModelUsageMeasurer().measure(trajectory)
    result = ShortDecisionChurnDetector().detect(trajectory)

    assert measurement.measurements["output_under_500_tokens_call_count"] == 4
    assert measurement.measurements["output_under_500_tokens_ratio"] == 1.0
    assert result.findings[0].code == "short_decision_churn"
    assert result.findings[0].step_ids == ("1", "2", "3", "4")


def test_context_and_cache_detectors_consume_derived_measurements():
    trajectory = Trajectory(
        trajectory_id="growing-context",
        steps=(
            _model_step(
                "first",
                0,
                input_tokens=10_000,
                output_tokens=100,
                cached_tokens=8_000,
            ),
            _model_step(
                "last",
                1,
                input_tokens=20_000,
                output_tokens=100,
                cached_tokens=18_000,
            ),
        ),
    )
    measurements = (
        ModelUsageMeasurer().measure(trajectory),
        ContextUsageMeasurer().measure(trajectory),
    )

    context = ContextBloatWithoutCompactDetector().detect(
        trajectory, measurements=measurements
    )
    cache = CacheRetentionBloatDetector().detect(trajectory, measurements=measurements)

    assert context.findings[0].code == "context_bloat_without_compact"
    assert cache.findings[0].code == "cache_retention_bloat"


def test_runner_measures_before_measurement_dependent_detectors():
    trajectory = Trajectory(
        trajectory_id="runner-context",
        steps=(
            _model_step("first", 0, input_tokens=8_000, output_tokens=10),
            _model_step("last", 1, input_tokens=20_000, output_tokens=10),
        ),
    )
    run = TrajectoryAnalysisRunner(
        measurers=(ContextUsageMeasurer(),),
        detectors=(ContextBloatWithoutCompactDetector(),),
    ).run(
        TrajectoryDataset(
            dataset_id="dataset", version="v1", trajectories=(trajectory,)
        ),
        run_id="analysis",
    )

    assert run.detections[0].results[0].findings[0].code == (
        "context_bloat_without_compact"
    )


def test_measurement_threshold_verifier_applies_explicit_business_budget():
    trajectory = Trajectory(
        trajectory_id="budget",
        steps=(_model_step("model", 0, input_tokens=900, output_tokens=200),),
    )
    measurement = ModelUsageMeasurer().measure(trajectory)
    verifier = MeasurementThresholdVerifier(
        verifier_id="model_token_budget_v1",
        title="Model token budget",
        measurer_id="model_usage",
        measurement="total_tokens",
        threshold=1_000,
        owner="review-agent",
    )

    result = verifier.verify(trajectory, measurements=(measurement,))

    assert result.status == "verified"
    assert result.verdict == "fail"
    assert verifier.spec.rule_type == "hard"
    assert verifier.spec.category == "cost"
    assert "required <= 1000" in result.explanation
