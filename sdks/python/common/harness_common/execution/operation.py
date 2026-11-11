"""Stable service operations shared by protocol-oriented harnesses."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Generic, TypeVar
from urllib.parse import quote

from harness_common.identity import Identity

from harness_common.execution.outcome import Outcome
from harness_common.system.service import Service


@dataclass(frozen=True, slots=True)
class Operation(Identity):
    """A named capability exposed by a Service."""

    name: str
    service_name: str = field(kw_only=True)

    def id(self) -> str:
        """Service-qualified capability identity, independent of transport details."""
        if not self.service_name or not self.name:
            raise ValueError("service and operation names must not be empty")
        return (
            "operation:"
            + quote(self.service_name, safe="")
            + "/"
            + quote(self.name, safe="")
        )


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

    def __post_init__(self) -> None:
        if self.operation.service_name != self.service.name:
            raise ValueError("operation owner does not match the execution service")
