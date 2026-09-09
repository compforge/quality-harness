"""diagnose —— 单 trace 判读汇流：多类生产者 → Finding 流，按 node_id 分组返回。

两层：
- **base 生产者**（一次性收集）：errors（节点固有错误）/ per-kind rules（域知识跟 kind 走）/
  outlier（突变离群）/ trend（渐变恶化）/ pattern（行为模式，单条嫌疑、跨 trace 才成结论）。
- **scoped detector**：内置拓扑（detached/obs_hole/propagated，住
  `detectors.py`）+ domain detector，统一 `(node, ctx, found)` 签名，**后序**逐 node 跑、
  可读已产 findings 做归因。判读机制收敛到这一套，不再硬编码 detect()。

构建与判读分离：build_context 不跑这里；probe 是唯一有副作用的环节（落盘证据），
`probes=False` 默认关、显式开。各类都只产 Finding，render 统一上色——判读与展示不耦合。
"""

from __future__ import annotations

from collections import defaultdict

from trace_harness.analyze.diagnose.detectors import BUILTIN_DETECTORS
from trace_harness.analyze.diagnose.outliers import find_outliers
from trace_harness.analyze.diagnose.patterns import find_patterns
from trace_harness.analyze.diagnose.probes import probe
from trace_harness.analyze.diagnose.registry import DetectorRegistry
from trace_harness.analyze.diagnose.series import find_trends
from trace_harness.model.context import TraceContext
from trace_harness.model.node import Finding, Node


def _error_findings(ctx: TraceContext) -> list[Finding]:
    return [
        Finding(n.node_id, "error", "error", note=ctx.error_text(n.error_anchor))
        for n in ctx.nodes
        if n.has_error
    ]


def _rule_findings(ctx: TraceContext) -> list[Finding]:
    """per-kind rules：每个节点过其 kind 的 spec.rules（kind 绑定的便捷判读）。"""
    out: list[Finding] = []
    for n in ctx.nodes:
        spec = ctx.specs.get(n.kind)
        if spec is None:
            continue
        for rule in spec.rules:
            out.extend(rule(n, ctx) or [])
    return out


def _post_order(ctx: TraceContext) -> list[Node]:
    """后序节点序（子先于父），供注册 detector 归因：跑到某 node 时其子树 findings 已就绪。"""
    view = ctx.view()
    out: list[Node] = []
    seen: set[str] = set()

    def visit(n: Node) -> None:
        if n.node_id in seen:
            return
        seen.add(n.node_id)
        for c in view.children(n):
            visit(c)
        out.append(n)

    for r in view.roots:
        visit(r)
    return out


def diagnose(
    ctx: TraceContext,
    probes: bool = False,
    *,
    detector_registry: DetectorRegistry | None = None,
) -> dict[str, list[Finding]]:
    """跑 base 判读 + 注册的全局 detector（含内置拓扑），返回 {node_id: [Finding]}。"""
    base = (
        _error_findings(ctx)
        + _rule_findings(ctx)
        + find_outliers(ctx)
        + find_trends(ctx)
        + find_patterns(ctx)
    )
    if probes:
        base += probe(ctx)
    found: dict[str, list[Finding]] = defaultdict(list)
    for m in base:
        found[m.node_id].append(m)
    # 注册的全局 detector：后序遍历，每个 node 过一遍，可读 found（子树已产 findings）做归因
    detector_registry = detector_registry or DetectorRegistry(BUILTIN_DETECTORS)
    dets = detector_registry.registered()
    if dets:
        for n in _post_order(ctx):
            for det in dets:
                for f in det(n, ctx, found) or []:
                    found[f.node_id].append(f)
    return dict(found)
