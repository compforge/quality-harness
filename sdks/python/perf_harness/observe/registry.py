"""Registry for consumer-defined probes.

Consumer projects import an extension module from the experiment config. The
module registers probe factories here, keeping service-specific observation out
of the harness while preserving declarative ``observe:`` configuration.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from perf_harness.model import Service
from perf_harness.observe.base import Probe


@dataclass(frozen=True)
class ProbeConfig:
    """Normalized context passed to a registered probe factory."""

    service: Service
    per_pod: bool = False
    options: Mapping[str, object] = field(default_factory=dict)


ProbeFactory = Callable[[ProbeConfig], Probe]
_REGISTRY: dict[str, ProbeFactory] = {}


def register_probe(name: str, factory: ProbeFactory) -> None:
    """Register a consumer probe factory under the config-visible ``name``."""
    _REGISTRY[name] = factory


def build_probe(name: str, config: ProbeConfig) -> Probe:
    """Resolve a registered consumer probe."""
    if name in _REGISTRY:
        return _REGISTRY[name](config)
    raise ValueError(
        f"unknown probe {name!r}; register it in an extension module via "
        f"perf_harness.register_probe({name!r}, ...)"
    )
