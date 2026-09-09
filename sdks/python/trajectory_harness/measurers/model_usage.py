"""Measure model-call and token usage without judging trajectory quality."""

from __future__ import annotations

from dataclasses import dataclass

from trajectory_harness.measure import MeasurementResult, MeasurementSpec, MeasurerSpec
from trajectory_harness.measurers._tokens import (
    CACHED_INPUT_TOKEN_ATTRIBUTES,
    INPUT_TOKEN_ATTRIBUTES,
    MODEL_OPERATIONS,
    OUTPUT_TOKEN_ATTRIBUTES,
    token_count,
)
from atif import Trajectory

from trajectory_harness.model import step_id, step_operation

_DISTRIBUTION_AGGREGATIONS = ("sum", "mean", "p50", "p95")


@dataclass(frozen=True, slots=True)
class ModelUsageMeasurer:
    """Measure model calls and reported token usage across one trajectory."""

    spec: MeasurerSpec = MeasurerSpec(
        measurer_id="model_usage",
        title="Model usage",
        description="Measure model calls, token usage, and prompt-cache efficiency.",
        category="cost",
        owner="trajectory_harness",
        measurements=(
            MeasurementSpec(
                "model_call_count",
                "count",
                "Observed model calls.",
                "neutral",
                _DISTRIBUTION_AGGREGATIONS,
            ),
            MeasurementSpec(
                "usage_reported_call_count",
                "count",
                "Model calls reporting at least one token usage field.",
                "neutral",
                _DISTRIBUTION_AGGREGATIONS,
            ),
            MeasurementSpec(
                "usage_coverage_ratio",
                "ratio",
                "Model calls with token usage divided by observed model calls.",
                "higher_is_better",
                ("mean", "p50", "p95"),
            ),
            MeasurementSpec(
                "input_tokens",
                "token",
                "Reported input tokens, including cached input when provided that way.",
                "neutral",
                _DISTRIBUTION_AGGREGATIONS,
            ),
            MeasurementSpec(
                "output_tokens",
                "token",
                "Reported output tokens.",
                "neutral",
                _DISTRIBUTION_AGGREGATIONS,
            ),
            MeasurementSpec(
                "output_reported_call_count",
                "count",
                "Model calls reporting output tokens.",
                "neutral",
                _DISTRIBUTION_AGGREGATIONS,
            ),
            MeasurementSpec(
                "average_output_tokens_per_call",
                "token",
                "Mean output tokens across calls that expose output usage.",
                "neutral",
                ("mean", "p50", "p95"),
            ),
            MeasurementSpec(
                "peak_output_tokens_per_call",
                "token",
                "Largest reported output token count for one model call.",
                "neutral",
                ("mean", "p50", "p95"),
            ),
            MeasurementSpec(
                "output_under_500_tokens_call_count",
                "count",
                "Model calls reporting fewer than 500 output tokens.",
                "neutral",
                _DISTRIBUTION_AGGREGATIONS,
            ),
            MeasurementSpec(
                "output_under_500_tokens_ratio",
                "ratio",
                "Covered model calls under 500 output tokens divided by covered calls.",
                "neutral",
                ("mean", "p50", "p95"),
            ),
            MeasurementSpec(
                "total_tokens",
                "token",
                "Sum of reported input and output tokens.",
                "neutral",
                _DISTRIBUTION_AGGREGATIONS,
            ),
            MeasurementSpec(
                "average_input_tokens_per_call",
                "token",
                "Mean reported input tokens across calls that expose input usage.",
                "neutral",
                ("mean", "p50", "p95"),
            ),
            MeasurementSpec(
                "peak_input_tokens_per_call",
                "token",
                "Largest reported input token count for one model call.",
                "neutral",
                ("mean", "p50", "p95"),
            ),
            MeasurementSpec(
                "cached_input_tokens",
                "token",
                "Reported input tokens served from a prompt cache.",
                "neutral",
                _DISTRIBUTION_AGGREGATIONS,
            ),
            MeasurementSpec(
                "uncached_input_tokens",
                "token",
                "Derived non-cached input for calls where cached input is a subset of input.",
                "neutral",
                _DISTRIBUTION_AGGREGATIONS,
            ),
            MeasurementSpec(
                "cache_hit_ratio",
                "ratio",
                "Cached input tokens divided by input tokens with cache data.",
                "neutral",
                ("mean", "p50", "p95"),
            ),
        ),
    )

    def measure(self, trajectory: Trajectory) -> MeasurementResult:
        calls = [
            step
            for step in trajectory.steps
            if step_operation(step) in MODEL_OPERATIONS
        ]
        if not calls:
            return MeasurementResult(
                measurer_id=self.spec.measurer_id,
                status="not_applicable",
                explanation="Trajectory contains no model calls.",
            )

        input_tokens = 0
        output_tokens = 0
        cached_input_tokens = 0
        paired_input_tokens = 0
        paired_cached_tokens = 0
        input_reported = False
        output_reported = False
        cached_reported = False
        usage_reported_calls = 0
        reported_call_inputs: list[int] = []
        reported_call_outputs: list[int] = []

        for call in calls:
            call_input = token_count(call, INPUT_TOKEN_ATTRIBUTES)
            call_output = token_count(call, OUTPUT_TOKEN_ATTRIBUTES)
            call_cached = token_count(call, CACHED_INPUT_TOKEN_ATTRIBUTES)
            if any(
                value is not None for value in (call_input, call_output, call_cached)
            ):
                usage_reported_calls += 1
            if call_input is not None:
                input_reported = True
                input_tokens += call_input
                reported_call_inputs.append(call_input)
            if call_output is not None:
                output_reported = True
                output_tokens += call_output
                reported_call_outputs.append(call_output)
            if call_cached is not None:
                cached_reported = True
                cached_input_tokens += call_cached
            if (
                call_input is not None
                and call_cached is not None
                and call_cached <= call_input
            ):
                paired_input_tokens += call_input
                paired_cached_tokens += call_cached

        measurements: dict[str, float | int | bool] = {
            "model_call_count": len(calls),
            "usage_reported_call_count": usage_reported_calls,
            "usage_coverage_ratio": round(usage_reported_calls / len(calls), 6),
        }
        if input_reported:
            measurements["input_tokens"] = input_tokens
            measurements["average_input_tokens_per_call"] = round(
                input_tokens / len(reported_call_inputs), 6
            )
            measurements["peak_input_tokens_per_call"] = max(reported_call_inputs)
        if output_reported:
            measurements["output_tokens"] = output_tokens
            measurements["output_reported_call_count"] = len(reported_call_outputs)
            measurements["average_output_tokens_per_call"] = round(
                output_tokens / len(reported_call_outputs), 6
            )
            measurements["peak_output_tokens_per_call"] = max(reported_call_outputs)
            under_500 = sum(value < 500 for value in reported_call_outputs)
            measurements["output_under_500_tokens_call_count"] = under_500
            measurements["output_under_500_tokens_ratio"] = round(
                under_500 / len(reported_call_outputs), 6
            )
        if input_reported or output_reported:
            measurements["total_tokens"] = input_tokens + output_tokens
        if cached_reported:
            measurements["cached_input_tokens"] = cached_input_tokens
        if paired_input_tokens:
            measurements["uncached_input_tokens"] = (
                paired_input_tokens - paired_cached_tokens
            )
            measurements["cache_hit_ratio"] = round(
                paired_cached_tokens / paired_input_tokens, 6
            )

        return MeasurementResult(
            measurer_id=self.spec.measurer_id,
            status="measured",
            measurements=measurements,
            explanation=(
                f"Observed {len(calls)} model calls; "
                f"{usage_reported_calls} reported token usage."
            ),
            step_ids=tuple(
                step_id(call)
                for call in calls
                if token_count(call, OUTPUT_TOKEN_ATTRIBUTES) is not None
            ),
        )
