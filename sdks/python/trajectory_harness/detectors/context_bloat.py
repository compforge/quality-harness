"""Detect large context growth without an observed compaction boundary."""

from __future__ import annotations

from dataclasses import dataclass

from trajectory_harness._measurements import measured_result, numeric_measurement
from trajectory_harness.detect import DetectionResult, DetectorSpec, Finding
from trajectory_harness.measure import Measurements
from atif import Trajectory

MIN_LAST_INPUT_TOKENS = 16_000
MIN_INPUT_GROWTH_RATIO = 2.0


@dataclass(frozen=True, slots=True)
class ContextBloatWithoutCompactDetector:
    spec: DetectorSpec = DetectorSpec(
        detector_id="context_bloat_without_compact",
        title="Context bloat without compaction",
        description=(
            "Detect context that reaches 16k input tokens, doubles from its first "
            "covered call, and has no observed compact step."
        ),
        category="cost",
        kind="common",
        owner="trajectory_harness",
    )

    def detect(
        self, trajectory: Trajectory, *, measurements: Measurements = ()
    ) -> DetectionResult:
        result = measured_result(measurements, "context_usage")
        last = numeric_measurement(measurements, "context_usage", "last_input_tokens")
        growth = numeric_measurement(
            measurements, "context_usage", "input_growth_ratio"
        )
        compacts = numeric_measurement(measurements, "context_usage", "compact_count")
        if result is None or last is None or growth is None or compacts is None:
            return DetectionResult(
                detector_id=self.spec.detector_id,
                status="not_applicable",
                explanation="Context usage measurements are incomplete.",
            )

        if (
            last < MIN_LAST_INPUT_TOKENS
            or growth < MIN_INPUT_GROWTH_RATIO
            or compacts > 0
        ):
            return DetectionResult(
                detector_id=self.spec.detector_id,
                status="analyzed",
                explanation=(
                    f"Last input is {last:g} tokens at {growth:g}x growth with "
                    f"{compacts:g} compact steps."
                ),
            )

        summary = (
            f"Input context grew to {last:g} tokens ({growth:g}x) without compaction."
        )
        return DetectionResult(
            detector_id=self.spec.detector_id,
            status="analyzed",
            explanation=summary,
            findings=(
                Finding(
                    code="context_bloat_without_compact",
                    severity="warning",
                    summary=summary,
                    step_ids=result.step_ids,
                    hypotheses=(
                        "The loop may lack an effective context budget or compact trigger.",
                        "Several tasks may be sharing one session boundary.",
                        "The retained context may still be necessary for the task.",
                    ),
                ),
            ),
        )
