"""Deployment environment identity, optional access host and observed evidence."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from harness_common.host import Host, parse_host


@dataclass(frozen=True, slots=True)
class Environment:
    """A named environment in which Components have running Service instances.

    Host is optional. When omitted, operations use the current execution host.
    It does not identify a Kubernetes node or dictate where the case runner runs.
    """

    name: str
    host: Host | None = field(default=None, kw_only=True)
    kind: ClassVar[str] = "generic"


@dataclass(frozen=True, slots=True)
class HostEnvironment(Environment):
    """A target running directly on the environment's host."""

    kind: ClassVar[str] = "host"


@dataclass(frozen=True, slots=True)
class KubernetesEnvironment(Environment):
    """A cluster accessed from Host; kubeconfig is a path on that host."""

    kubeconfig: str = ""
    context: str | None = None
    kind: ClassVar[str] = "kubernetes"


@dataclass
class EnvironmentFacts:
    """Observed facts with provenance. Missing values mean unknown, not false."""

    source: str = ""
    observed_at: str = ""
    values: dict[str, str] = field(default_factory=dict)


@dataclass
class EnvironmentSnapshot:
    """Run evidence; access configuration and credentials are deliberately absent.

    Runner and target facts are separate: a local runner can test a remote Pod.
    Profiles select project-owned conditions and never substitute for observation.
    """

    name: str
    kind: str
    profile: str = "default"
    revision: str = ""
    host_name: str = ""
    host: EnvironmentFacts | None = None
    runner: EnvironmentFacts = field(default_factory=EnvironmentFacts)
    target: EnvironmentFacts = field(default_factory=EnvironmentFacts)


def parse_environment(data: dict) -> Environment:
    """Parse the shared environment wire format, including legacy Kubernetes input."""
    if not isinstance(data, dict):
        raise ValueError("environment must be a mapping")
    unknown = set(data) - {"name", "kind", "host", "kubeconfig", "context"}
    if unknown:
        raise ValueError(
            f"environment contains unknown field(s): {', '.join(sorted(unknown))}"
        )
    if any(
        value is not None and not isinstance(value, str)
        for key, value in data.items()
        if key != "host"
    ):
        raise ValueError("environment fields except host must be strings")
    kind = data.get("kind") or (
        "kubernetes" if data.get("kubeconfig") or data.get("context") else "generic"
    )
    if kind not in {"generic", "host", "kubernetes"}:
        raise ValueError(f"unknown environment kind {kind!r}")
    host = parse_host(data["host"]) if data.get("host") is not None else None
    name = data.get("name") or ""
    if kind == "kubernetes":
        return KubernetesEnvironment(
            name, data.get("kubeconfig") or "", data.get("context"), host=host
        )
    if data.get("kubeconfig") or data.get("context"):
        raise ValueError(f"{kind} environment contains Kubernetes access fields")
    if kind == "host":
        return HostEnvironment(name, host=host)
    return Environment(name, host=host)
