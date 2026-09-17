"""Deployment environment identity, optional access host and observed evidence."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from harness_common.host import Host


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
