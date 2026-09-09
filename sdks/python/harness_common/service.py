"""A Component's runtime presence in one Environment."""

from __future__ import annotations

from dataclasses import dataclass

from harness_common.component import Component
from harness_common.environment import Environment


@dataclass(frozen=True, slots=True)
class Service:
    """One Component manifested as a running workload in an Environment.

    Components without a runtime workload do not have a corresponding Service.
    """

    name: str
    component: Component
    environment: Environment
