"""explore + IR 回归：assemble 烤 brief → nodes.json 契约 → 缩略图/expand/同构折叠/selector。

立场：nodes.json 是 assemble（域感知）与 explore（通用）唯一契约——渲染器吃序列化 IR 即可
出树（读 brief、不回 ctx）；treecli 在 IR 上做有状态探索（缩略图 = 空展开集，expand 展开一层，
同构兄弟折叠成 ×N）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trace_harness.ingest.assemble import assemble
from trace_harness.ingest.load import build_context
from trace_harness.kinds import genai
from trace_harness.model.ir import TraceView, dump_view, is_nodes_file, load_view
from trace_harness.model.span import NormSpan
from trace_harness.view.engine import render_callstack
from trace_harness.view.explore import render_explore
from trace_harness.view.state import ViewState, handle, resolve_selector

FIXTURE = Path(__file__).parents[4] / "conformance" / "trace" / "fixtures" / "genai-basic.jsonl"

_MODEL = {"gen_ai.operation.name": "chat", "gen_ai.request.model": "m"}


@pytest.fixture
def ctx():
    return build_context(FIXTURE)


def _span(sid, parent, name, dur, attrs, *, start=0.0, error=False):
    return NormSpan(
        span_id=sid,
        parent_span_id=parent,
        name=name,
        start_ms=start,
        dur_ms=dur,
        service="svc",
        has_error=error,
        attrs=attrs,
        raw={"traceID": "t"},
    )


# —— IR 契约：assemble 烤 brief/error_text → nodes.json 自包含 → 可反序列化重建结构 ——


def test_ir_roundtrip(tmp_path, ctx):
    p = dump_view(ctx, tmp_path / "n.json")
    assert is_nodes_file(p)
    tv = load_view(p)
    assert tv.trace_id == ctx.trace_id
    assert len(tv.nodes) == len(ctx.nodes)
    assert tv.span_count == ctx.span_count
    # per-kind 投影烤进了 IR（不再依赖 ctx/kind）
    planner = next(n for n in tv.nodes if n.name == "chat planner")
    assert planner.brief and any("model-alpha-seed-2" in f.value for f in planner.brief)
    # 错误原文烤进了 IR
    synth = next(n for n in tv.nodes if n.name == "chat synth")
    assert "ModelTPOTTimeoutError" in synth.error_text
    # 父子结构从平 node 集重建
    vt = tv.view()
    agent = next(n for n in tv.nodes if n.kind == "agent")
    assert len(vt.children(agent)) == 3


def test_loaded_ir_renders_via_callstack(tmp_path, ctx):
    # 统一性：既有渲染器吃序列化 IR（零 ctx）也能出树，brief 来自烤进 IR 的字段
    tv = load_view(dump_view(ctx, tmp_path / "n.json"))
    out = render_callstack(tv)
    assert "chat planner" in out
    assert "model-alpha-seed-2" in out


# —— 缩略图：空展开集只铺顶层，子节点折叠成 ⊕ 提示；expand 后子节点现身 ——


def test_thumbnail_collapses_then_expand_reveals(ctx):
    tv = TraceView.from_context(ctx)
    thumb = render_explore(tv, ViewState())
    assert "invoke_agent main" in thumb
    assert "⊕" in thumb  # 有可展开折叠点
    assert "chat planner" not in thumb  # 子节点默认折叠
    agent = next(n for n in ctx.nodes if n.kind == "agent")
    opened = render_explore(tv, ViewState(expanded={agent.node_id}))
    assert "chat planner" in opened  # 展开父后子节点现身


# —— 同构兄弟折叠：一次 eval 跑出的 N 个同形子树 → 一行 ×N ——


def test_isomorphic_siblings_collapse_to_xN():
    spans = {}
    for i in range(3):
        spans[f"agent{i}"] = _span(
            f"agent{i}", None, "invoke_agent x", 5000, {"gen_ai.agent.name": "x"}, start=i * 10000
        )
        spans[f"call{i}"] = _span(f"call{i}", f"agent{i}", "chat", 1000, _MODEL, start=i * 10000)
    ctx = assemble(spans, genai.specs())
    out = render_explore(TraceView.from_context(ctx), ViewState())
    assert "×3" in out  # 3 个同构 agent 折叠成一行
    assert out.count("invoke_agent x") == 1  # 不是铺 3 行


# —— selector：AI 把「想看 X」翻成这些 ——


def test_resolve_selector_variants(ctx):
    nodes = ctx.nodes
    assert resolve_selector(nodes, "planner")  # 名字子串
    assert len(resolve_selector(nodes, "kind:model-call")) == 2  # 按 kind
    assert len(resolve_selector(nodes, "error")) == 2  # 出错：synth model-call + http 504 子
    assert len(resolve_selector(nodes, "slowest")) == 1  # 序数
    n0 = nodes[0]
    assert resolve_selector(nodes, handle(n0)) == [n0.node_id]  # handle 回指
