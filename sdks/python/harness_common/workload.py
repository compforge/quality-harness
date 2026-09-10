"""Declared runtime workload references, separate from live instance observations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True, slots=True)
class Workload:
    """A named runtime carrier of a Service; platform subclasses locate it precisely.

    A declaration does not claim that the workload or any instance currently exists.
    """

    name: str


@dataclass(frozen=True, slots=True)
class KubernetesWorkload(Workload):
    """A controller or explicit Pod in the Service's Kubernetes environment.

    namespace/kind/name locate the resource. container selects where to operate,
    not a different workload identity. Kubernetes Service is a network endpoint,
    not a workload. Pods and their UIDs are resolved at execution time.
    """

    namespace: str
    kind: Literal["Deployment", "StatefulSet", "DaemonSet", "Pod"] = "Deployment"
    container: str | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.namespace.strip():
            raise ValueError("Kubernetes workload requires name and namespace")
        if self.kind not in ("Deployment", "StatefulSet", "DaemonSet", "Pod"):
            raise ValueError(f"Unsupported Kubernetes workload kind: {self.kind}")
