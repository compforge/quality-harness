"""Ctx —— Feature 计算上下文：pull + memo。

`get(node, name)` 拉一个特征值：先查 memo、再查 `node.facts`（build/已烤的）、否则找产它的
Feature 现算（其 produces 一并缓存）。依赖经递归 get 自解，故引擎不用排序、不用 bottom-up。
`raw` 有无随数据源——完整 trace 给 `raw_fn`，重载的瘦 IR 不给（读 raw 的 feature 自然降级返空）。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from trace_harness.feature.registry import FeatureRegistry
from trace_harness.model.node import Node


class Ctx:
    def __init__(
        self,
        view: Any,
        raw_fn: Callable[[str], dict] | None,
        registry: FeatureRegistry,
    ) -> None:
        self._view = view
        self._raw_fn = raw_fn
        self._registry = registry
        self._memo: dict[tuple[str, str], Any] = {}

    def children(self, node: Node) -> list[Node]:
        return self._view.children(node)

    def raw(self, span_id: str) -> dict:
        """物理 span 原文；瘦 IR（无 raw_fn）上返回 {}，读 raw 的 feature 自然降级、不可用。"""
        return self._raw_fn(span_id) if self._raw_fn else {}

    def get(self, node: Node, name: str) -> Any:
        key = (node.node_id, name)
        if key in self._memo:
            return self._memo[key]
        if name in node.facts:  # build / 已烤进 facts 的，直接用
            self._memo[key] = node.facts[name]
            return self._memo[key]
        self._memo[key] = None  # 先占位防环：compute 递归里再入同 key → 拿到 None、断环
        f = self._registry.producing(name, node)  # 找产 name 的 Feature 现算
        if f is not None:
            for k, v in (f.compute(node, self) or {}).items():
                self._memo[(node.node_id, k)] = v
        return self._memo.get(key)
