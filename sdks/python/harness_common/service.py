"""A Component's runtime presence in one Environment."""

from __future__ import annotations

from dataclasses import dataclass, field

from harness_common.component import Component
from harness_common.environment import Environment
from harness_common.workload import Workload


@dataclass(frozen=True, slots=True)
class Service:
    """One Component's logical runtime presence in an Environment.

    workloads declares zero or more platform carriers; it is neither a live Pod
    inventory nor proof of existence. The mapping may change without changing
    the logical Service identity. Consumers choose sampling/aggregation policy.
    """

    name: str
    component: Component
    environment: Environment

    workloads: tuple[Workload, ...] = field(default=(), kw_only=True, compare=False)
