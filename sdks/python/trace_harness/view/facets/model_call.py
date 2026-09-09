"""ModelCallFacet —— model-call 默认折叠 http 子 node。

1:1 去融合后 http 自成子 node（有自己的 duration/status）；但 http 的状态已由 derive 卷成
model-call 的 `http_status`（显示在 brief 上），故默认 `Hide` http 子——不出"… +N 折叠"噪声行，
要拆延迟（看 http 那一跳自身耗时）再 expand。非 http 子（如残余 service 组）仍走默认 prune。
"""

from __future__ import annotations

from trace_harness.model.node import Node
from trace_harness.view.facet import (
    ChildOp,
    DefaultFacet,
    Hide,
    PerspectiveLevel,
    RenderCtx,
    TracePerspective,
)


class ModelCallFacet(DefaultFacet):
    priority = 20

    def match(self, node: Node) -> bool:
        return node.kind == "model-call"

    def perspective_level(
        self, node: Node, perspective: TracePerspective
    ) -> PerspectiveLevel | None:
        return "primary" if perspective == "agent" else None

    def layout(self, node: Node, children: list[Node], rctx: RenderCtx) -> list[ChildOp]:
        http = [c for c in children if c.kind == "http"]
        rest = [c for c in children if c.kind != "http"]
        return [Hide(c) for c in http] + super().layout(node, rest, rctx)
