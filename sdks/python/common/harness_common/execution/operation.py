"""Stable service operations shared by protocol-oriented harnesses."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Generic, TypeVar
from urllib.parse import quote

from harness_common.identity import Identity
from harness_common.observe import ObservationDefinition

from harness_common.execution.outcome import Outcome
from harness_common.system.service import Service


@dataclass(frozen=True, slots=True)
class Operation(Identity):
    """A named capability exposed by a Service."""

    name: str
    service_name: str = field(kw_only=True)
    description: str | None = field(default=None, kw_only=True, compare=False)
    importance: str | None = field(default=None, kw_only=True, compare=False)
    calls: tuple[str, ...] | None = field(default=None, kw_only=True, compare=False)
    may_calls: tuple[str, ...] | None = field(default=None, kw_only=True, compare=False)
    observes: tuple[ObservationDefinition, ...] | None = field(
        default=None, kw_only=True, compare=False
    )

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
    """An HTTP capability; path is its route template, e.g. /users/{id}.

    @rule Metrics match service + method + route; concrete request URLs are execution data.
    """

    method: str
    path: str
    response_mode: str = field(default="unary", init=False)


@dataclass(frozen=True, slots=True)
class SSEOperation(HttpOperation):
    """An HTTP operation returning Server-Sent Events.

    @spec Response mode does not change Operation identity or imply business success.
    @rule First event, first useful output and full stream duration have distinct boundaries.
    """

    response_mode: str = field(default="sse", init=False)


def operation_from(raw: dict) -> Operation:
    """Construct Common identity once; HTTP path is metadata, never the join key."""
    fields = dict(raw)
    mode = fields.pop("response_mode", "unary")
    if mode not in {"unary", "sse"}:
        raise ValueError("operation response_mode must be unary or sse")
    if "path" in fields or "method" in fields:
        cls = SSEOperation if mode == "sse" else HttpOperation
    else:
        if mode == "sse":
            raise ValueError("SSE operation requires method and path")
        cls = Operation
    try:
        if "observes" in fields:
            if not isinstance(fields["observes"], list):
                raise ValueError("Operation observes must be a list")
            fields["observes"] = tuple(
                ObservationDefinition.from_dict(item) for item in fields["observes"]
            )
        operation = cls(**fields)
        operation.id()
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            f"Invalid Common Operation {fields.get('name')!r}: {exc}"
        ) from exc
    return operation


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
