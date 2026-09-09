"""Verifier contract and single-trajectory orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

from atif import Trajectory

from trajectory_harness.model import AnalysisCategory, RuleType
from trajectory_harness.measure import Measurements

VerifierKind = Literal["common", "domain"]
VerificationStatus = Literal["verified", "not_applicable", "error"]
VerificationVerdict = Literal["pass", "fail", "warning"]


@dataclass(frozen=True, slots=True)
class VerifierSpec:
    """Stable catalog entry for one common or domain verification rule."""

    verifier_id: str
    title: str
    description: str
    category: AnalysisCategory
    kind: VerifierKind = "domain"
    owner: str = ""
    rule_type: RuleType = "hard"

    def __post_init__(self) -> None:
        if self.category not in ("cost", "effect"):
            raise ValueError("verifier category must be 'cost' or 'effect'")
        if self.rule_type not in ("hard", "soft"):
            raise ValueError("verifier rule_type must be 'hard' or 'soft'")

    def to_dict(self) -> dict[str, Any]:
        return {
            "verifier_id": self.verifier_id,
            "title": self.title,
            "description": self.description,
            "category": self.category,
            "kind": self.kind,
            "owner": self.owner,
            "rule_type": self.rule_type,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> VerifierSpec:
        return cls(
            verifier_id=str(value["verifier_id"]),
            title=str(value["title"]),
            description=str(value.get("description") or ""),
            category=value["category"],
            kind=value.get("kind", "domain"),
            owner=str(value.get("owner") or ""),
            rule_type=value.get("rule_type", "hard"),
        )


@dataclass(frozen=True, slots=True)
class VerificationResult:
    """One verifier's conclusion and supporting trajectory evidence."""

    verifier_id: str
    status: VerificationStatus
    verdict: VerificationVerdict | None = None
    score: float | None = None
    explanation: str = ""
    step_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status == "verified" and self.verdict is None and self.score is None:
            raise ValueError(
                "a verified result must provide a verdict, a score, or both"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "verifier_id": self.verifier_id,
            "status": self.status,
            "verdict": self.verdict,
            "score": self.score,
            "explanation": self.explanation,
            "step_ids": list(self.step_ids),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> VerificationResult:
        return cls(
            verifier_id=str(value["verifier_id"]),
            status=value["status"],
            verdict=value.get("verdict"),
            score=(float(value["score"]) if value.get("score") is not None else None),
            explanation=str(value.get("explanation") or ""),
            step_ids=tuple(str(item) for item in value.get("step_ids", ())),
        )


@runtime_checkable
class Verifier(Protocol):
    """Verify a trajectory and its measurements against an explicit criterion."""

    spec: VerifierSpec

    def verify(
        self,
        trajectory: Trajectory,
        *,
        measurements: Measurements = (),
        reference: Trajectory | None = None,
    ) -> VerificationResult: ...


@dataclass(frozen=True, slots=True)
class TrajectoryVerification:
    """A trajectory paired with all verifier results produced for it."""

    trajectory: Trajectory
    results: tuple[VerificationResult, ...]
    target: str = ""
    category: AnalysisCategory = "effect"

    def __post_init__(self) -> None:
        if self.category not in ("cost", "effect"):
            raise ValueError("verification category must be 'cost' or 'effect'")

    def to_dict(self) -> dict[str, Any]:
        return {
            "trajectory_id": self.trajectory.trajectory_id,
            "target": self.target,
            "category": self.category,
            "results": [result.to_dict() for result in self.results],
        }

    @classmethod
    def from_dict(
        cls, value: dict[str, Any], *, trajectory: Trajectory
    ) -> TrajectoryVerification:
        return cls(
            trajectory=trajectory,
            target=str(value.get("target") or ""),
            category=value.get("category", "effect"),
            results=tuple(
                VerificationResult.from_dict(item) for item in value.get("results", ())
            ),
        )


def verify(
    trajectory: Trajectory,
    verifiers: list[Verifier] | tuple[Verifier, ...],
    *,
    measurements: Measurements = (),
    reference: Trajectory | None = None,
    target: str = "",
    category: AnalysisCategory = "effect",
) -> TrajectoryVerification:
    """Run verifiers; verifier failures remain health data, never a zero score."""

    results = []
    for verifier in verifiers:
        try:
            results.append(
                verifier.verify(
                    trajectory,
                    measurements=measurements,
                    reference=reference,
                )
            )
        except Exception as error:  # verifier plugins are an isolation boundary
            results.append(
                VerificationResult(
                    verifier_id=verifier.spec.verifier_id,
                    status="error",
                    explanation=f"{type(error).__name__}: {error}",
                )
            )
    return TrajectoryVerification(
        trajectory=trajectory,
        results=tuple(results),
        target=target,
        category=category,
    )
