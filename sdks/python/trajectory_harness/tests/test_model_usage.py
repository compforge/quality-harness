from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import dataclass

from trajectory_harness import (
    ModelUsageMeasurer,
    MeasurementResult,
    MeasurerSpec,
    TrajectoryAnalysisRun,
    aggregate_metrics,
    measure,
    render_report_html,
)
from trajectory_harness.model import (
    make_atif_step as Step,
    make_atif_trajectory as Trajectory,
)


def _model_step(step_id: str, operation: str = "inference", **attributes) -> Step:
    return Step(
        step_id=step_id,
        parent_step_id=None,
        operation=operation,
        name="model",
        start_ms=0,
        duration_ms=1,
        attributes=attributes,
    )


def test_model_usage_accepts_otel_and_framework_token_names():
    trajectory = Trajectory(
        trajectory_id="t1",
        steps=(
            _model_step(
                "otel",
                operation="chat",
                **{
                    "gen_ai.usage.input_tokens": 100,
                    "gen_ai.usage.output_tokens": "20",
                    "gen_ai.usage.cached_input_tokens": 40,
                },
            ),
            _model_step(
                "framework",
                prompt_tokens=50,
                completion_tokens=10,
                cached_tokens=5,
            ),
            _model_step("missing"),
        ),
    )

    result = ModelUsageMeasurer().measure(trajectory)

    assert result.status == "measured"
    assert result.measurements == {
        "model_call_count": 3,
        "usage_reported_call_count": 2,
        "usage_coverage_ratio": 0.666667,
        "input_tokens": 150,
        "average_input_tokens_per_call": 75.0,
        "peak_input_tokens_per_call": 100,
        "output_tokens": 30,
        "output_reported_call_count": 2,
        "average_output_tokens_per_call": 15.0,
        "peak_output_tokens_per_call": 20,
        "output_under_500_tokens_call_count": 2,
        "output_under_500_tokens_ratio": 1.0,
        "total_tokens": 180,
        "cached_input_tokens": 45,
        "uncached_input_tokens": 105,
        "cache_hit_ratio": 0.3,
    }


def test_model_usage_counts_calls_without_inventing_missing_token_values():
    result = ModelUsageMeasurer().measure(
        Trajectory(trajectory_id="t1", steps=(_model_step("s1"),))
    )

    assert result.measurements == {
        "model_call_count": 1,
        "usage_reported_call_count": 0,
        "usage_coverage_ratio": 0.0,
    }


def test_model_usage_accepts_otel_cache_read_input_tokens():
    result = ModelUsageMeasurer().measure(
        Trajectory(
            trajectory_id="t1",
            steps=(
                _model_step(
                    "s1",
                    **{
                        "gen_ai.usage.input_tokens": 100,
                        "gen_ai.usage.cache_read.input_tokens": 80,
                    },
                ),
            ),
        )
    )

    assert result.measurements["cached_input_tokens"] == 80
    assert result.measurements["uncached_input_tokens"] == 20
    assert result.measurements["cache_hit_ratio"] == 0.8


def test_model_usage_is_not_applicable_without_model_calls():
    result = ModelUsageMeasurer().measure(
        Trajectory(
            trajectory_id="t1",
            steps=(
                Step(
                    step_id="tool",
                    parent_step_id=None,
                    operation="execute_tool",
                    name="read",
                    start_ms=0,
                    duration_ms=1,
                    attributes={"prompt_tokens": 999},
                ),
            ),
        )
    )

    assert result.status == "not_applicable"
    assert result.measurements == {}


def test_model_usage_measurements_aggregate_dataset_sum_and_distribution():
    measurer = ModelUsageMeasurer()
    trajectories = (
        Trajectory(
            trajectory_id="t1",
            steps=(_model_step("s1", prompt_tokens=100, completion_tokens=10),),
        ),
        Trajectory(
            trajectory_id="t2",
            steps=(_model_step("s2", prompt_tokens=300, completion_tokens=30),),
        ),
    )
    run = TrajectoryAnalysisRun(
        run_id="weekly",
        created_at=datetime(2026, 8, 23, tzinfo=timezone.utc),
        dataset_id="reviews",
        dataset_version="",
        trajectory_ids=tuple(item.trajectory_id for item in trajectories),
        trajectory_targets=tuple((item.trajectory_id, "") for item in trajectories),
        measurements=tuple(measure(item, [measurer]) for item in trajectories),
        measurer_specs=(measurer.spec,),
        annotation_count=2,
    )

    metrics = {metric.qualified_name: metric.value for metric in aggregate_metrics(run)}

    dimensions = "{category=cost,measurer_id=model_usage,measurement=input_tokens}"
    assert metrics[f"measurement.value.sum{dimensions}"] == 400
    assert metrics[f"measurement.value.mean{dimensions}"] == 200
    assert metrics[f"measurement.value.p50{dimensions}"] == 100
    assert metrics[f"measurement.value.p95{dimensions}"] == 300


def test_measurer_exception_is_health_data_not_zero_cost():
    @dataclass(frozen=True)
    class BrokenMeasurer:
        spec: MeasurerSpec = MeasurerSpec(
            measurer_id="broken",
            title="Broken",
            description="Test measurer failure.",
            category="cost",
        )

        def measure(self, trajectory):
            del trajectory
            raise RuntimeError("usage unavailable")

    result = measure(
        Trajectory(trajectory_id="t1", steps=()), [BrokenMeasurer()]
    ).results

    assert result == (
        MeasurementResult(
            measurer_id="broken",
            status="error",
            explanation="RuntimeError: usage unavailable",
        ),
    )


def test_report_lists_measurements_separately_from_verifiers():
    measurer = ModelUsageMeasurer()
    trajectory = Trajectory(
        trajectory_id="t1",
        steps=(_model_step("s1", prompt_tokens=100),),
    )
    run = TrajectoryAnalysisRun(
        run_id="weekly",
        created_at=datetime(2026, 8, 23, tzinfo=timezone.utc),
        dataset_id="reviews",
        dataset_version="",
        trajectory_ids=(trajectory.trajectory_id,),
        trajectory_targets=((trajectory.trajectory_id, ""),),
        measurements=(measure(trajectory, [measurer]),),
        measurer_specs=(measurer.spec,),
        annotation_count=1,
    )

    html = render_report_html([run])

    assert "Measurer catalog" in html
    assert "model_usage" in html
