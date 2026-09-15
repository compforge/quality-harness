"""Bind reusable cases to named environments using the existing CaseRun lifecycle."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Generic, TypeVar

from harness_common.environment import EnvironmentSnapshot
from e2e_harness.caserun import CasePlan, CaseRef, CaseRun, PhaseContext, run_lifecycle
from e2e_harness.matrix import Variant

S = TypeVar("S")


@dataclass
class EnvironmentState(Generic[S]):
    environment: EnvironmentSnapshot
    value: S


def run_in_environment(
    ref: CaseRef,
    state: EnvironmentState[S],
    definition: CasePlan[EnvironmentState[S]],
    *,
    required: dict[str, str] | None = None,
    variant: Variant | None = None,
) -> CaseRun:
    """Verify prepared target conditions, run the case and retain realized facts.

    Project callbacks own startup, observation and teardown. Requirements are
    checked independently of the product assertions; an absent fact is an error.
    """
    values = dict(variant.values) if variant else {}
    identity = (
        state.environment.name,
        state.environment.kind,
        state.environment.profile,
    )
    conflict = False
    for key, value in {
        "environment": state.environment.name,
        "profile": state.environment.profile,
    }.items():
        conflict |= key in values and values[key] != value
        values[key] = value

    def prepare(ctx: PhaseContext, current: EnvironmentState[S]) -> None:
        env = current.environment
        if conflict:
            raise ValueError("variant conflicts with selected environment")
        if not env.name or not env.kind or not env.profile:
            raise ValueError("environment name, kind and profile are required")
        if definition.prepare:
            definition.prepare(ctx, current)
        env = current.environment
        if (env.name, env.kind, env.profile) != identity:
            raise ValueError("prepare changed environment identity")
        if not env.target.source or not env.target.observed_at:
            raise ValueError("target environment facts require source and observed_at")
        for key, expected in sorted((required or {}).items()):
            if key not in env.target.values or env.target.values[key] != expected:
                raise ValueError(
                    f"environment condition {key}: observed {env.target.values.get(key)!r}, required {expected!r}"
                )

    prepared = deepcopy(state.environment)
    cleaned = None

    def capture_prepare(ctx: PhaseContext, current: EnvironmentState[S]) -> None:
        nonlocal prepared
        try:
            prepare(ctx, current)
        finally:
            # Preserve the conditions validated before stimulus, including partial failure.
            prepared = deepcopy(current.environment)

    def cleanup(ctx: PhaseContext, current: EnvironmentState[S]) -> None:
        nonlocal cleaned
        try:
            if definition.cleanup:
                definition.cleanup(ctx, current)
        finally:
            cleaned = deepcopy(current.environment)

    run = run_lifecycle(
        ref,
        state,
        replace(
            definition,
            prepare=capture_prepare,
            cleanup=cleanup if definition.cleanup else None,
        ),
        variant=Variant(values),
    )
    run.environment = prepared
    run.cleanup_environment = cleaned
    return run
