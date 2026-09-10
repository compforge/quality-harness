"""Stable Kubernetes observations exposed to harness consumers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class Options:
    """Namespace and explicit Kubernetes API resource limits."""

    namespace: str
    request_timeout_s: float
    connection_pool_maxsize: int
    exec_timeout_s: float = 60
    exec_concurrency: int = 4
    max_exec_bytes: int = 64 * 1024 * 1024


@dataclass(frozen=True)
class PodRef:
    """Identity of one physical Pod instance."""

    name: str
    uid: str


@dataclass(frozen=True)
class Pod:
    """Stable Pod state without exposing generated Kubernetes models."""

    name: str
    uid: str
    labels: dict[str, str]
    phase: str
    ready: bool
    deleting: bool
    unschedulable: bool
    reason: str
    message: str
    containers: tuple[Container, ...] = ()

    def ref(self) -> PodRef:
        return PodRef(name=self.name, uid=self.uid)


@dataclass(frozen=True)
class Event:
    """Evidence-bearing subset of a Kubernetes Event."""

    type: str
    reason: str
    message: str
    count: int
    observed_at: datetime


@dataclass(frozen=True)
class PodSpec:
    """A caller-owned disposable workload; no service-specific defaults."""

    name: str
    image: str
    container: str = "main"
    command: tuple[str, ...] = ()
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)
    requests: dict[str, str] = field(default_factory=dict)
    limits: dict[str, str] = field(default_factory=dict)
    restart_policy: str = "Never"


@dataclass(frozen=True)
class Container:
    name: str
    restart_count: int
