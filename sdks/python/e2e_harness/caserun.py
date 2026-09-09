"""One canonical case execution with explicit lifecycle and phase evidence.

``Case`` stays stable input/judgment data. ``CasePlan`` owns the operational
lifecycle needed by real e2e cases: prepare resources, execute the stimulus,
judge eventual behavior, and always clean up.  The Python and Go SDKs share
these phase/status semantics while keeping language-idiomatic APIs.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Generic, TypeVar

from harness_common import Execution, OperationRun
from harness_common.verdict import CaseVerdict, Status
from e2e_harness.matrix import Variant

S = TypeVar("S")


@dataclass(frozen=True)
class CaseRef:
    """Stable executable reference: canonical CaseSet name + case id."""

    caseset: str
    id: str


@dataclass(frozen=True)
class Budgets:
    prepare_s: float
    execute_s: float
    judge_s: float
    cleanup_s: float


@dataclass(frozen=True)
class PhaseContext:
    """Cooperative phase deadline for sync Python steps.

    Python cannot safely preempt an arbitrary synchronous callable. Steps that
    poll or call external systems should derive their own timeout from
    ``remaining_s``. CaseRun also rejects a step that returns after its budget.
    """

    name: str
    started_at: float
    deadline: float

    @property
    def remaining_s(self) -> float:
        return max(0.0, self.deadline - time.monotonic())

    def raise_if_expired(self) -> None:
        if self.remaining_s <= 0:
            raise TimeoutError(f"{self.name} phase exceeded its time budget")


Step = Callable[[PhaseContext, S], None]


@dataclass(frozen=True)
class CasePlan(Generic[S]):
    """Lifecycle steps used by the e2e engine to produce a CaseRun."""

    execute: Step[S]
    budgets: Budgets
    prepare: Step[S] | None = None
    judge: Step[S] | None = None
    cleanup: Step[S] | None = None
    facets: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PhaseResult:
    name: str
    status: Status
    duration_ms: int
    reason: str | None = None


@dataclass
class CaseRun(Execution):
    """One executed e2e Case and its lifecycle and Operation evidence."""

    ref: CaseRef
    variant: Variant
    status: Status
    phases: tuple[PhaseResult, ...]
    facets: dict[str, str]
    reason: str | None = None

    def case_verdict(self) -> CaseVerdict:
        metrics = {
            f"{phase.name}_duration_ms": {
                "value": phase.duration_ms,
                "unit": "ms",
            }
            for phase in self.phases
        }
        return CaseVerdict(
            case_id=self.ref.id,
            arm_id=self.variant.id,
            status=self.status,
            reason=self.reason,
            facets=self.facets,
            metrics=metrics,
        )


class Skip(Exception):
    """Mark the case inapplicable while still allowing cleanup to run."""


class Fail(Exception):
    """A judged behavior mismatch, distinct from an execution error."""


def run_lifecycle(
    ref: CaseRef,
    state: S,
    definition: CasePlan[S],
    *,
    variant: Variant | None = None,
    operation_runs: list[OperationRun] | None = None,
) -> CaseRun:
    """Run ``prepare → execute → judge → cleanup`` and retain phase evidence."""
    variant = variant or Variant()
    facets = {**definition.facets, **variant.values}
    if not ref.caseset or not ref.id:
        return CaseRun(
            id=ref.id,
            operation_runs=operation_runs or [],
            ref=ref,
            variant=variant,
            status="error",
            phases=(),
            facets=facets,
            reason="case ref requires caseset and id",
        )

    phases: list[PhaseResult] = []
    status: Status = "pass"
    reason: str | None = None
    blocked = False

    def apply(phase: PhaseResult) -> None:
        nonlocal status, reason, blocked
        if phase.status == "pass":
            return
        if phase.status == "error":
            status = "error"
        elif phase.status == "fail" and status != "error":
            status = "fail"
        elif phase.status == "skipped" and status == "pass":
            status = "skipped"
        if reason is None or phase.status == "error":
            reason = f"{phase.name}: {phase.reason or phase.status}"
        if phase.name != "cleanup":
            blocked = True

    if definition.prepare is not None:
        phase = _run_phase(
            "prepare", definition.budgets.prepare_s, state, definition.prepare
        )
        phases.append(phase)
        apply(phase)

    if not blocked:
        phase = _run_phase(
            "execute", definition.budgets.execute_s, state, definition.execute
        )
        phases.append(phase)
        apply(phase)

    if definition.judge is not None:
        if blocked:
            phases.append(
                PhaseResult("judge", "skipped", 0, "blocked by an earlier phase")
            )
        else:
            phase = _run_phase(
                "judge", definition.budgets.judge_s, state, definition.judge
            )
            phases.append(phase)
            apply(phase)

    if definition.cleanup is not None:
        # Cleanup receives a fresh deadline even when execute/judge exhausted
        # theirs; teardown must not inherit an already-expired budget.
        phase = _run_phase(
            "cleanup", definition.budgets.cleanup_s, state, definition.cleanup
        )
        phases.append(phase)
        apply(phase)

    return CaseRun(
        id=ref.id,
        operation_runs=operation_runs or [],
        ref=ref,
        variant=variant,
        status=status,
        phases=tuple(phases),
        facets=facets,
        reason=reason,
    )


def _run_phase(name: str, budget_s: float, state: S, step: Step[S]) -> PhaseResult:
    started = time.monotonic()
    if budget_s <= 0:
        return PhaseResult(name, "error", 0, "phase requires a positive time budget")
    ctx = PhaseContext(name=name, started_at=started, deadline=started + budget_s)
    try:
        step(ctx, state)
        ctx.raise_if_expired()
    except Skip as exc:
        status: Status = "skipped"
        reason = str(exc)
    except Fail as exc:
        status = "fail"
        reason = str(exc)
    except Exception as exc:  # noqa: BLE001 - phase failures become structured evidence
        status = "error"
        reason = f"{type(exc).__name__}: {exc}"
    else:
        status = "pass"
        reason = None
    return PhaseResult(
        name=name,
        status=status,
        duration_ms=int((time.monotonic() - started) * 1000),
        reason=reason,
    )
