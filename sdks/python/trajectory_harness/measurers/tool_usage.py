"""Measure executed tool usage without inferring whether calls were necessary."""

from __future__ import annotations

from dataclasses import dataclass

from atif import Step, Trajectory

from trajectory_harness._tool_calls import has_tool_execution, tool_output_bytes
from trajectory_harness.measure import MeasurementResult, MeasurementSpec, MeasurerSpec
from trajectory_harness.model import (
    step_duration_ms,
    step_failure,
    step_id,
    step_output_messages,
    step_start_ms,
)

_DISTRIBUTION_AGGREGATIONS = ("sum", "mean", "p50", "p95")


@dataclass(frozen=True, slots=True)
class ToolUsageMeasurer:
    """Measure executed tool calls, failures, latency, output size, and concurrency."""

    spec: MeasurerSpec = MeasurerSpec(
        measurer_id="tool_usage",
        title="Tool usage",
        description=(
            "Measure executed tool calls, normalized failures, duration, result "
            "coverage, result bytes, and observed concurrency."
        ),
        category="cost",
        owner="trajectory_harness",
        measurements=(
            MeasurementSpec(
                "tool_call_count",
                "count",
                "Executed tool calls.",
                "neutral",
                _DISTRIBUTION_AGGREGATIONS,
            ),
            MeasurementSpec(
                "failed_tool_call_count",
                "count",
                "Executed tool calls carrying a normalized failure.",
                "neutral",
                _DISTRIBUTION_AGGREGATIONS,
            ),
            MeasurementSpec(
                "tool_failure_ratio",
                "ratio",
                "Failed executed tool calls divided by all executed tool calls.",
                "lower_is_better",
                ("mean", "p50", "p95"),
            ),
            MeasurementSpec(
                "tool_duration_ms",
                "ms",
                "Total duration of executed tool calls.",
                "neutral",
                _DISTRIBUTION_AGGREGATIONS,
            ),
            MeasurementSpec(
                "result_reported_call_count",
                "count",
                "Executed tool calls carrying output messages.",
                "neutral",
                _DISTRIBUTION_AGGREGATIONS,
            ),
            MeasurementSpec(
                "result_coverage_ratio",
                "ratio",
                "Tool calls carrying output messages divided by executed calls.",
                "higher_is_better",
                ("mean", "p50", "p95"),
            ),
            MeasurementSpec(
                "result_bytes",
                "byte",
                "UTF-8 JSON size of reported tool output messages.",
                "neutral",
                _DISTRIBUTION_AGGREGATIONS,
            ),
            MeasurementSpec(
                "average_result_bytes_per_call",
                "byte",
                "Mean reported tool-output size across calls carrying output.",
                "neutral",
                ("mean", "p50", "p95"),
            ),
            MeasurementSpec(
                "peak_result_bytes_per_call",
                "byte",
                "Largest reported tool-output size for one call.",
                "neutral",
                ("mean", "p50", "p95"),
            ),
            MeasurementSpec(
                "max_concurrent_tool_calls",
                "count",
                "Largest number of overlapping executed tool-call steps.",
                "neutral",
                ("mean", "p50", "p95"),
            ),
        ),
    )

    def measure(self, trajectory: Trajectory) -> MeasurementResult:
        calls = tuple(step for step in trajectory.steps if has_tool_execution(step))
        if not calls:
            return MeasurementResult(
                measurer_id=self.spec.measurer_id,
                status="not_applicable",
                explanation="Trajectory contains no executed tool-call steps.",
            )

        failed = sum(step_failure(call) is not None for call in calls)
        reported = tuple(
            call
            for call in calls
            if call.observation is not None or step_output_messages(call)
        )
        result_sizes = tuple(tool_output_bytes(call) for call in reported)
        measurements: dict[str, float | int | bool] = {
            "tool_call_count": len(calls),
            "failed_tool_call_count": failed,
            "tool_failure_ratio": round(failed / len(calls), 6),
            "tool_duration_ms": round(sum(step_duration_ms(call) for call in calls), 6),
            "result_reported_call_count": len(reported),
            "result_coverage_ratio": round(len(reported) / len(calls), 6),
            "result_bytes": sum(result_sizes),
            "max_concurrent_tool_calls": _max_concurrency(calls),
        }
        if result_sizes:
            measurements["average_result_bytes_per_call"] = round(
                sum(result_sizes) / len(result_sizes), 6
            )
            measurements["peak_result_bytes_per_call"] = max(result_sizes)
        return MeasurementResult(
            measurer_id=self.spec.measurer_id,
            status="measured",
            measurements=measurements,
            explanation=(
                f"Observed {len(calls)} executed tool calls; "
                f"{len(reported)} reported output messages."
            ),
            step_ids=tuple(step_id(call) for call in calls),
        )


def _max_concurrency(calls: tuple[Step, ...]) -> int:
    intervals = [
        (
            step_start_ms(call),
            step_start_ms(call) + step_duration_ms(call),
        )
        for call in calls
        if step_duration_ms(call) > 0
    ]
    if not intervals:
        return 1
    events = sorted(
        (event for start, end in intervals for event in ((start, 1), (end, -1))),
        key=lambda item: (item[0], item[1]),
    )
    current = 0
    peak = 0
    for _, change in events:
        current += change
        peak = max(peak, current)
    return max(1, peak)
