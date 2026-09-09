"""Detect high cache reuse coupled with large and growing model context."""

from __future__ import annotations

from dataclasses import dataclass

from trajectory_harness._measurements import measured_result, numeric_measurement
from trajectory_harness.detect import DetectionResult, DetectorSpec, Finding
from trajectory_harness.measure import Measurements
from atif import Trajectory

MIN_CACHE_HIT_RATIO = 0.8
MIN_PEAK_INPUT_TOKENS = 16_000
MIN_INPUT_GROWTH_RATIO = 1.5


@dataclass(frozen=True, slots=True)
class CacheRetentionBloatDetector:
    spec: DetectorSpec = DetectorSpec(
        detector_id="cache_retention_bloat",
        title="Cache retention bloat",
        description=(
            "Detect cache hit ratio of at least 80% coupled with a 16k-token peak "
            "context and at least 1.5x input growth."
        ),
        category="cost",
        kind="common",
        owner="trajectory_harness",
    )

    def detect(
        self, trajectory: Trajectory, *, measurements: Measurements = ()
    ) -> DetectionResult:
        del trajectory
        model_result = measured_result(measurements, "model_usage")
        context_result = measured_result(measurements, "context_usage")
        cache_ratio = numeric_measurement(
            measurements, "model_usage", "cache_hit_ratio"
        )
        peak = numeric_measurement(
            measurements, "model_usage", "peak_input_tokens_per_call"
        )
        growth = numeric_measurement(
            measurements, "context_usage", "input_growth_ratio"
        )
        if (
            model_result is None
            or context_result is None
            or cache_ratio is None
            or peak is None
            or growth is None
        ):
            return DetectionResult(
                detector_id=self.spec.detector_id,
                status="not_applicable",
                explanation="Cache and context growth measurements are incomplete.",
            )

        if (
            cache_ratio < MIN_CACHE_HIT_RATIO
            or peak < MIN_PEAK_INPUT_TOKENS
            or growth < MIN_INPUT_GROWTH_RATIO
        ):
            return DetectionResult(
                detector_id=self.spec.detector_id,
                status="analyzed",
                explanation=(
                    f"Cache hit ratio is {cache_ratio:.1%}, peak input is {peak:g} "
                    f"tokens, and input growth is {growth:g}x."
                ),
            )

        summary = (
            f"Cache hit ratio is {cache_ratio:.1%} while context peaks at {peak:g} "
            f"tokens and grows {growth:g}x."
        )
        return DetectionResult(
            detector_id=self.spec.detector_id,
            status="analyzed",
            explanation=summary,
            findings=(
                Finding(
                    code="cache_retention_bloat",
                    severity="warning",
                    summary=summary,
                    step_ids=tuple(
                        dict.fromkeys(model_result.step_ids + context_result.step_ids)
                    ),
                    hypotheses=(
                        "A large stale prefix may be retained only to preserve cache hits.",
                        "The session may need a task boundary or compact handoff.",
                        "The cached context may still be relevant and economical.",
                    ),
                ),
            ),
        )
