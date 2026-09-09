from __future__ import annotations

from datetime import datetime, timezone

from trajectory_harness import (
    ContextUsageMeasurer,
    Failure,
    Metric,
    MetricSelector,
    ParetoSpec,
    PostCompactRefetchDetector,
    RetryLoopDetector,
    ToolUsageMeasurer,
    TrajectoryAnalysisRun,
    measure,
    render_report_html,
)
from trajectory_harness.model import (
    make_atif_step as Step,
    make_atif_trajectory as Trajectory,
    trajectory_from_dict,
    trajectory_to_dict,
)


def _tool_message(name: str, arguments: dict) -> dict:
    return {
        "role": "assistant",
        "parts": [{"type": "tool_call", "name": name, "arguments": arguments}],
    }


def _tool_step(
    step_id: str,
    *,
    start_ms: float,
    arguments: dict,
    duration_ms: float = 10,
    failure: Failure | None = None,
    output: str | None = None,
) -> Step:
    output_messages = (
        (
            {
                "role": "tool",
                "parts": [{"type": "tool_call_response", "content": output}],
            },
        )
        if output is not None
        else ()
    )
    return Step(
        step_id=step_id,
        parent_step_id=None,
        operation="execute_tool",
        name="execute_tool read",
        start_ms=start_ms,
        duration_ms=duration_ms,
        failure=failure,
        input_messages=(_tool_message("read", arguments),),
        output_messages=output_messages,
    )


def _model_step(step_id: str, start_ms: float, input_tokens: int) -> Step:
    return Step(
        step_id=step_id,
        parent_step_id=None,
        operation="chat",
        name="model",
        start_ms=start_ms,
        duration_ms=1,
        attributes={"gen_ai.usage.input_tokens": input_tokens},
    )


def test_generation_provenance_roundtrips_and_is_reported():
    trajectory = Trajectory(
        trajectory_id="t1",
        steps=(),
        generation={
            "agent_revision": "agent@abc123",
            "instruction_version": "prompt@7",
            "skill_version": "quality@0.0.6",
            "tool_contract_version": "tools@4",
            "model": "gpt-5",
            "loop_config": "compact@2",
            "orchestration": "lead+workers@3",
            "reasoning": "high",
        },
    )

    assert trajectory_from_dict(trajectory_to_dict(trajectory)) == trajectory

    html = render_report_html(
        [
            TrajectoryAnalysisRun(
                run_id="generation",
                created_at=datetime(2026, 8, 25, tzinfo=timezone.utc),
                dataset_id="reviews",
                dataset_version="v1",
                trajectory_ids=("t1",),
                trajectory_targets=(("t1", ""),),
                measurements=(measure(trajectory, [ContextUsageMeasurer()]),),
                measurer_specs=(ContextUsageMeasurer().spec,),
            )
        ]
    )
    assert "Generation provenance" in html
    assert "agent@abc123" in html
    assert "quality@0.0.6" in html


def test_tool_usage_measures_failure_result_coverage_and_concurrency():
    failure = Failure(kind="tool", phase="execute", error_type="invalid_argument")
    result = ToolUsageMeasurer().measure(
        Trajectory(
            trajectory_id="t1",
            steps=(
                _tool_step(
                    "read-1",
                    start_ms=0,
                    arguments={"path": "a"},
                    output="content",
                ),
                _tool_step(
                    "read-2",
                    start_ms=5,
                    arguments={"path": "b"},
                    failure=failure,
                ),
            ),
        )
    )

    assert result.status == "measured"
    assert result.measurements["tool_call_count"] == 2
    assert result.measurements["failed_tool_call_count"] == 1
    assert result.measurements["tool_failure_ratio"] == 0.5
    assert result.measurements["tool_duration_ms"] == 20
    assert result.measurements["result_reported_call_count"] == 1
    assert result.measurements["result_coverage_ratio"] == 0.5
    assert result.measurements["result_bytes"] > 0
    assert result.measurements["average_result_bytes_per_call"] > 0
    assert result.measurements["peak_result_bytes_per_call"] > 0
    assert result.measurements["max_concurrent_tool_calls"] == 2
    assert result.step_ids == ("1", "2")


