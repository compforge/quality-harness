"""DisplayNode —— 渲染产物：一棵脱离 ctx/kind 的显示树。

facet 不拼字符串、不碰格式：它只产出结构化的 DisplayNode（本行 = kind/name/brief，
加 engine 绑上来的 findings），由序列化器（text/markdown/html/treecli）各自成文。一个 facet
也可以产出**合成节点**（kind="" 的 collapse 行 / plan 分组头）——没有对应 Node 时 node_ids 为空。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import ceil
from typing import Protocol, runtime_checkable

from trace_harness.model.node import Field, Finding


@runtime_checkable
class Compact(Protocol):
    """Choose a name for a target ratio and report its actual ratio.

    Serializers use ``actual`` as the next breakpoint, so implementations should return the
    highest-fidelity stable projection that fits ``expect``.
    """

    def compact(self, expect: float) -> tuple[str, float]: ...


def _name_length(value: str) -> int:
    return sum(2 if ord(character) > 255 else 1 for character in value)


@dataclass(frozen=True)
class DisplayName:
    name: str
    detail: str = ""

    def compact(self, expect: float) -> tuple[str, float]:
        candidates = (
            (f"{self.name} · {self.detail}", self.detail, self.name)
            if self.detail
            else (self.name,)
        )
        raw_length = max(1, _name_length(candidates[0]))
        projections = tuple(
            (name, _name_length(name) / raw_length) for name in dict.fromkeys(candidates)
        )
        target = max(0.0, min(1.0, expect))
        return next(
            (projection for projection in projections if projection[1] <= target),
            min(projections, key=lambda projection: projection[1]),
        )


def name_projections(named: DisplayNode | Compact, default_name: str = "") -> list[str]:
    """Serialize every distinct name needed by an integer display-width budget."""
    owner = named if isinstance(named, Compact) else DisplayName(default_name or named.name)
    raw, actual = owner.compact(1)
    raw_length = max(1, _name_length(raw))
    projections = [raw]
    budget = ceil(actual * raw_length) - 1
    while budget >= 0:
        expect = budget / raw_length
        name, actual = owner.compact(expect)
        if name not in projections:
            projections.append(name)
        if actual > expect:
            break
        # One projection covers every integer budget down to its actual ratio.
        budget = min(budget - 1, ceil(actual * raw_length) - 1)
    return projections


@dataclass
class DisplayNode:
    kind: str  # "" = 合成行（折叠摘要 / 分组头），序列化时不打 `name` 反引号
    name: str
    brief: list[Field] = field(default_factory=list)
    node_ids: list[str] = field(default_factory=list)  # 本行代表的 Node；合成行为空，聚合行为 N 个
    children: list[DisplayNode] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)  # engine 按 node_id 绑定（含上浮的）
    folded: int = 0  # 本行折叠/聚合掉多少个原生 Node（× N / rollup 提示）
