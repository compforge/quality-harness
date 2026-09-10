"""cohort 层回归：Source / Cohort（of + select）/ contrast / 源头去重 / Finding scope。

合成 span 覆盖小 fixture 触不到的两条判读：错误传播去重、失败 vs 成功 contrast。
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from trace_harness import TraceContributions, TraceHarness
from trace_harness.analyze.diagnose import diagnose
from trace_harness.corpus.operators import contrast
from trace_harness.corpus.tables import CorpusTables, build_tables
from trace_harness.ingest.assemble import assemble
from trace_harness.ingest.sources.base import SpanQuery
from trace_harness.ingest.sources.jaeger_file import JaegerFileSource
from trace_harness.ingest.sources.opensearch import OpenSearchSource
from trace_harness.kinds import genai
from trace_harness.model.node import Finding
from trace_harness.model.span import NormSpan

FIXTURE = Path(__file__).parents[4] / "conformance" / "trace" / "fixtures" / "genai-basic.jsonl"


def _span(sid, parent, name, attrs, *, err=False, etype="", dur=1000, start=0.0, trace="t1"):
    return NormSpan(
        span_id=sid,
        parent_span_id=parent,
        name=name,
        start_ms=start,
        dur_ms=dur,
        service="svc",
        has_error=err,
        attrs=attrs,
        raw={"traceID": trace, "spanID": sid},
        error_events=[{"type": etype, "message": "boom", "stacktrace": ""}] if err else [],
    )


def _model(sid, parent, *, model="m1", in_tok=None, err=False, etype="", trace="t1"):
    a = {"gen_ai.operation.name": "chat", "gen_ai.request.model": model}
    if in_tok is not None:
        a["gen_ai.usage.input_tokens"] = str(in_tok)
    return _span(sid, parent, f"{model}.model-call", a, err=err, etype=etype, trace=trace)


# —— scoped Finding ——


def test_finding_scope_backcompat():
    # 旧位置参形态仍然成立，默认 node scope，node_id == ref
    f = Finding("nid", "error", "error", note="x")
    assert f.scope == "node" and f.ref == "nid" and f.node_id == "nid"
    # trace / cohort 级判读是一等公民
    c = Finding(None, "contrast:in_tokens", "info", scope="cohort", data={"a": 1})
    assert c.scope == "cohort" and c.ref is None and c.data == {"a": 1}


# —— Source 协议（离线）——


async def test_jaeger_file_source_select_and_fetch():
    src = JaegerFileSource(FIXTURE)
    try:
        ids = [tid async for tid in src.select(SpanQuery(error_only=True))]
        assert len(ids) == 1
        assert len(await src.fetch(ids[0], ())) == 6
    finally:
        await src.aclose()


async def test_dataset_single_callstack(tmp_path):
    h = TraceHarness(TraceContributions(specs=tuple(genai.specs())))
    async with h.open(JaegerFileSource(FIXTURE), work_dir=tmp_path) as session:
        dataset = await session.select()
        async with session.tree(dataset, next(dataset.members())) as analysis:
            assert sorted(n.kind for n in analysis.trace.nodes) == [
                "agent",
                "http",
                "http",
                "model-call",
                "model-call",
                "tool-call",
            ]


async def test_selection_builds_complete_skeleton(tmp_path):
    h = TraceHarness(TraceContributions(specs=tuple(genai.specs())))
    async with h.open(JaegerFileSource(FIXTURE), work_dir=tmp_path) as session:
        errors = await session.select(SpanQuery(error_only=True))
        all_traces = await session.select()
        async with (
            session.tree(errors, next(errors.members())) as one,
            session.tree(all_traces, next(all_traces.members())) as two,
        ):
            assert len(one.trace.nodes) == len(two.trace.nodes) == 6


async def test_batch_rows_allow_kind_filter(tmp_path):
    h = TraceHarness(TraceContributions(specs=tuple(genai.specs())))
    async with h.open(JaegerFileSource(FIXTURE), work_dir=tmp_path) as session:
        result = await session.detect(await session.select())
        rows = [row for row in result.rows("facts") if row["kind"] == "model-call"]
        assert len(rows) == 2


# —— 源头 vs 传播去重（合成链：agent 与其 model-call 同错误签名）——


async def test_error_propagation_marks_ancestor_not_origin():
    spans = {
        "a": _span(
            "a",
            None,
            "code.agent",
            {"gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": "code"},
            err=True,
            etype="ModelTotalTimeoutError",
        ),
        "m": _model("m", "a", err=True, etype="ModelTotalTimeoutError"),
    }
    ctx = assemble(spans, genai.specs())
    findings = [f for fs in (await diagnose(ctx)).values() for f in fs]
    prop = [f for f in findings if f.source == "propagated"]
    # 祖先 agent 被标传播副本，源头 model-call 不被标
    assert {f.ref for f in prop} == {"a"}


# —— contrast：失败 vs 成功逐 metric 对比 ——


def test_contrast_splits_by_outcome():
    # 同 (kind,name) 的 model-call：失败 in_tokens 小、成功 in_tokens 大 → contrast 暴露两桶
    traces = []
    for i in range(4):  # 成功样本
        s = {"m": _model("m", None, in_tok=1000, err=False, trace=f"ok{i}")}
        traces.append((assemble(s, genai.specs()), {}))
    for i in range(3):  # 失败样本（输入反而更小）
        s = {"m": _model("m", None, in_tok=300, err=True, etype="X", trace=f"bad{i}")}
        traces.append((assemble(s, genai.specs()), {}))
    tables = build_tables(traces)
    rows = contrast(tables, split="has_error")
    in_tok = next(r for r in rows if r["metric"] == "in_tokens")
    assert in_tok["buckets"]["True"]["p50"] < in_tok["buckets"]["False"]["p50"]


def test_contrast_needs_two_buckets():
    # 只有成功样本 → 没有可对比的两桶
    t = CorpusTables(
        facts=[{"kind": "model-call", "name": "m", "has_error": False, "in_tokens": 1}],
        metric_cols={"in_tokens"},
    )
    assert contrast(t) == []


# —— OpenSearchSource：注入 transport，验证 nested 查询拼装 ——


async def test_opensearch_select_builds_nested_error_query():
    captured = {}

    async def handle(request):
        captured.update(json.loads(request.content))
        return httpx.Response(
            200, json={"aggregations": {"members": {"buckets": [{"key": {"trace": "t"}}]}}}
        )

    src = OpenSearchSource("http://h:9200", "jaeger-span-*", transport=httpx.MockTransport(handle))
    try:
        assert [
            tid
            async for tid in src.select(SpanQuery(attr_eq={"error.type": "Foo"}, error_only=True))
        ] == ["t"]
        filters = captured["query"]["bool"]["filter"]
        assert any("nested" in f for f in filters)
        assert filters[-1]["bool"]["minimum_should_match"] == 1
    finally:
        await src.aclose()


async def test_opensearch_fetch_by_trace_id():
    async def handle(request):
        payload = json.loads(request.content)
        assert payload["query"]["bool"]["filter"] == [{"term": {"traceID": "t1"}}]
        assert "tags" not in payload["_source"]
        return httpx.Response(
            200,
            json={
                "hits": {
                    "hits": [
                        {
                            "_id": "doc",
                            "_index": "i",
                            "_source": {
                                "traceID": "t1",
                                "spanID": "a",
                                "operationName": "op",
                                "startTime": 0,
                                "duration": 2000,
                            },
                        }
                    ]
                }
            },
        )

    src = OpenSearchSource("http://h", "i", transport=httpx.MockTransport(handle))
    try:
        span = (await src.fetch("t1", ()))["a"]
        assert span.storage_id == "doc" and span.storage_index == "i"
    finally:
        await src.aclose()


# —— render：tree-always + 剪枝（上收自 trace-as 的 render_show）——


def _tool(sid, parent, name, dur):
    return _span(sid, parent, f"{name}.tool", {"gen_ai.tool.name": name}, dur=dur)


def test_render_md_prunes_cheap_subtrees():
    from trace_harness.view.engine import render_md

    spans = {
        "r": _span(
            "r",
            None,
            "root.agent",
            {"gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": "r"},
            dur=1000,
        ),
        "h": _tool("h", "r", "heavy", 500),
        "c": _tool("c", "r", "cheap", 5),
    }
    ctx = assemble(spans, genai.specs())
    pruned = render_md(ctx, {}, prune_below_ms=100)
    assert "heavy" in pruned and "折叠" in pruned  # 大节点留、小节点折叠成摘要
    assert "cheap" not in pruned
    full = render_md(ctx, {})  # 不剪枝：都在
    assert "heavy" in full and "cheap" in full


async def test_render_callstack_keeps_tree_shape_and_findings():
    from trace_harness.view.engine import render_callstack

    ctx = assemble(
        {
            "r": _span(
                "r",
                None,
                "root.agent",
                {"gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": "r"},
                dur=1000,
            ),
            "m": _model("m", "r", err=True, etype="ModelTotalTimeoutError"),
        },
        genai.specs(),
    )
    out = render_callstack(ctx, (await diagnose(ctx)))
    assert out.startswith("trace_id:")  # 多分辨率头
    assert "- agent `root.agent`" in out  # tree 形状（缩进 + backtick）
    assert "🔴" in out  # 错误节点上色
    assert "findings:" in out  # 末尾 findings 块
