"""A Component's runtime presence in one Environment."""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import quote

from harness_common.identity import Identity

from harness_common.system.component import Component
from harness_common.system.environment import Environment
from harness_common.system.workload import Workload


@dataclass(frozen=True, slots=True)
class Service(Identity):
    """One Component's logical runtime presence in an Environment.

    workloads declares zero or more platform carriers; it is neither a live Pod
    inventory nor proof of existence. The mapping may change without changing
    the logical Service identity. Consumers choose sampling/aggregation policy.
    Description, concepts and workflows are discovery metadata, not part of identity.
    Concepts describes domain objects, relationships and constraints; workflows
    describes their collaboration from trigger to completion. Both are optional
    Markdown text, not executable plans or observations of the current environment.
    """

    name: str
    component: Component
    environment: Environment

    description: str | None = field(default=None, kw_only=True, compare=False)
    concepts: str | None = field(default=None, kw_only=True, compare=False)
    workflows: str | None = field(default=None, kw_only=True, compare=False)
    workloads: tuple[Workload, ...] = field(default=(), kw_only=True, compare=False)

    def id(self) -> str:
        """Name-based identity within one environment's service catalog.

        @spec Names must be unique in the catalog; renaming changes this identifier.
        @rule Environment is supplied by the owning graph/run, not encoded in this local ID.
        """
        if not self.name:
            raise ValueError("service name must not be empty")
        return "service:" + quote(self.name, safe="")
