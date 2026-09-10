"""facet-engine 渲染回归。

P1 立场（机制就位、输出对齐 callstack）已过去：P2b 1:1 去融合后 http 自成子 node，
`ModelCallFacet` **默认 Hide http 子**——engine 故意偏离旧 `callstack`（后者无 facet、原样显示
http 子 node）。本文件验证这个分派 + 折叠行为，以及 http 结果仍经 derive 卷到 model-call 上。
"""

from __future__ import annotations

from pathlib import Path

from trace_harness.analyze.diagnose import diagnose
from trace_harness.ingest.load import build_context
from trace_harness.view import engine
from trace_harness.view.facet import DefaultFacet, RenderConfig
from trace_harness.view.facets import builtin_facets
from trace_harness.view.facets.agent import AgentFacet, ToolCallFacet
from trace_harness.view.facets.model_call import ModelCallFacet
from trace_harness.view.facets.service import ServiceFacet
from trace_harness.view.registry import FacetRegistry

FIXTURE = Path(__file__).parents[4] / "conformance" / "trace" / "fixtures" / "genai-basic.jsonl"


def _ctx():
    return build_context(FIXTURE)


def test_modelcall_facet_hides_success_http_but_surfaces_error_http():
    ctx = _ctx()
    eng = engine.render_callstack(ctx)
    # 通用策略允许 ModelCallFacet 隐藏成功 http，但 error node 必须强制浮出。
    assert eng.count("- http `POST /chat/completions`") == 1
    # 但 http 结果仍在：model-call brief 带 http_status（derive 从 http 子卷上）
    assert "http=200" in eng and "http=504" in eng


def test_modelcall_facet_dispatched():
    ctx = _ctx()
    mc = [n for n in ctx.nodes if n.kind == "model-call"]
    registry = FacetRegistry(builtin_facets())
    assert mc and isinstance(registry.dispatch(mc[0]), ModelCallFacet)


def test_facet_dispatch_by_kind():
    ctx = _ctx()
    registry = FacetRegistry(builtin_facets())
    svc = [n for n in ctx.nodes if n.kind == "service"]
    if svc:  # fixture 含残余 service 组时命中 ServiceFacet
        assert isinstance(registry.dispatch(svc[0]), ServiceFacet)
    agent = next(n for n in ctx.nodes if n.kind == "agent")
    tool = next(n for n in ctx.nodes if n.kind == "tool-call")
    assert isinstance(registry.dispatch(agent), AgentFacet)
    assert isinstance(registry.dispatch(tool), ToolCallFacet)
    # http 等无专属 facet 的 kind → 兜底 DefaultFacet
    plain = [n for n in ctx.nodes if n.kind not in ("service", "model-call", "agent", "tool-call")]
    assert plain
    assert type(registry.dispatch(plain[0])) is DefaultFacet


async def test_engine_renders_findings():
    ctx = _ctx()
    out = engine.render_callstack(ctx, (await diagnose(ctx)))
    assert out.startswith("trace_id:") and "findings:" in out


def test_render_md_folds_http_via_engine():
    from trace_harness.view import render_md

    md = render_md(_ctx())
    assert md.startswith("# trace ")
    assert md.count("- http `POST /chat/completions`") == 1  # 成功 http 藏，错误 http 浮出
    assert "http=200" in md  # model-call brief 仍带 http_status（derive 卷上）


def test_render_interactive_via_engine_smoke():
    from trace_harness.view.interactive import render_interactive

    h = render_interactive(_ctx())
    # 交互页也走 engine 的 DisplayNode（facet 折叠）+ 保留交互 JS（折叠/展开）。
    assert h.startswith("<!doctype html>")
    assert "renderInto" in h and "调用栈" in h and "火焰图" in h
    assert 'data-perspective="full"' in h and 'data-perspective="agent"' not in h
    assert 'data-layout="tree"' in h and 'data-layout="flame"' in h


def test_node_tree_tool_name_uses_shared_budget_compaction():
    from trace_harness.view.interactive import render_interactive

    ctx = _ctx()
    tool = next(node for node in ctx.nodes if node.kind == "tool-call")
    tool.facts["tool"] = "shell"
    ctx.spans[tool.primary_span_id].attrs["gen_ai.tool.call.arguments"] = (
        '{"command":"python3 references/scripts/stream_query.py --question example"}'
    )

    h = render_interactive(ctx)

    assert '"name_variants": ["shell · stream_query.py", "stream_query.py", "shell"]' in h
    assert "compactName(n,nameLayout?nameLayout.budget:48)" in h
    assert "compactName(n,Math.max" in h


def test_display_name_compaction_uses_ratio_budget():
    from trace_harness.view.display import DisplayName, DisplayNode, name_projections

    name = DisplayName("shell", "stream_query.py")

    assert name.compact(1) == ("shell · stream_query.py", 1)
    compacted, actual = name.compact(0.5)
    assert compacted != "shell · stream_query.py"
    assert actual < 0.5
    assert name_projections(name) == ["shell · stream_query.py", "stream_query.py", "shell"]
    assert name_projections(DisplayNode(kind="node", name="plain")) == ["plain"]


