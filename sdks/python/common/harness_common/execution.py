"""Harness-neutral grouping of related OperationRuns."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Generic, TypeVar

from harness_common.operation import OperationRun
from harness_common.outcome import Outcome


OutcomeT = TypeVar("OutcomeT", bound=Outcome)


@dataclass(kw_only=True)
class Execution(Generic[OutcomeT]):
    """One domain-defined unit of work within an ExperimentRun.

    E2E and perf intentionally organize Operations differently.  The shared
    model records only the stable result: an identity and the OperationRuns
    grouped by that unit of work.
    """

    id: str
    operation_runs: list[OperationRun[OutcomeT]] = field(default_factory=list)
