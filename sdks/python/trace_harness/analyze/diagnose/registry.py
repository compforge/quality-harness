"""Scoped detector 注册表：不绑 kind、对每个 node 跑一遍（自 gate）。

与 per-kind `KindSpec.rules` 互补：rules 绑 kind、本表跨 kind（含整树/trace 级结论）。
签名 `(node, ctx, found) -> list[Finding]`：

- node 自带子树（`ctx.view().children`）→ trace 级 detector = 在 root/锚 node 上 gate、再顺子树扫；
  命不中的 node 第一行就 `return []`，避免 N× 重扫。
- `found` = 至此已产的 findings（dict[node_id, [Finding]]）；引擎**后序**跑（子先于父），
  故 detector 到某 node 时其子树 findings 已就绪，可挑进 `Finding.causes` 归因。多数忽略 found。

Domain 通过 ``TraceContributions.detectors`` 显式组合。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from trace_harness.model.context import TraceContext
from trace_harness.model.node import Finding, Node

Detector = Callable[[Node, TraceContext, "dict[str, list[Finding]]"], list[Finding]]


class DetectorRegistry:
    """一次 trace 分析使用的全局/整树 detector 集。"""

    def __init__(self, detectors: Iterable[Detector] = ()) -> None:
        self._detectors = list(detectors)

    def register(self, detector: Detector) -> Detector:
        self._detectors.append(detector)
        return detector

    def registered(self) -> list[Detector]:
        return list(self._detectors)