def test_name_projections_jump_between_reported_ratios():
    from trace_harness.view.display import name_projections

    class SparseName:
        calls = 0

        def compact(self, expect: float) -> tuple[str, float]:
            self.calls += 1
            if expect >= 1:
                return "x" * 1000, 1
            if expect >= 0.5:
                return "m" * 500, 0.5
            return "s" * 100, 0.1

    name = SparseName()

    assert name_projections(name) == ["x" * 1000, "m" * 500, "s" * 100]
    assert name.calls == 4


def test_agent_perspective_keeps_semantic_nodes_and_compresses_context_paths():
    from trace_harness.model.node import Node
    from trace_harness.model.viewtree import build_view

    def make_node(
        node_id: str,
        kind: str,
        parent_id: str | None = None,
        start: float = 0,
        *,
        error: bool = False,
    ):
        return Node(
            kind=kind,
            name=f"{node_id}.{kind}",
            primary_span_id=node_id,
            span_ids=[node_id],
            facts={},
            start_ms=start,
            duration_ms=10,
            service=None,
            node_id=node_id,
            parent_node_id=parent_id,
            error_span_ids=[node_id] if error else [],
        )

    root = make_node("root", "service")
    agent = make_node("agent", "agent", root.node_id, 1)
    bridge = make_node("bridge", "service", agent.node_id, 2)
    model = make_node("model", "model-call", bridge.node_id, 3)
    tool = make_node("tool", "tool-call", agent.node_id, 4)
    noise = make_node("noise", "service", agent.node_id, 5)
    error_http = make_node("error-http", "http", model.node_id, 6, error=True)
    roots = engine.render(
        build_view([root, agent, bridge, model, tool, noise, error_http]),
        config=RenderConfig(perspective="agent"),
    )
    flat = []
    stack = list(roots)
    while stack:
        display = stack.pop()
        flat.append(display)
        stack.extend(display.children)

    assert sorted(display.name for display in flat if display.kind) == sorted(
        [agent.name, model.name, tool.name]
    )
    assert any("上下文节点" in display.name for display in flat)
    assert noise.name not in [display.name for display in flat]
    assert error_http.name not in [display.name for display in flat]
    assert model.parent_node_id == bridge.node_id


def test_group_childop_named_virtual_concept_collapses_members():
    """Group ChildOp：把一组（可异类）兄弟收成一个命名虚拟概念合成行，默认折起、成员进 node_ids。"""
    from trace_harness.model.node import Field
    from trace_harness.view.facet import Facet, Group
    from trace_harness.view.registry import FacetRegistry

    ctx = _ctx()
    view = ctx.view()
    parent = next((n for n in ctx.nodes if len(view.children(n)) >= 2), None)
    assert parent is not None
    kids = view.children(parent)

    class _GroupFacet(Facet):
        priority = 1 << 20

        def match(self, node):
            return node.node_id == parent.node_id

        def layout(self, node, children, rctx):
            return [Group(list(children), label="⟨规划轮 ×N⟩", brief=[Field("sum", "1.2s", "dim")])]

    reg = FacetRegistry()
    reg.register(_GroupFacet())
    roots = engine.render(view, {}, registry=reg)

    def _find(d):
        if parent.node_id in d.node_ids:
            return d
        for c in d.children:
            r = _find(c)
            if r:
                return r
        return None

    pd = next(filter(None, (_find(r) for r in roots)), None)
    assert pd is not None
    grp = [c for c in pd.children if c.kind == "" and "规划轮" in c.name]
    assert len(grp) == 1
    g = grp[0]
    assert g.folded >= len(kids)  # collapsed：折叠整子树计数
    assert set(g.node_ids) == {k.node_id for k in kids}  # 成员经 node_ids 追踪
    assert len(g.children) == len(kids)  # 成员渲进 children（虚拟节点可展开），非空
    assert g.brief  # 携带 brief

    md = "\n".join(line for r in roots for line in engine.to_md_lines(r))
    assert "⟨规划轮 ×N⟩" in md  # 命名概念行
    assert "sum=" in md  # 合成行也渲 brief（_md_line 改动）
    # collapsed：静态 md 只出摘要行，成员子节点不下降（精简）
    for k in kids:
        assert f"`{k.name}`" not in md


def test_group_expanded_shows_members_in_static():
    """Group(collapsed=False)：展开态——静态 md 也露出成员（同概念但有信号、默认展开的那个）。"""
    from trace_harness.view.facet import Facet, Group
    from trace_harness.view.registry import FacetRegistry

    ctx = _ctx()
    view = ctx.view()
    parent = next((n for n in ctx.nodes if len(view.children(n)) >= 2), None)
    assert parent is not None
    kids = view.children(parent)

    class _ExpandedGroupFacet(Facet):
        priority = 1 << 20

        def match(self, node):
            return node.node_id == parent.node_id

        def layout(self, node, children, rctx):
            return [Group(list(children), label="⟨turn 8⟩", collapsed=False)]

    reg = FacetRegistry()
    reg.register(_ExpandedGroupFacet())
    roots = engine.render(view, {}, registry=reg)
    md = "\n".join(line for r in roots for line in engine.to_md_lines(r))
    assert "⟨turn 8⟩" in md  # 组头出现
    assert f"`{kids[0].name}`" in md  # collapsed=False：成员在静态里露出


