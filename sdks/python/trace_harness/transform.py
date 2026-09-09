"""Fact → fact transformations, materialized only when a consumer requests their outputs.

The trace owns one dependency context. Each materialization commits atomically;
failed dependencies do not leave partial facts or poison subsequent requests.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from trace_harness.model.node import Node
from trace_harness.model.viewtree import ViewTree


@dataclass(frozen=True)
class FactTransform:
    produces: tuple[str, ...]
    applies: Callable[[Node], bool]
    compute: Callable[[Node, TransformContext], dict]


class TransformContext:
    def __init__(self, view: ViewTree, transforms: Iterable[FactTransform]):
        self._view = view
        self._owners: dict[tuple[str, str], FactTransform] = {}
        self._pending: dict[tuple[str, str], Any] = {}
        self._active: set[tuple[str, int]] = set()
        self._completed: set[tuple[str, int]] = set()
        self._done: set[tuple[str, int]] = set()
        transforms = tuple(transforms)
        for node in view.by_id.values():
            for transform in transforms:
                if not transform.applies(node):
                    continue
                for name in transform.produces:
                    key = (node.node_id, name)
                    if name in node.facts or key in self._owners:
                        raise ValueError(f"conflicting fact producer: {key}")
                    self._owners[key] = transform

    def children(self, node: Node) -> list[Node]:
        return self._view.children(node)

    def get(self, node: Node, name: str) -> Any:
        """Resolve a fact dependency within a materialization; raw spans are not an input."""
        key = (node.node_id, name)
        if name in node.facts:
            return node.facts[name]
        if key in self._pending:
            return self._pending[key]
        transform = self._owners.get(key)
        if transform is None:
            return None
        invocation = (node.node_id, id(transform))
        if invocation in self._active:
            raise ValueError(f"cyclic fact dependency: {key}")
        if invocation not in self._done and invocation not in self._completed:
            self._active.add(invocation)
            try:
                values = transform.compute(node, self)
                if set(values) - set(transform.produces):
                    raise ValueError(f"undeclared fact output: {key}")
                for output, value in values.items():
                    self._pending[(node.node_id, output)] = value
                self._completed.add(invocation)
            finally:
                self._active.remove(invocation)
        return self._pending.get(key)

    def materialize(self, requests: Iterable[tuple[Node, str]]) -> None:
        """Compute selected outputs and their dependencies; all outputs become node facts."""
        try:
            for node, name in requests:
                if self._view.by_id.get(node.node_id) is not node:
                    raise ValueError(f"node does not belong to this trace: {node.node_id}")
                self.get(node, name)
            for node_id, name in self._pending:
                if name in self._view.by_id[node_id].facts:
                    raise ValueError(f"conflicting fact write: {(node_id, name)}")
            for (node_id, name), value in self._pending.items():
                self._view.by_id[node_id].facts[name] = value
            self._done.update(self._completed)
        finally:
            self._pending.clear()
            self._completed.clear()


def _http_status(node: Node, ctx: TransformContext) -> dict:
    statuses = [ctx.get(child, "status") for child in ctx.children(node) if child.kind == "http"]
    statuses = [int(status) for status in statuses if status is not None]
    return {"http_status": next((s for s in statuses if s != 200), statuses[0])} if statuses else {}


BUILTIN_TRANSFORMS = (
    FactTransform(("http_status",), lambda n: n.kind == "model-call", _http_status),
)
