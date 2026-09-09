"""渲染引擎 —— 走原生 ViewTree，每 node 分派 facet，执行 ChildOp，产出 DisplayNode 树。

职责分工：**facet 只声明意图（brief + layout 的 ChildOp），递归与执行在 engine**。这样
facet 不持有遍历逻辑、不自己 recurse，机制集中、可换序列化器。

finding 按 node_id 绑到对应 DisplayNode（node 是 IR）；若该 node 被折叠（不可见），上浮到
最近的可见祖先 node。剪枝保护（含 finding/error 的子树不折）沿用 callstack 口径，故默认配方下
finding 节点恒可见，上浮是 0 成本兜底。

本模块还提供 `to_md_lines` 序列化与 `render_callstack` 对齐入口——复用 callstack 的 head /
findings_block / 格式化函数，使输出与旧 `view.callstack.render_callstack` 逐行一致。
"""

from __future__ import annotations

from trace_harness.kinds.base import _fmt_ms
from trace_harness.model.context import TraceContext
from trace_harness.model.node import Field, Finding, Node
from trace_harness.model.viewtree import ViewTree
from trace_harness.view.callstack import (
    _SEV_FLAG,
    _SEV_ORDER,
    _md_field,
    _prune_cutoff,
    findings_block,
)
from trace_harness.view.display import DisplayNode
from trace_harness.view.facet import (
    Aggregate,
    ChildOp,
    Expand,
    Fold,
    Group,
    Hide,
    RenderConfig,
    RenderCtx,
    Summarize,
)
from trace_harness.view.facets import builtin_facets
from trace_harness.view.perspective import project_perspective
from trace_harness.view.registry import FacetRegistry

# —— 剪枝/折叠辅助（与 callstack._subtree_flagged / _subtree_size 同口径）——


def _flagged(view: ViewTree, findings: dict[str, list[Finding]]) -> dict[str, bool]:
    flagged: dict[str, bool] = {}

    def mark(n: Node) -> bool:
        f = bool(findings.get(n.node_id)) or n.has_error
        for c in view.children(n):
            f = mark(c) or f
        flagged[n.node_id] = f
        return f

    for r in view.roots:
        mark(r)
    return flagged


def _subtree_size(view: ViewTree, n: Node) -> tuple[int, float]:
    cnt, total = 1, n.duration_ms
    for c in view.children(n):
        cc, ct = _subtree_size(view, c)
        cnt += cc
        total += ct
    return cnt, total


def _collapse_line(view: ViewTree, folded: list[Node], cut: float) -> DisplayNode:
    cnt = sum(_subtree_size(view, c)[0] for c in folded)
    total = sum(c.duration_ms for c in folded)
    return DisplayNode(
        kind="",
        name=f"… +{cnt} 个小节点折叠（<{_fmt_ms(cut)}，sum {_fmt_ms(total)}）",
        node_ids=[c.node_id for c in folded],
        folded=cnt,
    )


def _aggregate_line(op: Aggregate) -> DisplayNode:
    cnt = len(op.nodes)
    total = sum(n.duration_ms for n in op.nodes)
    label = op.label or (op.nodes[0].kind if op.nodes else "")
    return DisplayNode(
        kind="",
        name=f"{label} ×{cnt}（sum {_fmt_ms(total)}）",
        node_ids=[n.node_id for n in op.nodes],
        folded=cnt,
    )


def _group_line(view: ViewTree, op: Group) -> DisplayNode:
    """命名虚拟概念合成行：label 由 facet 自定义，brief 附 cost 等徽标，成员由调用方渲进 children。
    collapsed → folded 计**整子树** node 数（静态 md 据此只出摘要、不下降）；展开态 folded=0。
    node_ids 留成员供 finding 上浮 / 交互层展开。"""
    cnt = sum(_subtree_size(view, n)[0] for n in op.nodes) if op.collapsed else 0
    return DisplayNode(
        kind="",
        name=op.label,
        brief=op.brief,
        node_ids=[n.node_id for n in op.nodes],
        folded=cnt,
    )


def _group_sig(op: Group) -> tuple:
    """collapsed Group 的同构签名：(label, 成员 node 名序列)。相邻同签名者折成 ×N。
    用成员 node **名**（浅口径）——biz 给可折叠 unit 同一 label 即视为同种，不抠深层工具差异。"""
    return (op.label, tuple(n.name for n in op.nodes))


