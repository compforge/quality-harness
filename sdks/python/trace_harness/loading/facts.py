"""Business-owned dependency declarations; the harness owns their execution."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from trace_harness.model.context import TraceContext
from trace_harness.model.node import Node


@dataclass(frozen=True)
class EvidenceDependency:
    span_ids: tuple[str, ...]
    fields: tuple[str, ...] | None


@dataclass(frozen=True)
class FactDependency:
    node_id: str
    name: str


@dataclass(frozen=True)
class FactProducer:
    produces: tuple[str, ...]
    applies: Callable[[Node], bool]
    requires: Callable[[Node, TraceContext], tuple[EvidenceDependency | FactDependency, ...]]
    compute: Callable[[Node, TraceContext], dict]
    version: str = "1"