def test_synthetic_line_without_brief_unchanged():
    """既有合成行（折叠/聚合，无 brief）输出不变：_md_line 给 kind='' 渲 brief 是 additive。"""
    from trace_harness.view.display import DisplayNode

    d = DisplayNode(kind="", name="… +3 个小节点折叠（<1s，sum 2s）", folded=3)
    assert engine._md_line(d, 1) == "  - … +3 个小节点折叠（<1s，sum 2s）"


def test_salience_aware_collapse_surfaces_error_member():
    """G1：折叠的 Group 里带 error 成员 → 静态 md 自动浮出该成员（salience-aware collapse），
    其余非 salient 成员仍收着。"""
    from trace_harness.model.node import Field, Finding
    from trace_harness.view.facet import Facet, Group
    from trace_harness.view.registry import FacetRegistry

    ctx = _ctx()
    view = ctx.view()
    parent = next((n for n in ctx.nodes if len(view.children(n)) >= 2), None)
    assert parent is not None
    kids = view.children(parent)
    hot, cold = kids[0], kids[1]  # hot 注入 error，cold 不注入

    class _GroupFacet(Facet):
        priority = 1 << 20

        def match(self, node):
            return node.node_id == parent.node_id

        def layout(self, node, children, rctx):
            return [Group(list(children), label="⟨turn ×N⟩", brief=[Field("sum", "1s", "dim")])]

    reg = FacetRegistry()
    reg.register(_GroupFacet())
    findings = {hot.node_id: [Finding(hot.node_id, "error", "error", note="boom")]}
    roots = engine.render(view, findings, registry=reg)
    md = "\n".join(line for r in roots for line in engine.to_md_lines(r))

    assert "⟨turn ×N⟩" in md  # 折叠头仍在
    assert f"`{hot.name}`" in md  # error 成员浮出
    # 其余非 salient 成员收着（取一个确实不带 finding 的 cold 验证）
    if cold.node_id != hot.node_id and not view.children(cold):
        assert f"`{cold.name}`" not in md


def test_findings_block_filters_structural_info():
    """G3：info finding（结构 signal）不进问题清单（problem_floor=warn）；但仍 flag 驱动 keep。"""
    from trace_harness.model.node import Finding
    from trace_harness.view.callstack import findings_block
    from trace_harness.view.engine import _flagged

    ctx = _ctx()
    node = ctx.nodes[0]
    findings = {
        node.node_id: [
            Finding(node.node_id, "spine", "info", note="backbone-marker"),
            Finding(node.node_id, "slow", "warn", note="slow-marker"),
        ]
    }
    block = findings_block(ctx, findings)
    assert "slow-marker" in block  # warn 进问题清单
    assert "backbone-marker" not in block and "spine" not in block  # info 结构 signal 不进

    # 但 info finding 仍 flag → 驱动 keep（不被 prune）
    flagged = _flagged(ctx.view(), {node.node_id: [Finding(node.node_id, "spine", "info")]})
    assert flagged[node.node_id]


def test_fold_iso_groups_merges_consecutive_units():
    """G2：相邻、同构、collapsed 的 Group 被引擎合并成 `label ×N`（facet 只 emit per-unit）。
    并验证不合并的边界：被其它 ChildOp 隔断 / 异 label / expanded（骨干）。"""
    from trace_harness.view.engine import _fold_iso_groups
    from trace_harness.view.facet import Expand, Group

    ctx = _ctx()
    c = ctx.nodes[0]  # 任意真实 node（带 name/duration_ms）

    # 3 个同构 collapsed Group → 合并成 1 个 `turn ×3`
    merged = _fold_iso_groups([Group([c], "turn"), Group([c], "turn"), Group([c], "turn")])
    assert len(merged) == 1
    assert isinstance(merged[0], Group) and merged[0].label == "turn ×3"
    assert len(merged[0].nodes) == 3 and merged[0].brief  # 成员合并 + sum brief

    # 边界：被 Expand 隔断 / 异 label / expanded（骨干）都不合并
    assert len(_fold_iso_groups([Group([c], "turn"), Expand(c), Group([c], "turn")])) == 3
    assert len(_fold_iso_groups([Group([c], "a"), Group([c], "b")])) == 2
    assert (
        len(_fold_iso_groups([Group([c], "t", collapsed=False), Group([c], "t", collapsed=False)]))
        == 2
    )


def test_interactive_collapses_folded_by_default():
    """HTML 交互页：折叠合成节点（folded>0）默认收起——payload 带 folded 字段、JS 有收起逻辑。"""
    from trace_harness.view.interactive import render_interactive

    h = render_interactive(_ctx())
    assert '"folded"' in h  # payload 传 folded 标记
    assert "(n.folded||n.collapsed)&&n.children.length" in h
