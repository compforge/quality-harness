"""Detect trajectories dominated by short model-output decisions."""

from __future__ import annotations

from dataclasses import dataclass

from trajectory_harness.detect import DetectionResult, DetectorSpec, Finding
from trajectory_harness.measure import Measurements
from trajectory_harness.measurers._tokens import (
    MODEL_OPERATIONS,
    OUTPUT_TOKEN_ATTRIBUTES,
    token_count,
)
from atif import Trajectory

from trajectory_harness.model import step_id, step_operation

MIN_COVERED_CALLS = 3
SHORT_OUTPUT_TOKENS = 500
MIN_SHORT_OUTPUT_RATIO = 0.8


@dataclass(frozen=True, slots=True)
class ShortDecisionChurnDetector:
    spec: DetectorSpec = DetectorSpec(
        detector_id="short_decision_churn",
        title="Short decision churn",
        description=(
            "Detect at least three covered model calls where 80% or more report "
            "fewer than 500 output tokens."
        ),
        category="cost",
        kind="common",
        owner="trajectory_harness",
    )

    def detect(
        self, trajectory: Trajectory, *, measurements: Measurements = ()
    ) -> DetectionResult:
        del measurements
        covered = tuple(
            (step, count)
            for step in trajectory.steps
            if step_operation(step) in MODEL_OPERATIONS
            if (count := token_count(step, OUTPUT_TOKEN_ATTRIBUTES)) is not None
        )
        if len(covered) < MIN_COVERED_CALLS:
            return DetectionResult(
                detector_id=self.spec.detector_id,
                status="not_applicable",
                explanation=(
                    f"Only {len(covered)} model calls report output tokens; "
                    f"{MIN_COVERED_CALLS} are required."
                ),
            )

        short = tuple(step for step, count in covered if count < SHORT_OUTPUT_TOKENS)
        ratio = len(short) / len(covered)
        if ratio < MIN_SHORT_OUTPUT_RATIO:
            return DetectionResult(
                detector_id=self.spec.detector_id,
                status="analyzed",
                explanation=f"Short-output calls account for {ratio:.1%} of covered calls.",
            )

        summary = (
            f"{len(short)} of {len(covered)} covered model calls report fewer than "
            f"{SHORT_OUTPUT_TOKENS} output tokens."
        )
        return DetectionResult(
            detector_id=self.spec.detector_id,
            status="analyzed",
            explanation=summary,
            findings=(
                Finding(
                    code="short_decision_churn",
                    severity="warning",
                    summary=summary,
                    step_ids=tuple(step_id(step) for step in short),
                    hypotheses=(
                        "Several decisions may be expressible as one model call.",
                        "A deterministic transform may replace some model calls.",
                        "Short calls may still represent necessary checkpoints.",
                    ),
                ),
            ),
        )
