"""ViewTree —— 视图期惰性索引（doc 立场①）。

node 集本身是**平的**：所有 diagnose 算子吃 `iter_nodes`，只用成对父子关系。真正需要
递归整棵树的只有渲染缩进、火焰图路径、最近祖先——全是视图时刻。ViewTree 按 node 的
`parent_node_id` 边现搭 children 索引，**没有任何分析算子需要持有它**。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Generic, Protocol, TypeVar

from trace_harness.model.node import Node

if TYPE_CHECKING:
    from trace_harness.model.context import TraceContext

T_co = TypeVar("T_co", covariant=True)


class NodeTreeExtractor(Protocol, Generic[T_co]):
    """A deterministic pass that extracts one concern-specific IR from a complete node tree."""

    def extract(self, context: TraceContext) -> T_co: ...


def iter_nodes(nodes: list[Node]) -> Iterator[Node]:
    """平铺遍历——分析算子的默认入口（不关心结构，只关心 node 集）。"""
    yield from nodes


@dataclass
class ViewTree:
    """按父子边现搭的索引；roots 按开始时间排序，children 同。"""

    roots: list[Node]
    by_id: dict[str, Node] = field(default_factory=dict)
    by_span: dict[str, Node] = field(default_factory=dict)
    _children: dict[str, list[Node]] = field(default_factory=dict)

    def children(self, n: Node) -> list[Node]:
        return self._children.get(n.node_id, [])

    def walk(self) -> Iterator[tuple[Node, int]]:
        """前序遍历，带深度——渲染缩进用。"""

        def rec(n: Node, depth: int) -> Iterator[tuple[Node, int]]:
            yield n, depth
            for c in self.children(n):
                yield from rec(c, depth + 1)

        for r in self.roots:
            yield from rec(r, 0)


def build_view(nodes: list[Node]) -> ViewTree:
    """list[Node] + parent 边 → 视图期树索引。孤儿（parent 不在集中）按 root 处理。"""
    by_id = {n.node_id: n for n in nodes}
    by_span = {sid: n for n in nodes for sid in n.span_ids}
    children: dict[str, list[Node]] = {}
    roots: list[Node] = []
    for n in nodes:
        pid = n.parent_node_id
        if pid and pid in by_id:
            children.setdefault(pid, []).append(n)
        else:
            roots.append(n)
    for lst in children.values():
        lst.sort(key=lambda x: x.start_ms)
    roots.sort(key=lambda x: x.start_ms)
    return ViewTree(roots=roots, by_id=by_id, by_span=by_span, _children=children)
