"""FacetRegistry —— 按 priority 分派 facet。

dispatch 取最高 priority 的 match；都不中 → DefaultFacet 兜底。消费方通过
``TraceContributions.facets`` 显式组合，无模块级注册表。
"""

from __future__ import annotations

from collections.abc import Iterable

from trace_harness.model.node import Node
from trace_harness.view.facet import (
    DefaultFacet,
    Facet,
    PerspectiveLevel,
    TracePerspective,
)


class FacetRegistry:
    def __init__(self, facets: Iterable[Facet] = ()) -> None:
        self._facets: list[Facet] = []
        self._default = DefaultFacet()
        for facet in facets:
            self.register(facet)

    def register(self, facet: Facet) -> None:
        self._facets.append(facet)
        # 稳定排序：priority 降序，同分保留注册先后（list.sort 稳定）
        self._facets.sort(key=lambda f: -f.priority)

    def dispatch(self, node: Node) -> Facet:
        for f in self._facets:
            if f.match(node):
                return f
        return self._default

    def perspective_level(self, node: Node, perspective: TracePerspective) -> PerspectiveLevel:
        if perspective == "full":
            return "primary"
        for facet in self._facets:
            if not facet.match(node):
                continue
            level = facet.perspective_level(node, perspective)
            if level is not None:
                return level
        return self._default.perspective_level(node, perspective) or "detail"