def test_context_usage_measures_growth_and_compact_reduction():
    compact = Step(
        step_id="compact",
        parent_step_id=None,
        operation="context.compact",
        name="compact",
        start_ms=1,
        duration_ms=1,
    )
    result = ContextUsageMeasurer().measure(
        Trajectory(
            trajectory_id="t1",
            steps=(
                _model_step("before", 0, 100),
                compact,
                _model_step("after", 2, 40),
            ),
        )
    )

    assert result.status == "measured"
    assert result.measurements == {
        "model_call_count": 2,
        "input_reported_call_count": 2,
        "input_coverage_ratio": 1.0,
        "compact_count": 1,
        "first_input_tokens": 100,
        "last_input_tokens": 40,
        "peak_input_tokens": 100,
        "input_token_delta": -60,
        "input_growth_ratio": 0.4,
        "post_compact_observed_count": 1,
        "post_compact_input_delta_tokens": -60,
    }


def test_retry_loop_keeps_failed_and_recovered_attempts_visible():
    failure = Failure(kind="tool", phase="prepare", error_type="invalid_argument")
    trajectory = Trajectory(
        trajectory_id="t1",
        steps=(
            _tool_step(
                "failed",
                start_ms=0,
                arguments={"path": "wrong"},
                failure=failure,
            ),
            _tool_step(
                "recovered",
                start_ms=2,
                arguments={"path": "right"},
                output="content",
            ),
        ),
    )

    result = RetryLoopDetector().detect(trajectory)

    assert result.status == "analyzed"
    assert result.findings[0].code == "retry_loop"
    assert result.findings[0].step_ids == ("1", "2")
    assert "later recovered" in result.findings[0].summary


def test_post_compact_refetch_detects_exact_call_across_boundary():
    compact = Step(
        step_id="compact",
        parent_step_id=None,
        operation="context.compact",
        name="compact",
        start_ms=1,
        duration_ms=0.5,
    )
    trajectory = Trajectory(
        trajectory_id="t1",
        steps=(
            _tool_step(
                "before",
                start_ms=0,
                arguments={"path": "same"},
                output="content",
            ),
            compact,
            _tool_step(
                "after",
                start_ms=2,
                arguments={"path": "same"},
                output="content",
            ),
        ),
    )

    result = PostCompactRefetchDetector().detect(trajectory)

    assert result.status == "analyzed"
    assert result.findings[0].code == "post_compact_refetch"
    assert result.findings[0].step_ids == ("1", "3")


def _metric(
    run_id: str,
    name: str,
    value: float,
    aggregation: str,
    dimensions: tuple[tuple[str, str], ...] = (),
) -> Metric:
    return Metric(
        run_id=run_id,
        dataset_id="reviews",
        dataset_version=run_id,
        name=name,
        value=value,
        aggregation=aggregation,
        dimensions=dimensions,
    )


def _pareto_run(run_id: str, effect: float, cost: float) -> TrajectoryAnalysisRun:
    dimensions = (
        ("category", "effect"),
        ("verifier_id", "domain_effect"),
    )
    cost_dimensions = (
        ("category", "cost"),
        ("measurer_id", "model_usage"),
        ("measurement", "total_tokens"),
    )
    metrics = (
        _metric(run_id, "verification.pass", effect, "rate", dimensions),
        _metric(run_id, "measurement.value", cost, "mean", cost_dimensions),
        _metric(run_id, "execution.completion", 1.0, "rate"),
        _metric(run_id, "execution.duration_ms", 50.0, "p95"),
    )
    return TrajectoryAnalysisRun(
        run_id=run_id,
        created_at=datetime(2026, 8, 25, tzinfo=timezone.utc),
        dataset_id="reviews",
        dataset_version=run_id,
        trajectory_ids=(),
        metrics=metrics,
    )


def test_report_builds_explicit_effect_cost_pareto_comparison():
    pareto = ParetoSpec(
        effect=MetricSelector(
            name="verification.pass",
            aggregation="rate",
            objective="maximize",
            dimensions=(
                ("category", "effect"),
                ("verifier_id", "domain_effect"),
            ),
        ),
        cost=MetricSelector(
            name="measurement.value",
            aggregation="mean",
            objective="minimize",
            dimensions=(
                ("category", "cost"),
                ("measurer_id", "model_usage"),
                ("measurement", "total_tokens"),
            ),
        ),
    )

    html = render_report_html(
        [
            _pareto_run("baseline", effect=0.8, cost=100),
            _pareto_run("candidate", effect=0.9, cost=80),
        ],
        pareto=pareto,
    )

    assert "Effect-cost Pareto comparison" in html
    assert "baseline" in html
    assert "candidate" in html
    assert "Duration p95" in html
    assert 'data-v="yes">yes</td>' in html
    assert 'data-v="no">no</td>' in html
