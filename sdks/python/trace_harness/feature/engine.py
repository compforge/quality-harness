"""Feature 引擎 —— eager 的 `bake_features` 烤进 facts；lazy 的 `lazy_features` 按需取。

两条路共享一个 pull+memo 的 Ctx：依赖自解，故跑 node 的顺序无所谓（不用 bottom-up）。
"""

from __future__ import annotations

from collections.abc import Callable

from trace_harness.feature.builtins import BUILTIN_FEATURES
from trace_harness.feature.ctx import Ctx
from trace_harness.feature.registry import FeatureRegistry
from trace_harness.model.node import Node
from trace_harness.model.viewtree import build_view


def bake_features(
    nodes: list[Node],
    raw_fn: Callable[[str], dict] | None = None,
    registry: FeatureRegistry | None = None,
) -> None:
    """assemble 末尾：拉所有 `bake=True` 的 Feature，写进 `node.facts`（取代旧 run_derive）。"""
    registry = registry or FeatureRegistry(BUILTIN_FEATURES)
    ctx = Ctx(build_view(nodes), raw_fn, registry)
    eager = [f for f in registry.registered() if f.bake]
    for n in nodes:
        for f in eager:
            if f.applies(n):
                for k in f.produces:
                    v = ctx.get(n, k)
                    if v is not None:  # 没产出（如叶子的 self_ms）就不写，别拿 None 污染 facts
                        n.facts[k] = v


def lazy_features(
    node: Node,
    view: object,
    raw_fn: Callable[[str], dict] | None = None,
    registry: FeatureRegistry | None = None,
) -> dict:
    """consumer 按需：拉这个 node 上所有 `bake=False` 的 Feature（curl/bash…），返回 {名: 值}。"""
    registry = registry or FeatureRegistry(BUILTIN_FEATURES)
    ctx = Ctx(view, raw_fn, registry)
    out: dict = {}
    for f in registry.registered():
        if not f.bake and f.applies(node):
            for k in f.produces:
                out[k] = ctx.get(node, k)
    return out
