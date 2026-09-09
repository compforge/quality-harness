"""Measure failed tool calls and the argument delta of their next retry."""

from __future__ import annotations

from dataclasses import dataclass

from trajectory_harness._tool_calls import tool_calls, tool_retry_transitions
from trajectory_harness.measure import MeasurementResult, MeasurementSpec, MeasurerSpec
from atif import Trajectory

from trajectory_harness.model import step_failure, step_id, step_status

_DISTRIBUTION_AGGREGATIONS = ("sum", "mean", "p50", "p95")


@dataclass(frozen=True, slots=True)
class RetryUsageMeasurer:
    """Measure whether failed tool calls are retried with an argument change."""

    spec: MeasurerSpec = MeasurerSpec(
        measurer_id="retry_usage",
        title="Retry usage",
        description=(
            "Measure failed tool calls, retry coverage, argument changes, and recovery."
        ),
        category="cost",
        owner="trajectory_harness",
        measurements=(
            MeasurementSpec(
                "failed_tool_call_count",
                "count",
                "Tool calls carrying a normalized failure or error status.",
                "neutral",
                _DISTRIBUTION_AGGREGATIONS,
            ),
            MeasurementSpec(
                "retried_failed_tool_call_count",
                "count",
                "Failed tool calls followed by another call to the same tool.",
                "neutral",
                _DISTRIBUTION_AGGREGATIONS,
            ),
            MeasurementSpec(
                "retried_failure_ratio",
                "ratio",
                "Retried failed tool calls divided by failed tool calls.",
                "neutral",
                ("mean", "p50", "p95"),
            ),
            MeasurementSpec(
                "unchanged_retry_count",
                "count",
                "Retries that repeat the failed tool name and exact arguments.",
                "neutral",
                _DISTRIBUTION_AGGREGATIONS,
            ),
            MeasurementSpec(
                "changed_retry_count",
                "count",
                "Retries to the same tool with different arguments.",
                "neutral",
                _DISTRIBUTION_AGGREGATIONS,
            ),
            MeasurementSpec(
                "recovered_retry_count",
                "count",
                "Retries whose next same-tool call did not report a failure.",
                "neutral",
                _DISTRIBUTION_AGGREGATIONS,
            ),
        ),
    )

    def measure(self, trajectory: Trajectory) -> MeasurementResult:
        calls = tool_calls(trajectory.steps)
        if not calls:
            return MeasurementResult(
                measurer_id=self.spec.measurer_id,
                status="not_applicable",
                explanation="Trajectory contains no tool calls.",
            )

        failed = tuple(
            call
            for call in calls
            if step_failure(call.step) is not None or step_status(call.step) == "error"
        )
        transitions = tool_retry_transitions(trajectory.steps)
        unchanged = sum(not item.arguments_changed for item in transitions)
        changed = sum(item.arguments_changed for item in transitions)
        recovered = sum(item.recovered for item in transitions)
        measurements: dict[str, float | int | bool] = {
            "failed_tool_call_count": len(failed),
            "retried_failed_tool_call_count": len(transitions),
            "unchanged_retry_count": unchanged,
            "changed_retry_count": changed,
            "recovered_retry_count": recovered,
        }
        if failed:
            measurements["retried_failure_ratio"] = round(
                len(transitions) / len(failed), 6
            )

        return MeasurementResult(
            measurer_id=self.spec.measurer_id,
            status="measured",
            measurements=measurements,
            explanation=(
                f"Observed {len(failed)} failed tool calls and "
                f"{len(transitions)} same-tool retries."
            ),
            step_ids=tuple(
                dict.fromkeys(
                    current_step_id
                    for item in transitions
                    for current_step_id in (
                        step_id(item.failed.step),
                        step_id(item.retry.step),
                    )
                )
            ),
        )
