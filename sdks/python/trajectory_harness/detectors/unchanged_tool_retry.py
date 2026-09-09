"""Detect retries that repeat a failed tool call without changing arguments."""

from __future__ import annotations

from dataclasses import dataclass

from trajectory_harness._tool_calls import tool_calls, tool_retry_transitions
from trajectory_harness.detect import DetectionResult, DetectorSpec, Finding
from trajectory_harness.measure import Measurements
from atif import Trajectory

from trajectory_harness.model import step_id


@dataclass(frozen=True, slots=True)
class UnchangedToolRetryDetector:
    spec: DetectorSpec = DetectorSpec(
        detector_id="unchanged_tool_retry",
        title="Unchanged tool retry",
        description=(
            "Detect a failed tool call followed by the same tool and exact arguments."
        ),
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

        unchanged = tuple(
            item
            for item in tool_retry_transitions(trajectory.steps)
            if not item.arguments_changed
        )
        if not unchanged:
            return DetectionResult(
                detector_id=self.spec.detector_id,
                status="analyzed",
                explanation="No failed tool call was retried with identical arguments.",
            )

        step_ids = tuple(
            dict.fromkeys(
                current_step_id
                for item in unchanged
                for current_step_id in (
                    step_id(item.failed.step),
                    step_id(item.retry.step),
                )
            )
        )
        summary = (
            f"{len(unchanged)} failed tool calls were retried with unchanged arguments."
        )
        return DetectionResult(
            detector_id=self.spec.detector_id,
            status="analyzed",
            explanation=summary,
            findings=(
                Finding(
                    code="unchanged_tool_retry",
                    severity="warning",
                    summary=summary,
                    step_ids=step_ids,
                    hypotheses=(
                        "The agent may be retrying without incorporating failure evidence.",
                        "The tool contract may not expose an actionable error.",
                        "A transient failure may make an unchanged retry appropriate.",
                    ),
                ),
            ),
        )
