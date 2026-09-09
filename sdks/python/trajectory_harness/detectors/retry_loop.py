"""Detect action retries that follow an observed execution failure."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from atif import Step, Trajectory

from trajectory_harness._tool_calls import tool_names
from trajectory_harness.detect import DetectionResult, DetectorSpec, Finding
from trajectory_harness.measure import Measurements
from trajectory_harness.model import (
    step_failure,
    step_id,
    step_name,
    step_operation,
)


@dataclass(frozen=True, slots=True)
class RetryLoopDetector:
    spec: DetectorSpec = DetectorSpec(
        detector_id="retry_loop",
        title="Retry loops",
        description="Detect repeated actions after an observed operation failure.",
        category="cost",
        kind="common",
        owner="trajectory_harness",
    )

    def detect(
        self, trajectory: Trajectory, *, measurements: Measurements = ()
    ) -> DetectionResult:
        del measurements
        if not trajectory.steps:
            return DetectionResult(
                detector_id=self.spec.detector_id,
                status="not_applicable",
                explanation="Trajectory contains no actions.",
            )
        failed_steps = tuple(step for step in trajectory.steps if step_failure(step))
        if not failed_steps:
            return DetectionResult(
                detector_id=self.spec.detector_id,
                status="analyzed",
                explanation="Trajectory contains no failed action that could be retried.",
            )

        groups: dict[tuple[str, str], list[Step]] = defaultdict(list)
        for step in trajectory.steps:
            groups[_action_key(step)].append(step)

        findings = []
        for attempts in groups.values():
            first_failure = next(
                (index for index, step in enumerate(attempts) if step_failure(step)),
                None,
            )
            if first_failure is None or first_failure == len(attempts) - 1:
                continue
            retried = attempts[first_failure:]
            later = retried[1:]
            recovered = any(step_failure(step) is None for step in later)
            findings.append(
                Finding(
                    code="retry_loop",
                    severity="warning",
                    summary=(
                        f"Action was attempted {len(retried)} times after an observed "
                        f"failure and {'later recovered' if recovered else 'kept failing'}."
                    ),
                    step_ids=tuple(step_id(step) for step in retried),
                    hypotheses=(
                        "The tool or model contract may require argument repair.",
                        "The runtime may be retrying without a bounded convergence rule.",
                        "A transient dependency failure may have required the retry.",
                    ),
                )
            )

        if not findings:
            return DetectionResult(
                detector_id=self.spec.detector_id,
                status="analyzed",
                explanation="No failed action was attempted again.",
            )
        return DetectionResult(
            detector_id=self.spec.detector_id,
            status="analyzed",
            explanation=f"Detected {len(findings)} retry-loop action groups.",
            findings=tuple(findings),
        )


def _action_key(step: Step) -> tuple[str, str]:
    operation = step_operation(step)
    names = tool_names(step) if operation == "execute_tool" else ()
    action_name = ",".join(names) if names else step_name(step)
    return (operation, action_name)
