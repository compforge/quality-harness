"""Facet —— 某类 Node 的渲染策略（view 层）。

机制 vs biz：本模块定义机制（Facet 协议、ChildOp、默认兜底 facet）。业务 Facet 只声明
节点摘要与子节点布局意图；递归、DisplayNode 组装和输出格式始终由 harness engine 负责。

约束（与 IR 解耦的关键）：facet 只决定"本层怎么显示、孩子怎么摆"，**绝不改 Node 父子**；
能折叠 / 同父兄弟聚合 ×N / 子树摘要，不能 re-parent。match 只读 `node.facts`（kind、将来的
agent_type、cost…），不抠原生 span——facts 是 model↔view 唯一契约。

两档覆盖力度：
- 只覆盖 `brief`：换本行内容（多数 facet）。
- 再覆盖 `layout`：折叠 / 聚合 / 摘要孩子（结构型）。

Facet 不提供自定义递归或整片 render 的逃生口，避免每个业务形成一套互不兼容的 renderer。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, TypeAlias

from trace_harness.model.node import Field, Finding, Node

if TYPE_CHECKING:
    from trace_harness.model.viewtree import ViewTree


TracePerspective: TypeAlias = Literal["full", "agent"]
PerspectiveLevel: TypeAlias = Literal["primary", "context", "detail"]


# —— ChildOp：facet 对每个孩子声明的处置意图，由 engine 执行（facet 不自己递归）——


@dataclass
class Expand:
    """正常递归渲染这个子（默认）。"""

    node: Node


@dataclass
class Fold:
    """折叠这个子：不显示，但其子树 finding 会上浮到本节点；engine 把同父的 Fold 汇成一行摘要。"""

    node: Node


@dataclass
class Aggregate:
    """把一组**同父兄弟**聚成一行 ×N（不展开各自子树）。"""

    nodes: list[Node]
    label: str = ""


@dataclass
class Summarize:
    """把这个子的整棵子树压成一行自定义内容，不递归。"""

    node: Node
    line: list[Field]


@dataclass
class Hide:
    """折叠这个子：不显示、**也不出"… +N 折叠"摘要行**（信息已在父节点别处，如 http_status 已在
    model-call brief 上）。其子树 finding 仍上浮到最近可见祖先。与 Fold 的区别：Fold 留计数摘要、
    Hide 不留痕。"""

    node: Node


@dataclass
class Group:
    """把一组**连续同父兄弟**收成一个**命名虚拟概念**的合成行（`kind=""`）——biz 的折叠自定义手柄。

    与 Aggregate（"同类 ×N"、文案固定）的区别：成员可**异类**（如一个 turn =
    PLANNER+TOOL_EXEC+COMPRESSION），label/brief 自定义。成员渲进 DisplayNode.children，
    像真节点一样可展开/收缩：
    - `collapsed=True`：静态 md 只出摘要行、交互层（html/treecli）可展开（降 node 数）；
    - `collapsed=False`：默认展开（成员可见），用于"同概念但有信号要露出"的那个。

    成员经 `node_ids` 追踪（finding 上浮 / 展开）。虚拟概念只活在 view 层、不进 model.Node。"""

    nodes: list[Node]
    label: str
    brief: list[Field] = field(default_factory=list)
    collapsed: bool = True


ChildOp = Expand | Fold | Aggregate | Summarize | Hide | Group


@dataclass
class RenderConfig:
    """view 配方：控制折叠等呈现策略；perspective 只决定看 full 还是 agent 侧重点。"""

    prune_below_ms: float | None = None  # 给定则：耗时低于此、且子树无 finding/error 的孩子折叠
    max_depth: int | None = None
    expand: set[str] = field(default_factory=set)  # treecli：用户强制展开的 node_id
    perspective: TracePerspective = "full"


@dataclass
class RenderCtx:
    """engine 传给 facet 的只读上下文：结构 + 判读 + 配方。"""

    view: ViewTree
    findings: dict[str, list[Finding]]
    flagged: dict[str, bool]  # node_id → 子树是否含 finding/error（剪枝时必留）
    config: RenderConfig


class Facet:
    """渲染策略基类。子类至少实现 `match`；按需覆盖 brief / layout / render。"""

    priority: int = 0  # specificity：越大越先认领；同分按注册序

    def match(self, node: Node) -> bool:
        raise NotImplementedError

    def brief(self, node: Node) -> list[Field]:
        """本节点这一行的内容。默认 = assemble 期烤好的 ctx-free brief（IR 自洽兜底）。"""
        return node.brief

    def perspective_level(
        self, node: Node, perspective: TracePerspective
    ) -> PerspectiveLevel | None:
        """当前侧重点下的展示权重；默认由统一 engine 当作 detail 处理。"""
        return None

    def layout(self, node: Node, children: list[Node], rctx: RenderCtx) -> list[ChildOp]:
        """孩子布局。默认全展开。"""
        return [Expand(c) for c in children]


class DefaultFacet(Facet):
    """兜底 facet：读 node.brief + 按 prune_below_ms 折叠零碎子节点，对齐 callstack。"""

    priority = -(1 << 30)  # 永远最后兜底

    def match(self, node: Node) -> bool:
        return True

    def layout(self, node: Node, children: list[Node], rctx: RenderCtx) -> list[ChildOp]:
        cut = rctx.config.prune_below_ms
        if cut is None:
            return [Expand(c) for c in children]
        ops: list[ChildOp] = []
        for c in children:
            if c.duration_ms < cut and not rctx.flagged.get(c.node_id):
                ops.append(Fold(c))  # engine 把同父的 Fold 汇成一行折叠摘要
            else:
                ops.append(Expand(c))
        return ops
