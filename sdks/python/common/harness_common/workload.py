"""Declared runtime workload references, separate from live instance observations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, TypedDict


class _ResourceLocation(TypedDict):
    kind: Literal["resource"]
    resource_kind: Literal["Deployment", "StatefulSet", "DaemonSet", "Pod"]
    name: str


class _ServiceLocation(TypedDict):
    kind: Literal["service"]
    name: str


class _LabelsLocation(TypedDict):
    kind: Literal["labels"]
    labels: dict[str, str]


@dataclass(frozen=True, slots=True)
class Workload:
    """A named runtime carrier of a Service; platform subclasses locate it precisely.

    A declaration does not claim that the workload or any instance currently exists.
    """

    name: str


@dataclass(frozen=True, slots=True)
class KubernetesWorkload(Workload):
    """A logical workload located by resource, network Service or Pod labels.

    name need not match the platform resource name. An omitted namespace requires
    a resolver default and never means all namespaces. container is an operation
    default, not workload identity. Location contains no credentials or client.
    """

    location: _ResourceLocation | _ServiceLocation | _LabelsLocation
    namespace: str | None = None
    container: str | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if not self.name.strip() or (
            self.namespace is not None and not self.namespace.strip()
        ):
            raise ValueError("Workload name and explicit namespace must be nonempty")
        match self.location:
            case {"kind": "resource", "resource_kind": kind, "name": name}:
                if kind not in ("Deployment", "StatefulSet", "DaemonSet", "Pod"):
                    raise ValueError(f"Unsupported workload resource kind: {kind}")
                if not name.strip():
                    raise ValueError("Resource name must be nonempty")
            case {"kind": "service", "name": name} if name.strip():
                pass
            case {"kind": "labels", "labels": labels} if labels:
                pass
            case _:
                raise ValueError(
                    "Workload requires a resource, Service or nonempty labels"
                )


@dataclass(frozen=True, slots=True)
class WorkloadInstance:
    """Observed target bound to a caller-owned, unambiguous environment key.

    environment distinguishes clusters/contexts, not merely display labels.
    workload is the declaration name scoped by its owning Service; it is
    provenance, not physical identity. No access secrets belong here.
    """

    environment: str
    workload: str = field(compare=False)


@dataclass(frozen=True, slots=True)
class KubernetesWorkloadInstance(WorkloadInstance):
    """Observed Pod incarnation, not a promise that it is still alive.

    @spec Environment and UID separate clusters and same-name replacements.
    @rule Container selection is not Pod identity; access/cache keys must
    independently include the selected container.
    """

    namespace: str
    pod: str
    uid: str
    container: str | None = field(default=None, compare=False)
