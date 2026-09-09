"""Detect tool observations large enough to dominate agent context."""

from __future__ import annotations

from dataclasses import dataclass

from atif import Trajectory

from trajectory_harness._tool_calls import has_tool_execution, tool_output_bytes
from trajectory_harness.detect import DetectionResult, DetectorSpec, Finding
from trajectory_harness.measure import Measurements
from trajectory_harness.model import (
    step_id,
    step_output_messages,
)

MAX_TOOL_OUTPUT_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class OversizedToolObservationDetector:
    spec: DetectorSpec = DetectorSpec(
        detector_id="oversized_tool_observation",
        title="Oversized tool observation",
        description=(
            "Detect reported tool outputs larger than the common 64 KiB diagnostic "
            "threshold."
        ),
        category="cost",
        kind="common",
        owner="trajectory_harness",
    )

    def detect(
        self, trajectory: Trajectory, *, measurements: Measurements = ()
    ) -> DetectionResult:
        del measurements
        reported = tuple(
            step
            for step in trajectory.steps
            if has_tool_execution(step)
            and (step.observation is not None or step_output_messages(step))
        )
        if not reported:
            return DetectionResult(
                detector_id=self.spec.detector_id,
                status="not_applicable",
                explanation="Trajectory contains no reported tool output.",
            )

        sized = tuple((step, tool_output_bytes(step)) for step in reported)
        oversized = tuple(
            (step, size) for step, size in sized if size > MAX_TOOL_OUTPUT_BYTES
        )
        if not oversized:
            return DetectionResult(
                detector_id=self.spec.detector_id,
                status="analyzed",
                explanation="No tool output exceeds 64 KiB.",
            )

        peak = max(size for _, size in oversized)
        summary = (
            f"{len(oversized)} tool outputs exceed 64 KiB; the largest is {peak} bytes."
        )
        return DetectionResult(
            detector_id=self.spec.detector_id,
            status="analyzed",
            explanation=summary,
            findings=(
                Finding(
                    code="oversized_tool_observation",
                    severity="warning",
                    summary=summary,
                    step_ids=tuple(step_id(step) for step, _ in oversized),
                    hypotheses=(
                        "The tool may need field, range, or time-window filters.",
                        "The result contract may need pagination or lazy loading.",
                        "The full result may be necessary for this task.",
                    ),
                ),
            ),
        )
