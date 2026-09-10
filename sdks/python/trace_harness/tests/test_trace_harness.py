"""trace_harness 1a 机制层回归：raw jaeger → fusion → node 树 → 文本视图。

fixture 是真实 ES jaeger-span 形状的小 trace（agent → model-call+http 卫星 → tool-call →
错误 model-call+504 http），覆盖 fusion 的卫星认领、facts 抽列、错误归一、父子边重连。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trace_harness.analyze.diagnose import diagnose
from trace_harness.analyze.verdict import evaluate_gates
from trace_harness.corpus import (
    build_tables,
    diff_runs,
    error_signatures,
    fleet_outliers,
    read_tables,
    run_experiment,
    write_tables,
)
from trace_harness.ingest.assemble import assemble
from trace_harness.ingest.load import build_context
from trace_harness.ingest.sources.jaeger_file import load_jaeger_file, normalize_es_doc
from trace_harness.kinds import genai
from trace_harness.model.span import NormSpan
from trace_harness.model.viewtree import build_view
from trace_harness.view.series import render_series
from trace_harness.view.text import render_text

FIXTURE = Path(__file__).parents[4] / "conformance" / "trace" / "fixtures" / "genai-basic.jsonl"


@pytest.fixture
def spans():
    return load_jaeger_file(FIXTURE)


@pytest.fixture
def ctx():
    return build_context(FIXTURE)


# —— source 归一 ——


def test_load_parses_all_spans(spans):
    assert len(spans) == 6
    assert set(spans) == {f"{d}" * 16 for d in range(1, 7)}


def test_parent_edges_from_references(spans):
    assert spans["1111111111111111"].parent_span_id is None
    assert spans["2222222222222222"].parent_span_id == "1111111111111111"
    assert spans["3333333333333333"].parent_span_id == "2222222222222222"


def test_error_flag_and_events(spans):
    bad = spans["5555555555555555"]
    assert bad.has_error
    assert bad.error_events[0]["type"] == "ModelTPOTTimeoutError"
    # http 卫星靠 otel.status_code 也标错
    assert spans["6666666666666666"].has_error
    # 正常 span 不标错
    assert not spans["2222222222222222"].has_error


def test_span_events_capture_non_exception(spans):
    # 非异常 span event（如 framework 的 event_loop_stall）也进 events，且不误标错
    doc = {
        "spanID": "aaaaaaaaaaaaaaaa",
        "operationName": "model-call",
        "startTime": 1_000_000,
        "duration": 2_000_000,
        "process": {"serviceName": "planit-server"},
        "logs": [
            {
                "timestamp": 1_500_000,
                "fields": [
                    {"key": "event", "value": "event_loop_stall"},
                    {"key": "gen_ai.app.loop_stall.duration_ms", "value": 75400.0},
                    {"key": "gen_ai.app.loop_stall.stack", "value": "File a.py line 1"},
                ],
            },
            {"fields": [{"key": "event", "value": "no_ts_event"}]},  # 无 timestamp
        ],
    }
    s = normalize_es_doc(doc)
    assert s is not None
    assert not s.has_error and s.error_events == []  # 非异常事件不标错
    assert len(s.events) == 2
    ev = s.events[0]
    assert ev["name"] == "event_loop_stall"
    assert ev["timestamp_ms"] == 1500.0
    assert ev["attrs"]["gen_ai.app.loop_stall.duration_ms"] == 75400.0
    # 缺 timestamp 的事件保留 None，区别于真 epoch-0
    assert s.events[1]["name"] == "no_ts_event"
    assert s.events[1]["timestamp_ms"] is None
    # 异常事件仍同时出现在 events（events 是 error_events 的超集）
    bad = spans["5555555555555555"]
    assert any(e["name"] == "exception" or e["attrs"].get("exception.type") for e in bad.events)


def test_no_kind_field_on_span(spans):
    # 立场：语义 kind 不在采集层，NormSpan 无 kind 属性
    assert not hasattr(spans["2222222222222222"], "kind")


def _ui_span(sid, parent, op, pid="p1"):
    return {
        "spanID": sid,
        "operationName": op,
        "references": [{"refType": "CHILD_OF", "spanID": parent}] if parent else [],
        "startTime": 1_000_000,
        "duration": 2_000,
        "tags": [{"key": "span.kind", "value": "node"}],
        "processID": pid,
    }


def test_load_bare_single_trace_object(tmp_path):
    # 根级单条 trace（信封被剥）：{traceID, spans, processes}——嗅探应认，等价于包了 data 信封
    trace = {
        "traceID": "a" * 32,
        "spans": [_ui_span("aa", None, "root.agent"), _ui_span("bb", "aa", "child.node")],
        "processes": {"p1": {"serviceName": "planit-server"}},
    }
    bare = tmp_path / "bare.json"
    bare.write_text(json.dumps(trace), encoding="utf-8")
    spans = load_jaeger_file(bare)
    assert set(spans) == {"aa", "bb"}
    assert spans["bb"].parent_span_id == "aa"  # references 父子边解析
    assert spans["aa"].service == "planit-server"  # processID 经 processes 解引用
    # 与 UI 信封形态结果一致
    wrapped = tmp_path / "wrapped.json"
    wrapped.write_text(json.dumps({"data": [trace]}), encoding="utf-8")
    assert load_jaeger_file(wrapped).keys() == spans.keys()


# —— fusion：卫星认领 + facts 抽列 ——


def test_http_is_child_node_not_fused(ctx):
    # 1:1（P2b 去融合）：6 物理 span → 6 node，两个 http 不再被吸收、自成 node
    kinds = sorted(n.kind for n in ctx.nodes)
    assert kinds == ["agent", "http", "http", "model-call", "model-call", "tool-call"]
    node_span_ids = {sid for n in ctx.nodes for sid in n.span_ids}
    assert node_span_ids == set(ctx.spans)  # 每个物理 span 都归属某 node
    # http 是 model-call 的子 node（经原生父子边，非吸收）
    byid = {n.node_id: n for n in ctx.nodes}
    https = [n for n in ctx.nodes if n.kind == "http"]
    assert https and all(byid[h.parent_node_id].kind == "model-call" for h in https)


def test_model_call_facts(ctx):
    planner = next(n for n in ctx.nodes if n.name == "chat planner")
    assert planner.kind == "model-call"
    assert planner.facts["model"] == "model-alpha-seed-2"
    assert planner.facts["in_tokens"] == 1820
    assert planner.facts["out_tokens"] == 640
    assert planner.facts["http_status"] == 200  # 由 derive 的 HttpStatusOp 从 http 子卷上
    assert planner.facts["io_span"] == "2222222222222222"  # 原文填指针
    assert pytest.approx(planner.facts["duration_ms"], rel=1e-6) == 3400.0


def test_error_node_surfaces_status_and_text(ctx):
    synth = next(n for n in ctx.nodes if n.name == "chat synth")
    assert synth.has_error
    assert synth.facts["http_status"] == 504  # 504 由 derive 从 http 子卷上
    assert "ModelTPOTTimeoutError" in ctx.error_text(synth.error_anchor)


def test_tool_call_facts(ctx):
    tool = next(n for n in ctx.nodes if n.kind == "tool-call")
    assert tool.facts["tool"] == "web_search"
    # result size + io pointer, mirroring model-call: the arguments/result live on
    # the tool span, so dump-io / the evidence probe can fetch WHAT ran + came back.
    assert tool.facts["result_bytes"] == len("sunny, 24C")
    assert tool.facts["io_span"] == "4444444444444444"


# —— 父子边（node 树重连，跳过被吸收的卫星）——


def test_node_edges_skip_satellites(ctx):
    agent = next(n for n in ctx.nodes if n.kind == "agent")
    children = [n for n in ctx.nodes if n.parent_node_id == agent.node_id]
    # agent 的三个直接逻辑子：planner / tool / synth（http 自成 node，但挂在 model-call 下）
    assert {n.name for n in children} == {
        "chat planner",
        "execute_tool web_search",
        "chat synth",
    }
    assert agent.parent_node_id is None


def test_node_has_no_children_attr(ctx):
    # 立场①：Node 不内嵌 children，子节点是 viewtree 视图期产物
    assert not hasattr(ctx.nodes[0], "children")
    view = build_view(ctx.nodes)
    agent = next(n for n in ctx.nodes if n.kind == "agent")
    assert len(view.children(agent)) == 3


# —— 文本视图 smoke ——


def test_render_text_smoke(ctx):
    out = render_text(ctx)
    assert "invoke_agent main" in out
    assert "[model-call] chat planner" in out
    assert "ModelTPOTTimeoutError" in out
    assert "model-alpha-seed-2" in out


# —— 多约定边界：OpenInference 标准别名（校准自 Langfuse 的 fallback 链，只收标准约定）——


def test_openinference_aliases_recognized():
    # OpenInference 用 llm.* 而非 gen_ai.*，无 operation.name；靠 model 名兜底识别为 model-call
    s = NormSpan(
        span_id="a",
        parent_span_id=None,
        name="llm",
        start_ms=0,
        dur_ms=1000,
        service="x",
        has_error=False,
        attrs={
            "llm.model_name": "gpt-4o",
            "llm.token_count.prompt": 12,
            "llm.token_count.completion": 3,
            "llm.token_count.total": 15,
        },
        raw={},
    )
    spec = genai.specs().classify(s)
    assert spec is not None and spec.kind == "model-call"
    facts = spec.build(s, [])
    assert facts["model"] == "gpt-4o"
    assert facts["in_tokens"] == 12
    assert facts["out_tokens"] == 3
    assert facts["total_tokens"] == 15


# —— 1b 判读层：四类生产者 → Finding ——


def _span(sid, parent, name, start_ms, dur_ms, attrs, *, service="svc", error=False):
    return NormSpan(
        span_id=sid,
        parent_span_id=parent,
        name=name,
        start_ms=start_ms,
        dur_ms=dur_ms,
        service=service,
        has_error=error,
        attrs=attrs,
        raw={"traceID": "t"},
    )


_MODEL_ATTRS = {"gen_ai.operation.name": "chat", "gen_ai.request.model": "m"}


async def test_error_finding_flows_through_diagnose(ctx):
    findings = await diagnose(ctx)
    synth = next(n for n in ctx.nodes if n.name == "chat synth")
    srcs = {m.source for m in findings.get(synth.node_id, [])}
    assert "error" in srcs


async def test_fixture_no_false_positive_findings(ctx):
    # fixture：2 个 model-call（不足 min_peers）、父子齐全、agent 空隙 <1s → 只该有 error
    findings = await diagnose(ctx)
    all_srcs = {m.source for ms in findings.values() for m in ms}
    assert all_srcs == {"error"}


async def test_detached_detector():
    spans = {"a": _span("a", "deadbeefdeadbeef", "chat", 0, 1_000, _MODEL_ATTRS)}
    findings = await diagnose(assemble(spans, genai.specs()))
    assert any(m.source == "detached" for m in findings.get("a", []))


async def test_obs_hole_detector():
    # 父 0–10s，唯一子 0–2s → 8s 空洞
    spans = {
        "p": _span("p", None, "invoke_agent x", 0, 10_000, {"gen_ai.agent.name": "x"}),
        "c": _span("c", "p", "chat", 0, 2_000, _MODEL_ATTRS),
    }
    findings = await diagnose(assemble(spans, genai.specs()))
    assert any(m.source == "obs_hole" for m in findings.get("p", []))


async def test_outlier_detector():
    # 4 个同类 model-call，一个 5× 慢 → duration_ms 离群
    spans = {}
    for i, dur in enumerate([1_000, 1_000, 1_000, 5_000]):
        spans[f"m{i}"] = _span(f"m{i}", None, "chat", i * 20_000, dur, _MODEL_ATTRS)
    findings = await diagnose(assemble(spans, genai.specs()))
    assert any(m.source == "outlier:duration_ms" for m in findings.get("m3", []))


async def test_empty_output_rule():
    attrs = {**_MODEL_ATTRS, "gen_ai.usage.output_tokens": 0}
    spans = {"m": _span("m", None, "chat", 0, 1_000, attrs)}
    findings = await diagnose(assemble(spans, genai.specs()))
    assert any(m.source == "empty_output" for m in findings.get("m", []))


async def test_probes_side_effect_gated(tmp_path, ctx):
    # 立场：probe 是唯一副作用环节，默认关——不开则 evidence_dir 不落地
    ctx.evidence_dir = tmp_path / "ev"
    (await diagnose(ctx, probes=False))
    assert not (tmp_path / "ev").exists()
    # 开启则落盘 + 产 probe:* Finding（synth 有 error → probe:error 原文）
    findings = await diagnose(ctx, probes=True)
    assert (tmp_path / "ev").exists()
    synth = next(n for n in ctx.nodes if n.name == "chat synth")
    assert any(m.source.startswith("probe:") for m in findings.get(synth.node_id, []))


async def test_render_text_shows_findings(ctx):
    spans = {
        "p": _span("p", None, "invoke_agent x", 0, 10_000, {"gen_ai.agent.name": "x"}),
        "c": _span("c", "p", "chat", 0, 2_000, _MODEL_ATTRS),
    }
    ctx2 = assemble(spans, genai.specs())
    out = render_text(ctx2, (await diagnose(ctx2)))
    assert "obs_hole" in out


# —— 1b-2：时序判读 + html/series 视图 ——


async def test_trend_detector_catches_gradual_drift():
    # 6 次 planner，每步都不离群（无单点 ≥3× 全局中位），但整体后段 ×3.5 于前段 → trend
    spans = {}
    for i, dur in enumerate([1000, 1100, 1200, 3000, 3500, 4000]):
        spans[f"m{i}"] = _span(f"m{i}", None, "chat", i * 20_000, dur, _MODEL_ATTRS)
    findings = await diagnose(assemble(spans, genai.specs()))
    all_findings = [m for ms in findings.values() for m in ms]
    assert any(m.source == "trend:duration_ms" for m in all_findings)
    assert not any(m.source.startswith("outlier") for m in all_findings)  # outlier 抓不到渐变


def test_render_series_sparkline():
    spans = {}
    for i, dur in enumerate([1000, 1000, 1000, 5000]):
        spans[f"m{i}"] = _span(f"m{i}", None, "chat", i * 20_000, dur, _MODEL_ATTRS)
    out = render_series(assemble(spans, genai.specs()), "model-call", "duration_ms")
    assert "n=4" in out
    assert any(bar in out for bar in "▁▂▃▄▅▆▇█")


async def test_render_interactive_single_file_page(ctx):
    from trace_harness.view.interactive import render_interactive

    page = render_interactive(ctx, (await diagnose(ctx)))
    assert "chat planner" in page  # 节点名进左树 payload
    assert "ModelTPOTTimeoutError" in page  # diagnose Finding 进右栏判读区
    assert "primary_span_id" in page  # span chips 下钻数据
    assert "全部展开" in page  # 折叠控制（交互页区别于静态表格的能力）
    assert "火焰图" in page and 'id="view-flame"' in page  # 菜单 + 火焰图功能区
    assert '"start_ms"' in page  # 火焰图时间轴依赖 node 的 wall-clock start
    js_body = page.split("<script>", 1)[1].rsplit("</script>", 1)[0]
    assert "</script>" not in js_body  # 嵌入 JSON 已转义 "</"，不会提前闭合标签


# —— step 2a：corpus 三表 + 三算子 ——


def _mk_ctx(tid, name, dur_ms, *, err=None, extra=None):
    attrs = {**_MODEL_ATTRS}
    if extra:
        attrs.update(extra)
    if err:
        attrs["otel.status_description"] = err
    spans = {"m": _span("m", None, name, 0, dur_ms, attrs, error=bool(err))}
    ctx = assemble(spans, genai.specs())
    ctx.trace_id = tid  # assemble 从 raw.traceID 取，fixture 里都是 't'；测试里显式给不同 id
    return ctx


async def test_build_tables_shapes(ctx):
    tables = build_tables([(ctx, (await diagnose(ctx)))])
    assert len(tables.traces) == 1
    assert tables.traces[0]["n_nodes"] == 6
    assert len(tables.facts) == 6  # 一行一 node（1:1：2 个 http 自成 node，不再熔进 model-call）
    assert {"duration_ms", "in_tokens", "out_tokens"} <= tables.metric_cols
    assert any(r["source"] == "error" for r in tables.findings)


async def test_error_signatures_cluster():
    # 504 / 503 归一到同一签名——散落两 trace 的同类错误收成一条
    a = _mk_ctx("t1", "chat", 1000, err="aigw 504 gateway timeout")
    b = _mk_ctx("t2", "chat", 1000, err="aigw 503 gateway timeout")
    tables = build_tables([(c, (await diagnose(c))) for c in (a, b)])
    sigs = error_signatures(tables)
    assert len(sigs) == 1
    assert sigs[0]["count"] == 2
    assert sigs[0]["n_traces"] == 2


async def test_fleet_outlier_cross_trace():
    # 单 trace 内每条只有一个 planner（无同类可比）；跨 6 条才看出那条 5× 慢
    ctxs = [_mk_ctx(f"t{i}", "chat", 1000) for i in range(5)] + [_mk_ctx("tbig", "chat", 5000)]
    tables = build_tables([(c, (await diagnose(c))) for c in ctxs])
    fo = fleet_outliers(tables, min_peers=5)
    assert any(o["metric"] == "duration_ms" and o["trace_id"] == "tbig" for o in fo)


async def test_diff_runs_signature_and_metric_shift():
    a = build_tables(
        [(c, (await diagnose(c))) for c in [_mk_ctx("a1", "chat", 1000, err="boom 7")]]
    )
    b = build_tables([(c, (await diagnose(c))) for c in [_mk_ctx("b1", "chat", 2000)]])
    d = diff_runs(a, b)
    assert any("boom" in s["signature"] for s in d["sig_removed"])  # 错误在 A 有、B 没了
    assert any(s["metric"] == "duration_ms" and s["ratio"] == 2.0 for s in d["metric_shifts"])


# —— §3.7 行为模式：tool_churn + corpus 出现率 ——

_TOOL = {"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "web_search"}


def _churn_spans(n_calls, *, tool_attrs=None, interleave=False):
    spans = {"p": _span("p", None, "invoke_agent x", 0, 60_000, {"gen_ai.agent.name": "x"})}
    t = 100
    for i in range(n_calls):
        spans[f"t{i}"] = _span(f"t{i}", "p", "execute_tool", t, 500, tool_attrs or _TOOL)
        t += 1000
        if interleave:  # 工具调用之间夹 model-call：think→call 循环是正常形态
            spans[f"m{i}"] = _span(f"m{i}", "p", "chat", t, 500, _MODEL_ATTRS)
            t += 1000
    return spans


async def test_tool_churn_consecutive():
    # 同父下同 tool 背靠背 ×3 → pattern:tool_churn（标在 run 的最后一个节点）
    ctx = assemble(_churn_spans(3), genai.specs())
    findings = await diagnose(ctx)
    churn = [f for fs in findings.values() for f in fs if f.source == "pattern:tool_churn"]
    assert len(churn) == 1
    assert "web_search 连续调用 3 次" in churn[0].note
    assert churn[0].node_id == "t2"  # run 末节点


async def test_tool_churn_interleaved_is_normal():
    # think→call→think→call 是正常 agent 循环：背靠背 run 被打断 → 不报
    ctx = assemble(_churn_spans(4, interleave=True), genai.specs())
    findings = await diagnose(ctx)
    assert not any(f.source == "pattern:tool_churn" for fs in findings.values() for f in fs)


async def test_tool_churn_below_threshold():
    ctx = assemble(_churn_spans(2), genai.specs())
    findings = await diagnose(ctx)
    assert not any(f.source == "pattern:tool_churn" for fs in findings.values() for f in fs)


async def test_pattern_rates_cross_trace():
    # 3 条 trace，2 条命中 churn → 出现率 66.7%；对象名来自 facts.tool
    from trace_harness.corpus import pattern_rates

    items = []
    for tid, n in (("t1", 3), ("t2", 4), ("t3", 1)):
        ctx = assemble(_churn_spans(n), genai.specs())
        ctx.trace_id = tid
        items.append((ctx, (await diagnose(ctx))))
    tables = build_tables(items)
    rates = pattern_rates(tables)
    assert len(rates) == 1
    r = rates[0]
    assert r["pattern"] == "pattern:tool_churn"
    assert r["name"] == "web_search"
    assert r["n_traces"] == 2
    assert r["trace_pct"] == 66.7


async def test_report_has_pattern_section():
    from harness_common.report_kit import render_html as _render

    from trace_harness.corpus.report import build_report

    ctx = assemble(_churn_spans(3), genai.specs())
    tables = build_tables([(ctx, (await diagnose(ctx)))])
    html = _render(build_report("x", tables))
    assert "行为模式" in html
    assert "web_search" in html


# —— step 2b：持久化 + 报告 + experiment runner ——


async def test_store_roundtrip_jsonl(tmp_path, ctx):
    # 环境无 pyarrow → 回退 jsonl；read 回来行数 + metric_cols 一致
    tables = build_tables([(ctx, (await diagnose(ctx)))])
    fmt = write_tables(tables, tmp_path / "run")
    assert fmt == "jsonl"
    assert (tmp_path / "run" / "facts.jsonl").exists()
    back = read_tables(tmp_path / "run")
    assert len(back.facts) == len(tables.facts)
    assert len(back.traces) == 1
    assert back.metric_cols == tables.metric_cols


async def test_store_parquet_when_available(tmp_path, ctx):
    pytest.importorskip("pyarrow")  # 装了 [trace-corpus] extra 才跑
    tables = build_tables([(ctx, (await diagnose(ctx)))])
    assert write_tables(tables, tmp_path / "run") == "parquet"
    assert (tmp_path / "run" / "facts.parquet").exists()
    assert len(read_tables(tmp_path / "run").facts) == len(tables.facts)


async def test_run_experiment_end_to_end(tmp_path):
    # 离线 jaeger_dir → 三表 + 报告
    jdir = tmp_path / "traces"
    jdir.mkdir()
    (jdir / "t1.jsonl").write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    exp = tmp_path / "exp.yaml"
    exp.write_text("name: smoke\nsource:\n  jaeger_dir: traces\n", encoding="utf-8")
    rd = await run_experiment(exp, runs_dir=tmp_path / "runs")
    assert (rd / "facts.jsonl").exists()
    assert (rd / "meta.json").exists()
    html = (rd / "report.html").read_text(encoding="utf-8")
    assert "corpus" in html  # 报告标题
    assert "错误签名" in html  # synth 节点的 error 进了签名 section
    # verdict 必产；没声明 gates → skipped（记录门原则：没验证过的 run 不读成 green）
    v = json.loads((rd / "verdict.json").read_text(encoding="utf-8"))
    assert v["harness"] == "trace"
    assert v["status"] == "skipped"
    assert "checks" not in v  # omit-empty：无 gate 即无 checks


# —— verdict：gates → checks[]（Finding 是发现不是判定，判定只来自显式 gates）——


async def test_gates_fail_on_error_findings(tmp_path):
    # synth：model-call span 与其 http(504) span 都 error → 1:1 后各自成 error node，共 2 个
    # error Finding（融合时曾是 1 个）→ max_error_findings: 0 的 gate 仍 fail
    jdir = tmp_path / "traces"
    jdir.mkdir()
    (jdir / "t1.jsonl").write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    exp = tmp_path / "exp.yaml"
    exp.write_text(
        "name: gated\nsource:\n  jaeger_dir: traces\ngates:\n  max_error_findings: 0\n",
        encoding="utf-8",
    )
    rd = await run_experiment(exp, runs_dir=tmp_path / "runs")
    v = json.loads((rd / "verdict.json").read_text(encoding="utf-8"))
    assert v["status"] == "fail"
    (check,) = v["checks"]
    assert check["metric"] == "error_findings"
    assert check["observed"] == 2.0
    assert "error_findings <= 0" in v["reason"]


async def test_gates_pass_and_skip_mix(ctx):
    # 宽松 error 门 pass；no_new_signatures 声明了却无 diff 基线 → 该 check skipped 而非 pass
    tables = build_tables([(ctx, (await diagnose(ctx)))])
    checks = evaluate_gates(
        tables, {"max_error_findings": 5, "no_new_signatures": True}, diff_result=None
    )
    by_name = {c.name: c.status for c in checks}
    assert by_name["error_findings <= 5"] == "pass"
    assert by_name["no_new_signatures"] == "skipped"


def test_load_jaeger_ui_export(tmp_path):
    # Jaeger UI 导出（单个多行 JSON，processID 间接引 service）→ 与 ES jsonl 同一归一形态
    import json as _json

    ui = {
        "data": [
            {
                "traceID": "t1",
                "processes": {"p1": {"serviceName": "planit-server"}},
                "spans": [
                    {
                        "traceID": "t1",
                        "spanID": "aaaa",
                        "operationName": "chat",
                        "processID": "p1",
                        "references": [],
                        "startTime": 1700000000000000,
                        "duration": 1000000,
                        "tags": [{"key": "gen_ai.operation.name", "value": "chat"}],
                        "logs": [],
                    }
                ],
            }
        ]
    }
    f = tmp_path / "ui.json"
    f.write_text(_json.dumps(ui, indent=2), encoding="utf-8")
    spans = load_jaeger_file(f)
    assert spans["aaaa"].service == "planit-server"
    assert spans["aaaa"].dur_ms == 1000.0
    assert spans["aaaa"].attrs["gen_ai.operation.name"] == "chat"


# —— step 3 gaps：残余聚合（service 组）+ per-metric 离群策略 ——


def test_residue_aggregates_into_service_groups():
    # agent 下两条未被认领的内部 span（同 service）→ 聚成一个 service 组节点，不再各自成节点
    spans = {
        "p": _span("p", None, "invoke_agent x", 0, 10_000, {"gen_ai.agent.name": "x"}),
        "r1": _span("r1", "p", "redis.get", 100, 5, {}, service="chat-server"),
        "r2": _span("r2", "p", "redis.set", 200, 7, {}, service="chat-server"),
    }
    ctx = assemble(spans, genai.specs())
    svc = [n for n in ctx.nodes if n.kind == "service"]
    assert len(svc) == 1
    (g,) = svc
    assert g.facts["count"] == 2
    assert g.facts["sum_ms"] == 12
    agent = next(n for n in ctx.nodes if n.kind == "agent")
    assert g.parent_node_id == agent.node_id
    # 组内 span 仍可经 view 索引回查
    assert set(g.span_ids) == {"r1", "r2"}


async def test_obs_hole_opt_out_per_kind():
    # 聚合容器 kind 声明 obs_hole=False → 同样的空洞不再报（误报教训）
    from trace_harness.model.spec import KindSpec, SpecSet

    agent_like = KindSpec(
        kind="agent",
        matches=lambda s: s.attr("gen_ai.agent.name") is not None,
        metrics={},
        obs_hole=False,
    )
    chat = KindSpec(
        kind="model-call",
        matches=lambda s: s.attr("gen_ai.operation.name") == "chat",
    )
    spans = {
        "p": _span("p", None, "invoke_agent x", 0, 10_000, {"gen_ai.agent.name": "x"}),
        "c": _span("c", "p", "chat", 0, 2_000, _MODEL_ATTRS),
    }
    findings = await diagnose(assemble(spans, SpecSet([agent_like, chat])))
    assert not any(f.source == "obs_hole" for fs in findings.values() for f in fs)


async def test_outlier_topn_strategy():
    # topn 策略：长尾分布上 ratio 标不出来（最大值 < 3× 中位），topn 永远标最大的 N 个
    from trace_harness.model.spec import KindSpec, SpecSet

    spec = KindSpec(
        kind="model-call",
        matches=lambda s: s.attr("gen_ai.operation.name") == "chat",
        build=lambda p, sats: {"out_bytes": p.num("x.bytes")},
        metrics={"out_bytes": lambda n: n.facts.get("out_bytes")},
        strategy={"out_bytes": "topn"},
    )
    spans = {
        f"m{i}": _span(
            f"m{i}",
            None,
            "chat",
            i * 10_000,
            1000,
            {"gen_ai.operation.name": "chat", "x.bytes": v},
        )
        for i, v in enumerate([100, 110, 120, 130, 250])  # 250 < 3×120：ratio 抓不到
    }
    ctx = assemble(spans, SpecSet([spec]))
    findings = await diagnose(ctx)
    tops = [f for fs in findings.values() for f in fs if f.source == "outlier:out_bytes"]
    assert len(tops) == 3  # top_n=3
    assert tops[0].rank == 0  # 最大者 rank 0
    by_rank0 = next(f for f in tops if f.rank == 0)
    assert by_rank0.node_id == "m4"


async def test_ratio_min_abs_floor_suppresses_ms_noise():
    # 5ms 是 1ms 的 5×，但低于 duration_ms 绝对下限（3s）→ 不报
    spans = {
        f"m{i}": _span(f"m{i}", None, "chat", i * 100, dur, _MODEL_ATTRS)
        for i, dur in enumerate([1, 1, 1, 5])
    }
    findings = await diagnose(assemble(spans, genai.specs()))
    assert not any(f.source == "outlier:duration_ms" for fs in findings.values() for f in fs)


async def test_gates_unknown_key_fails_fast(ctx):
    tables = build_tables([(ctx, (await diagnose(ctx)))])
    with pytest.raises(ValueError, match="unknown gate"):
        evaluate_gates(tables, {"max_eror_findings": 0})  # 拼错的 gate 不能静默不判
