"""Metric registry: name → instance. Resolves an experiment's ``metrics`` list.

Builtins are registered here; an LLM-judge or RAGAS-backed metric registers the
same way (expose an instance). ``resolve`` maps names → instances and is where
``metrics: [...]`` in the experiment yaml becomes live metric objects.
"""

from __future__ import annotations

from eval_harness.metric.base import BaseMetric
from eval_harness.metric.builtins import (
    ExactMatch,
    KeywordRefusal,
    Latency,
    Tokens,
    Ttft,
)

_BUILTINS: list[BaseMetric] = [
    ExactMatch(),
    KeywordRefusal(),
    Latency(),
    Ttft(),
    Tokens(),
]

REGISTRY: dict[str, BaseMetric] = {m.NAME: m for m in _BUILTINS}


def register(metric: BaseMetric) -> None:
    REGISTRY[metric.NAME] = metric


def resolve(names: list[str]) -> list[BaseMetric]:
    out: list[BaseMetric] = []
    for n in names:
        if n not in REGISTRY:
            raise KeyError(f"unknown metric {n!r}; available: {sorted(REGISTRY)}")
        out.append(REGISTRY[n])
    return out
