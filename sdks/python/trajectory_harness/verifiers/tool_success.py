"""Verify tool execution success across a trajectory."""

from __future__ import annotations

from dataclasses import dataclass

from atif import Trajectory

from trajectory_harness._tool_calls import has_tool_execution
from trajectory_harness.model import (
    step_failure,
    step_id,
    step_status,
)
from trajectory_harness.measure import Measurements
from trajectory_harness.verify import VerificationResult, VerifierSpec


@dataclass(frozen=True, slots=True)
class ToolSuccessVerifier:
    spec: VerifierSpec = VerifierSpec(
        verifier_id="tool_success",
        title="Tool success",
        description="Check whether tool executions in the trajectory succeeded.",
        category="effect",
        kind="common",
        owner="trajectory_harness",
    )

    def verify(
        self,
        trajectory: Trajectory,
        *,
        measurements: Measurements = (),
        reference: Trajectory | None = None,
    ) -> VerificationResult:
        del measurements, reference
        calls = [step for step in trajectory.steps if has_tool_execution(step)]
        if not calls:
            return VerificationResult(
                verifier_id=self.spec.verifier_id,
                status="not_applicable",
                explanation="Trajectory contains no executed tool calls.",
            )

        failed = [
            step for step in calls if step_failure(step) or step_status(step) == "error"
        ]
        success_rate = round((len(calls) - len(failed)) / len(calls), 3)
        return VerificationResult(
            verifier_id=self.spec.verifier_id,
            status="verified",
            verdict="pass" if not failed else "fail",
            score=success_rate,
            explanation=(
                f"{len(calls) - len(failed)} of {len(calls)} tool calls succeeded."
            ),
            step_ids=tuple(step_id(step) for step in failed),
        )
