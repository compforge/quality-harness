"""Feature —— 从一个 node 算出的命名值。统一了旧 build / derive / repro：凡"从 node 数据算东西"
都是 Feature，靠两个**正交**标志区分形态，不是三种概念。

- `bake`：True 烤进 `node.facts`/IR（eager，如 self_ms/in_tokens）；False 按需算、不落（lazy，
  如 curl/bash）。决定输出在哪。
- 读 raw 还是 facts：是 `compute` 自己的事；raw 在不在是 `Ctx` 的能力（完整 trace vs 瘦 IR），
  **不按 feature 分窄宽**。决定能否在重载 IR 上跑（读 raw 的要原始 trace）。

依赖靠 `Ctx.get` 递归 pull + memo 自解（wall_ms 在 compute 里 `ctx.get(child,"wall_ms")`），
故**无 order、无显式 bottom-up**。`matches`/`claims` 是结构装配、不是 Feature——树先定，再算特征。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from trace_harness.model.node import Node


@dataclass
class Feature:
    produces: tuple[str, ...]  # 产出名，如 self_ms / in_tokens / curl
    applies: Callable[[Node], bool]
    compute: Callable[[Node, object], dict]  # (node, Ctx) -> {名: 值}；Ctx 见 ctx.py
    bake: bool = True
