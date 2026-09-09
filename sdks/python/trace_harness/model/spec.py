"""KindSpec —— 横切两条流水线的语义包；物理上一 kind 一文件，逻辑上按契约分层。

每个消费者只声明自己的 facet（接口隔离）：

    Assembly contract   matches / claims / build   → assemble 消费（识别 + 卫星认领 + facts 抽列）
    Detection contract  metrics / rules            → diagnose 消费（离群/趋势 + per-kind 判读）
    IR projection       project                    → assemble 烤入 Node.brief

`build` 是**业务字段隔离边界**（= Canopy feature lambda）：raw 的业务 specific 字段
（gen_ai.* 之类）抽成命名 facts 列后即止，下游 report/diagnose/corpus 只见列名、零业务知识。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from trace_harness.model.node import Field, Node
from trace_harness.model.span import NormSpan

if TYPE_CHECKING:
    from trace_harness.model.context import TraceContext


# 类型别名（仅作可读签名，不强制）
Matcher = Callable[[NormSpan], bool]
# claims：吃 primary span + 其后代候选 span，返回要认领为卫星的 span_id 集
Claimer = Callable[[NormSpan, list[NormSpan]], set]
# build：吃 primary + 已认领卫星，产 facts 列（业务字段到此为止）
Builder = Callable[[NormSpan, list[NormSpan]], dict]
# rule：吃 node + ctx，产 Finding 列表（per-kind 域判读）
Rule = Callable[["Node", "TraceContext"], list]


@dataclass
class KindSpec:
    kind: str
    # —— Assembly contract ——
    matches: Matcher
    claims: Claimer | None = None
    build: Builder | None = None
    # —— Detection contract ——
    metrics: dict[str, Callable[[Node], float | None]] = field(default_factory=dict)
    # per-metric 离群策略："ratio"（≥N× 同类中位，默认）| "topn"（永远标最大的 N 个——
    # 适合 result_bytes/in_chars 这类无自然基线、看最反常几个的规模度量）
    strategy: dict[str, str] = field(default_factory=dict)
    rules: tuple[Rule, ...] = ()
    # 该 kind 的节点是否参与 obs_hole 检测（默认参与）。跨服务的「聚合容器」kind（如 AS 的
    # agent）应关掉：断链时子树天然不在它下面，空洞是拓扑事实不是缺埋点，报了全是误报。
    obs_hole: bool = True
    # —— IR projection —— per-kind 一行投影（assemble bake 期烤进 node.brief）
    project: Callable[[Node], list[Field]] | None = None


class SpecSet:
    """显式注册的 KindSpec 集合（无模块级全局表）。matches 顺序 = 识别优先级。"""

    def __init__(self, specs: list[KindSpec]):
        self._specs = list(specs)
        self._by_kind = {s.kind: s for s in specs}

    def __iter__(self):
        return iter(self._specs)

    def classify(self, span: NormSpan) -> KindSpec | None:
        """第一个 matches 命中的 spec 胜出；都不命中返回 None（→ 残余 generic 节点）。"""
        for spec in self._specs:
            if spec.matches(span):
                return spec
        return None

    def get(self, kind: str) -> KindSpec | None:
        return self._by_kind.get(kind)


def merge(*specsets: SpecSet) -> SpecSet:
    """组合多个 SpecSet（如通用 genai + 域 AS kinds），前者优先。"""
    out: list[KindSpec] = []
    for ss in specsets:
        out.extend(ss)
    return SpecSet(out)
