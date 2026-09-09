"""Node —— 分析本体：一次逻辑调用 = 1..N 个物理 span（primary + 卫星）熔成。

贫血 dataclass，per-kind 标准化行为不在这（走 KindSpec）。
**不内嵌 children**：只带 `parent_node_id` 边，子节点列表是 viewtree 视图期产物
（doc 立场①：tree 退为视图期索引）。

facts 二分（doc 核心模型）：度量填值（duration_ms/in_tokens，自洽可排序），
原文填指针（prompt/completion 存 span_id，按需经 raw_attr 回原文，不进 node 内存）。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Node:
    kind: str
    name: str
    primary_span_id: str
    span_ids: list[str]  # primary + 认领的卫星
    facts: dict  # spec.build 产出的命名特征列
    start_ms: float
    duration_ms: float
    service: str | None
    node_id: str  # = primary_span_id（稳定唯一）
    parent_node_id: str | None = None
    error_span_ids: list[str] = field(default_factory=list)
    # —— assemble 期烤好的渲染派生（doc 立场①：投影一次，渲染器/treecli 零 kind 依赖）——
    # per-kind 一行投影（assemble bake 期由 spec.project 烤入）；渲染器读这里、不回 ctx/kind 重算，
    # 故能脱离 ctx/kind 渲染（含 nodes.json）。
    brief: list[Field] = field(default_factory=list)
    # 错误原文摘要（首个出错 span）；有错才非空，渲染器内联标注不必回 raw span。
    error_text: str = ""

    @property
    def end_ms(self) -> float:
        return self.start_ms + self.duration_ms

    @property
    def has_error(self) -> bool:
        return bool(self.error_span_ids)

    @property
    def error_anchor(self) -> str:
        """错误原文锚点 span：有错取首个出错 span，否则回退 primary。"""
        return (self.error_span_ids or [self.primary_span_id])[0]


@dataclass
class Field:
    label: str
    value: str
    emphasis: str = "normal"  # normal/dim/strong —— 语义，renderer 转格式


@dataclass
class Finding:
    """统一判读输出：错误/离群/趋势/拓扑/probe/跨 trace 同流，render 读 Finding 上色。

    **scoped**（对齐 Langfuse score 的可空 observation_id）：判读可以挂在三级——
    node（单条调用，ref=node_id）、trace（整条 trace 结论，ref=trace_id）、cohort（跨 trace
    结论，ref=None）。这样 table 级算子（contrast / signature / fleet）产出的跨 trace 发现
    与单 trace detector 的发现是同一种一等公民，统一进 findings 表、统一渲染。

    `ref` 是首参 + scope 默认 node，保留旧 `Finding(node_id, source, severity, ...)` 位置参形态。
    """

    ref: str | None  # scope=node→node_id / trace→trace_id / cohort→None
    source: str  # "error" / "outlier:in_tokens" / "contrast:in_chars" / ...
    severity: str  # info/warn/error
    scope: str = "node"  # node | trace | cohort
    rank: int | None = None
    note: str = ""
    data: dict = field(default_factory=dict)  # 结构化载荷（contrast 的分位、签名的样本等）
    # 用户抱怨标签（慢/卡顿/空回复/不准…）：把"模糊抱怨 → 精确 finding"的桥。
    # 一句"为何觉得慢"→ 滤 symptoms 含"慢"的 finding → 顺 causes 给方向。空=非面向抱怨的机械特征。
    symptoms: tuple[str, ...] = ()
    # 归因（一对多）：造成本 finding 的更细 node_id / finding source 引用。
    # 让 trace 会说话："慢 ← EmotionDetection 14s"。机械 finding（outlier 等）多是被引用的 cause。
    causes: tuple[str, ...] = ()

    @property
    def node_id(self) -> str | None:
        """向后兼容：node-scope Finding 的 node_id 即 ref。"""
        return self.ref
