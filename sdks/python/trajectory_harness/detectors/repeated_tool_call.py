"""Detect exact repeated tool calls, a common agent-loop churn finding."""

from __future__ import annotations

from dataclasses import dataclass

from trajectory_harness._tool_calls import tool_calls
from trajectory_harness.detect import DetectionResult, DetectorSpec, Finding
from trajectory_harness.measure import Measurements
from atif import Trajectory

from trajectory_harness.model import step_id


@dataclass(frozen=True, slots=True)
class RepeatedToolCallDetector:
    spec: DetectorSpec = DetectorSpec(
        detector_id="repeated_tool_call",
        title="Repeated tool calls",
        description="Detect exact repeats of an earlier tool name and arguments.",
        category="cost",
        kind="common",
        owner="trajectory_harness",
    )

    def detect(
        self, trajectory: Trajectory, *, measurements: Measurements = ()
    ) -> DetectionResult:
        del measurements
        calls = tool_calls(trajectory.steps)
        if not calls:
            return DetectionResult(
                detector_id=self.spec.detector_id,
                status="not_applicable",
                explanation="Trajectory contains no tool calls.",
            )

        seen = set()
        duplicate_steps = []
        for call in calls:
            if call.signature in seen:
                duplicate_steps.append(step_id(call.step))
            else:
                seen.add(call.signature)

        if duplicate_steps:
            summary = (
                f"{len(duplicate_steps)} of {len(calls)} tool calls repeat an earlier "
                "tool name and arguments."
            )
            return DetectionResult(
                detector_id=self.spec.detector_id,
                status="analyzed",
                explanation=f"{summary} Inspect whether batching or reuse would help.",
                findings=(
                    Finding(
                        code="repeated_tool_call",
                        severity="warning",
                        summary=summary,
                        step_ids=tuple(duplicate_steps),
                        hypotheses=(
                            "The tool may be missing a batch operation.",
                            "The tool description may not encourage batching or result reuse.",
                            "The agent loop may lack an effective repeat-call stopping strategy.",
                        ),
                    ),
                ),
            )
        return DetectionResult(
            detector_id=self.spec.detector_id,
            status="analyzed",
            explanation=f"All {len(calls)} tool calls are distinct.",
        )
