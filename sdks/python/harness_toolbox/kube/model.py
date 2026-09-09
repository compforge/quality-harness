"""Stable Kubernetes observations exposed to harness consumers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Options:
    """Namespace and explicit Kubernetes API resource limits."""

    namespace: str
    request_timeout_s: float
    connection_pool_maxsize: int


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
