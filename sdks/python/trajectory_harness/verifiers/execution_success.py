"""Verify whether a trajectory reached a successful execution outcome."""

from __future__ import annotations

from dataclasses import dataclass

from atif import Trajectory

from trajectory_harness.model import trajectory_execution
from trajectory_harness.measure import Measurements
from trajectory_harness.verify import VerificationResult, VerifierSpec


@dataclass(frozen=True, slots=True)
class ExecutionSuccessVerifier:
    spec: VerifierSpec = VerifierSpec(
        verifier_id="execution_success",
        title="Execution success",
        description="Check the authoritative final outcome of the trajectory.",
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
        execution = trajectory_execution(trajectory)
        if execution is None or execution.outcome == "unknown":
            return VerificationResult(
                verifier_id=self.spec.verifier_id,
                status="not_applicable",
                explanation="Trajectory has no authoritative execution outcome.",
            )

        success = execution.outcome == "completed"
        return VerificationResult(
            verifier_id=self.spec.verifier_id,
            status="verified",
            verdict="pass" if success else "fail",
            score=1.0 if success else 0.0,
            explanation=f"Execution outcome is {execution.outcome}.",
        )