def _fold_iso_groups(ops: list[ChildOp]) -> list[ChildOp]:
    """把相邻、同构、collapsed 的 Group 合并成一个 `label ×N`（N=折叠的 unit 数），sum 求和。

    给 biz 的便利：facet 只 emit **每 unit 一个** collapsed Group（如每轮一个 `turn`），数 ×N /
    求和 / 合并由引擎办——biz 不必自己数、自己折。**只动 biz 已声明为 collapsed 的 Group**（折什么
    仍由 biz 决定）；expanded Group（骨干）与其它 ChildOp 原样。"""
    out: list[ChildOp] = []
    i, n = 0, len(ops)
    while i < n:
        op = ops[i]
        if isinstance(op, Group) and op.collapsed:
            sig = _group_sig(op)
            j = i + 1
            while (
                j < n
                and isinstance(ops[j], Group)
                and ops[j].collapsed
                and _group_sig(ops[j]) == sig
            ):
                j += 1
            if j - i >= 2:
                members = [m for o in ops[i:j] for m in o.nodes]
                sum_ms = sum(m.duration_ms for m in members)
                out.append(
                    Group(
                        members,
                        label=f"{op.label} ×{j - i}",
                        brief=[Field("sum", _fmt_ms(sum_ms), "dim")],
                    )
                )
                i = j
                continue
        out.append(op)
        i += 1
    return out


# —— 引擎 ——


def render(
    view: ViewTree,
    findings: dict[str, list[Finding]] | None = None,
    *,
    registry: FacetRegistry | None = None,
    config: RenderConfig | None = None,
) -> list[DisplayNode]:
    """渲染整棵 ViewTree 为 DisplayNode（每个 root 一棵）。"""
    findings = findings or {}
    registry = registry or FacetRegistry(builtin_facets())
    config = config or RenderConfig()
    # engine 统一计算 error/finding 可见信号；facet 只声明布局意图，不各自实现可见性策略。
    flagged = _flagged(view, findings)
    rctx = RenderCtx(view=view, findings=findings, flagged=flagged, config=config)
    visible: dict[str, DisplayNode] = {}

    def render_node(node: Node) -> DisplayNode:
        facet = registry.dispatch(node)
        children = view.children(node)
        ops = _fold_iso_groups(facet.layout(node, children, rctx))  # 相邻同构 collapsed Group → ×N
        disp_children: list[DisplayNode] = []
        folded: list[Node] = []
        for op in ops:
            if isinstance(op, Expand):
                disp_children.append(render_node(op.node))
            elif isinstance(op, Fold):
                if flagged.get(op.node.node_id):
                    disp_children.append(render_node(op.node))
                else:
                    folded.append(op.node)
            elif isinstance(op, Aggregate):
                line = _aggregate_line(op)
                # 成员渲进 children：虚拟节点可展开；静态 md 按 folded 门控不下降
                line.children = [render_node(n) for n in op.nodes]
                disp_children.append(line)
            elif isinstance(op, Summarize):
                if flagged.get(op.node.node_id):
                    disp_children.append(render_node(op.node))
                else:
                    disp_children.append(
                        DisplayNode(
                            kind=op.node.kind,
                            name=op.node.name,
                            brief=op.line,
                            node_ids=[op.node.node_id],
                        )
                    )
            elif isinstance(op, Group):
                line = _group_line(view, op)
                line.children = [render_node(n) for n in op.nodes]
                disp_children.append(line)
            elif isinstance(op, Hide) and flagged.get(op.node.node_id):
                disp_children.append(render_node(op.node))
        if folded:
            disp_children.append(_collapse_line(view, folded, config.prune_below_ms))  # type: ignore[arg-type]
        d = DisplayNode(
            kind=node.kind,
            name=node.name,
            brief=facet.brief(node),
            node_ids=[node.node_id],
            children=disp_children,
        )
        visible[node.node_id] = d
        return d

    roots = [render_node(r) for r in view.roots]
    _bind_findings(view, findings, visible)
    return project_perspective(roots, view.by_id, registry, config.perspective)


def _bind_findings(
    view: ViewTree, findings: dict[str, list[Finding]], visible: dict[str, DisplayNode]
) -> None:
    """finding 锚 node_id：可见则挂该 DisplayNode；被折叠则上浮到最近可见祖先 node。"""
    by_id = view.by_id
    for nid, fs in findings.items():
        if not fs:
            continue
        target = visible.get(nid)
        if target is None:  # node 被折叠/未渲染 → 上浮
            cur = by_id.get(nid)
            cur = by_id.get(cur.parent_node_id) if cur and cur.parent_node_id else None
            while cur is not None and cur.node_id not in visible:
                cur = by_id.get(cur.parent_node_id) if cur.parent_node_id else None
            target = visible.get(cur.node_id) if cur is not None else None
        if target is not None:
            target.findings.extend(fs)


