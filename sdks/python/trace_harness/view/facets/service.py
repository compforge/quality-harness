"""ServiceFacet —— 残余 service 组（kind == "service"）的渲染。

P1 继承 DefaultFacet（行为/输出与默认完全一致），只用来证明"按 kind 分派"这条缝通了；
后续这里承载 service 组的专属口径（如亚秒组聚合成一行）时，再覆盖 brief / layout。
"""

from __future__ import annotations

from trace_harness.model.node import Node
from trace_harness.view.facet import DefaultFacet


class ServiceFacet(DefaultFacet):
    priority = 10

    def match(self, node: Node) -> bool:
        return node.kind == "service"
