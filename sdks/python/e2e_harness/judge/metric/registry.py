"""Generic metric module discovery.

Services point ``MetricRegistry`` at their own metrics package (e.g.
``myapp.eval.metrics``); each module under the package exposes a single
``metric: BaseMetric`` instance, and the registry returns the dict keyed by
``metric.NAME``.

This avoids hard-coding the metrics package path in e2e-harness — different
services can have their own metric implementations co-located with their code.
"""

from __future__ import annotations

import importlib
import pkgutil

from e2e_harness.judge.metric.base import BaseMetric


class MetricRegistry:
    """Discover ``BaseMetric`` instances under a metrics package.

    Usage::

        registry = MetricRegistry("eval_suite.metrics")
        registry.list()              # → ['correctness', 'refusal', 'citation', ...]
        registry.get("correctness")  # → BaseMetric instance

    Each metric module must expose ``metric = <SomeBaseMetricInstance>``;
    modules without that attribute are ignored (so helper / base / common
    modules under the metrics package don't pollute the registry).
    """

    def __init__(
        self,
        package: str,
        *,
        skip_names: tuple[str, ...] = ("__init__", "base"),
    ):
        self._package = package
        self._skip_names = set(skip_names)

    def discover(self) -> dict[str, BaseMetric]:
        """Scan the package and return ``{metric_name: BaseMetric instance}``."""
        registry: dict[str, BaseMetric] = {}
        pkg = importlib.import_module(self._package)
        for info in pkgutil.iter_modules(pkg.__path__):
            if info.name in self._skip_names:
                continue
            module = importlib.import_module(f"{self._package}.{info.name}")
            instance = getattr(module, "metric", None)
            if instance is None:
                continue
            if not isinstance(instance, BaseMetric):
                raise TypeError(
                    f"{self._package}.{info.name}.metric must be a BaseMetric instance, "
                    f"got {type(instance).__name__}"
                )
            registry[instance.NAME] = instance
        return registry

    def list(self) -> list[str]:
        return sorted(self.discover().keys())

    def get(self, name: str) -> BaseMetric:
        registry = self.discover()
        if name not in registry:
            raise KeyError(
                f"unknown metric: {name}; available: {', '.join(sorted(registry))}"
            )
        return registry[name]
