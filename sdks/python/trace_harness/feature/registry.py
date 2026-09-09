"""Feature 注册表。

``FeatureRegistry`` 是每个 ``TraceHarness`` 拥有的 scoped 配置。新能力通过
``TraceContributions`` 显式传入，import 顺序不参与运行语义。
"""

from __future__ import annotations

from collections.abc import Iterable

from trace_harness.feature.feature import Feature
from trace_harness.model.node import Node


class FeatureRegistry:
    """一次 trace 分析使用的 Feature 集。

    声明顺序有语义：多个 Feature 产出同名值时，第一个适用者胜出。
    """

    def __init__(self, features: Iterable[Feature] = ()) -> None:
        self._features = list(features)

    def register(self, feature: Feature) -> Feature:
        self._features.append(feature)
        return feature

    def registered(self) -> list[Feature]:
        return list(self._features)

    def producing(self, name: str, node: Node) -> Feature | None:
        for feature in self._features:
            if name in feature.produces and feature.applies(node):
                return feature
        return None
