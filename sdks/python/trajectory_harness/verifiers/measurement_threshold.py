"""Verify an explicit threshold against one derived trajectory measurement."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from trajectory_harness.measure import Measurements
from atif import Trajectory

from trajectory_harness.model import AnalysisCategory
from trajectory_harness.verify import (
    VerificationResult,
    VerifierKind,
    VerifierSpec,
)

ThresholdComparison = Literal["at_most", "at_least"]


@dataclass(frozen=True, slots=True)
class MeasurementThresholdVerifier:
    """Apply one caller-owned hard criterion to a named numeric measurement."""

    verifier_id: str
    title: str
    measurer_id: str
    measurement: str
    threshold: float
    comparison: ThresholdComparison = "at_most"
    category: AnalysisCategory = "cost"
    kind: VerifierKind = "domain"
    owner: str = ""

    def __post_init__(self) -> None:
        if not self.verifier_id:
            raise ValueError("measurement threshold verifier_id must not be empty")
        if not self.measurer_id or not self.measurement:
            raise ValueError("measurement threshold source must not be empty")
        if not math.isfinite(self.threshold):
            raise ValueError("measurement threshold must be finite")
        if self.comparison not in ("at_most", "at_least"):
            raise ValueError("measurement threshold comparison is unsupported")

    @property
    def spec(self) -> VerifierSpec:
        operator = "<=" if self.comparison == "at_most" else ">="
        return VerifierSpec(
            verifier_id=self.verifier_id,
            title=self.title,
            description=(
                f"Verify {self.measurer_id}.{self.measurement} {operator} "
                f"{self.threshold:g}."
            ),
            category=self.category,
            kind=self.kind,
            owner=self.owner,
            rule_type="hard",
        )

    def verify(
        self,
        trajectory: Trajectory,
        *,
        measurements: Measurements = (),
        reference: Trajectory | None = None,
    ) -> VerificationResult:
        del trajectory, reference
        candidates = tuple(
            result for result in measurements if result.measurer_id == self.measurer_id
        )
        if not candidates:
            return VerificationResult(
                verifier_id=self.verifier_id,
                status="not_applicable",
                explanation=f"Measurement source {self.measurer_id!r} is absent.",
            )
        if len(candidates) > 1:
            return VerificationResult(
                verifier_id=self.verifier_id,
                status="error",
                explanation=(
                    f"Measurement source {self.measurer_id!r} is ambiguous: "
                    f"found {len(candidates)} results."
                ),
            )

        result = candidates[0]
        if result.status == "error":
            return VerificationResult(
                verifier_id=self.verifier_id,
                status="error",
                explanation=(
                    f"Measurement source {self.measurer_id!r} failed: "
                    f"{result.explanation}"
                ),
            )
        if result.status != "measured" or self.measurement not in result.measurements:
            return VerificationResult(
                verifier_id=self.verifier_id,
                status="not_applicable",
                explanation=(
                    f"Measurement {self.measurer_id}.{self.measurement} is unavailable."
                ),
            )

        value = result.measurements[self.measurement]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return VerificationResult(
                verifier_id=self.verifier_id,
                status="error",
                explanation=(
                    f"Measurement {self.measurer_id}.{self.measurement} is not numeric."
                ),
            )

        passed = (
            value <= self.threshold
            if self.comparison == "at_most"
            else value >= self.threshold
        )
        operator = "<=" if self.comparison == "at_most" else ">="
        return VerificationResult(
            verifier_id=self.verifier_id,
            status="verified",
            verdict="pass" if passed else "fail",
            explanation=(
                f"Observed {self.measurer_id}.{self.measurement}={value:g}; "
                f"required {operator} {self.threshold:g}."
            ),
            step_ids=result.step_ids,
        )
