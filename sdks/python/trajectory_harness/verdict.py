"""Project trajectory run health and optional domain gates into verdict.json."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, Sequence, runtime_checkable

from harness_common.verdict import (
    PRECEDENCE,
    CheckVerdict,
    RunVerdict,
    build_run_verdict,
    rollup_status,
    write_verdict,
)
from trajectory_harness.runio import TrajectoryRunArtifact


@runtime_checkable
class TrajectoryVerdictPolicy(Protocol):
    """Domain-owned aggregate gates; findings alone never become verdict checks."""

    def verify(self, artifact: TrajectoryRunArtifact) -> Sequence[CheckVerdict]: ...


def build_trajectory_verdict(
    scope: str,
    artifact: TrajectoryRunArtifact,
    *,
    policy: TrajectoryVerdictPolicy | None = None,
) -> RunVerdict:
    health_errors = _health_errors(artifact)
    if health_errors:
        status = "error"
        reason = health_errors[0]
        checks: list[CheckVerdict] = []
    elif not artifact.dataset.trajectories:
        status = "skipped"
        reason = "dataset contains no trajectories"
        checks = []
    elif policy is None:
        status = "skipped"
        reason = "no trajectory verdict policy declared (analysis artifacts only)"
        checks = []
    else:
        try:
            checks = list(policy.verify(artifact))
            invalid = next(
                (item for item in checks if item.status not in PRECEDENCE), None
            )
            if invalid is not None:
                raise ValueError(
                    f"check {invalid.name!r} has invalid status {invalid.status!r}"
                )
            status = rollup_status([item.status for item in checks])
            reason = _check_reason(checks, status)
        except Exception as error:  # domain policy is a plugin boundary
            status = "error"
            reason = f"verdict policy failed: {type(error).__name__}: {error}"
            checks = []

    return build_run_verdict(
        "trajectory",
        scope,
        artifact.run.run_id,
        [],
        checks=checks,
        status=status,
        reason=reason,
        artifact_paths={
            "dataset": "dataset.json",
            "run": "run.json",
            "report": "report.html",
        },
        created_at=artifact.run.created_at.isoformat(),
    )


def write_trajectory_verdict(
    run_dir: str | Path,
    scope: str,
    artifact: TrajectoryRunArtifact,
    *,
    policy: TrajectoryVerdictPolicy | None = None,
) -> Path:
    return write_verdict(
        run_dir, build_trajectory_verdict(scope, artifact, policy=policy)
    )


def _health_errors(artifact: TrajectoryRunArtifact) -> list[str]:
    errors = [
        f"dataset build issue for {item.recording_id}: {item.error}"
        for item in artifact.build.summary.issues
    ]
    for item in artifact.run.detections:
        errors.extend(
            f"detector {result.detector_id} failed: {result.explanation}"
            for result in item.results
            if result.status == "error"
        )
    for item in artifact.run.verifications:
        errors.extend(
            f"verifier {result.verifier_id} failed: {result.explanation}"
            for result in item.results
            if result.status == "error"
        )
    for item in artifact.run.measurements:
        errors.extend(
            f"measurer {result.measurer_id} failed: {result.explanation}"
            for result in item.results
            if result.status == "error"
        )
    return errors


def _check_reason(checks: Sequence[CheckVerdict], status: str) -> str | None:
    if not checks:
        return "policy declared but produced no checks"
    if status == "pass":
        return None
    matching = [item for item in checks if item.status == status]
    first = matching[0]
    detail = f": {first.reason}" if first.reason else ""
    return f"{len(matching)} policy check(s) {status}; first — {first.name}{detail}"
