"""Stable service operations shared by protocol-oriented harnesses."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

from harness_common.outcome import Outcome
from harness_common.service import Service


@dataclass(frozen=True, slots=True)
class Operation:
    """A named capability exposed by a Service."""

    name: str


@dataclass(frozen=True, slots=True)
class HttpOperation(Operation):
    """An Operation exposed through an HTTP method and path."""

    method: str
    path: str


OutcomeT = TypeVar("OutcomeT", bound=Outcome)


@dataclass(frozen=True, slots=True)
class OperationRun(Generic[OutcomeT]):
    """One execution of an Operation against a Service and its raw Outcome."""

    id: str
    service: Service
    operation: Operation
    outcome: OutcomeT