# —— 序列化：DisplayNode → markdown 缩进树（与 callstack 行格式一致）——

# salience-aware collapse 默认浮出阈值：折叠段里 ≥error 的成员强制浮出（绝不藏问题）。
# info/warn（含 biz 结构 signal、未校准 outlier）留在折叠里，待 severity 分级/组内相对校准后再放宽。
_SIGNAL_SURFACE_FLOOR = _SEV_ORDER["error"]


def _subtree_max_sev(d: DisplayNode) -> int:
    """DisplayNode 子树最高 finding 严重度（_SEV_ORDER 值；无 finding 则 0）。"""
    m = max((_SEV_ORDER.get(f.severity, 0) for f in d.findings), default=0)
    for c in d.children:
        m = max(m, _subtree_max_sev(c))
    return m


def to_md_lines(
    d: DisplayNode, depth: int = 0, *, signal_floor: int = _SIGNAL_SURFACE_FLOOR
) -> list[str]:
    lines = [_md_line(d, depth)]
    if d.folded:
        # 折起的合成行（Aggregate/Group collapsed / 小节点折叠）：**salience-aware collapse**——
        # 只浮出子树带 ≥floor signal 的成员（默认 error），其余收着、留给交互层展开。
        for c in d.children:
            if _subtree_max_sev(c) >= signal_floor:
                lines += to_md_lines(c, depth + 1, signal_floor=signal_floor)
        return lines
    for c in d.children:
        lines += to_md_lines(c, depth + 1, signal_floor=signal_floor)
    return lines


def _md_line(d: DisplayNode, depth: int) -> str:
    indent = "  " * depth
    if (
        not d.kind
    ):  # 合成行（折叠摘要 / 分组头）：无 kind、无反引号；有 brief 则附上（Group 命名概念用）
        brief = " ".join(_md_field(f) for f in d.brief)
        return f"{indent}- {d.name} {brief}".rstrip()
    if d.findings:
        sev = max(d.findings, key=lambda f: _SEV_ORDER.get(f.severity, 0)).severity
    else:
        sev = None
    flag = _SEV_FLAG.get(sev, "")
    brief = " ".join(_md_field(f) for f in d.brief)
    note = f"  ⟨{','.join(sorted({f.source for f in d.findings}))}⟩" if d.findings else ""
    return f"{indent}- {flag}{d.kind} `{d.name}` {brief}{note}"


def render_md(
    ctx: TraceContext,
    findings: dict[str, list[Finding]] | None = None,
    *,
    prune_below_ms: float | None = None,
    registry: FacetRegistry | None = None,
) -> str:
    """facet-engine 版 markdown 全树（默认）/剪枝树（给 prune_below_ms），对齐旧
    `callstack.render_md`（`# trace` 头 + 缩进树），但经 facet 折叠（http/代理链等）。"""
    roots = render(
        ctx.view(),
        findings or {},
        registry=registry,
        config=RenderConfig(prune_below_ms=prune_below_ms),
    )
    lines: list[str] = []
    for d in roots:
        lines += to_md_lines(d, 0)
    return "\n".join([f"# trace {ctx.trace_id}", *lines])


def render_callstack(
    ctx: TraceContext,
    findings: dict[str, list[Finding]] | None = None,
    *,
    node_threshold: int = 120,
    service_kind: str = "service",
    registry: FacetRegistry | None = None,
) -> str:
    """facet-engine 版调用栈，对齐旧 `callstack.render_callstack`：head + 树 + findings 块。"""
    skel = [n for n in ctx.nodes if n.kind != service_kind]
    svc = [n for n in ctx.nodes if n.kind == service_kind]
    head = (
        f"trace_id: {ctx.trace_id}  nodes: {len(skel)} "
        f"(+{len(svc)} {service_kind} 组) / spans: {ctx.span_count}"
    )
    prune: float | None = None
    if len(skel) > node_threshold:
        head += f"  [骨架 {len(skel)}>{node_threshold}，剪枝树：留大头+错误+判读]"
        prune = _prune_cutoff(skel)
    roots = render(
        ctx.view(), findings or {}, registry=registry, config=RenderConfig(prune_below_ms=prune)
    )
    lines: list[str] = []
    for d in roots:
        lines += to_md_lines(d, 0)
    return "\n".join([head, "", *lines, "", findings_block(ctx, findings)])
