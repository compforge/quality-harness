"""diagnose —— 单 trace 判读汇流：多类生产者 → Finding 流，按 node_id 分组返回。

两层：
- **base 生产者**（一次性收集）：errors（节点固有错误）/ per-kind rules（域知识跟 kind 走）/
  outlier（突变离群）/ trend（渐变恶化）/ pattern（行为模式，单条嫌疑、跨 trace 才成结论）。
- **scoped detector**：内置拓扑（detached/obs_hole/propagated，住
  `detectors.py`）+ domain detector，统一 `(node, analysis_context)` 签名，**后序**逐 node 跑、
  可读已产 findings 做归因。判读机制收敛到这一套，不再硬编码 detect()。

构建与判读分离：build_context 不跑这里；probe 是唯一有副作用的环节（落盘证据），
`probes=False` 默认关、显式开。各类都只产 Finding，render 统一上色——判读与展示不耦合。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Awaitable, Iterable
from dataclasses import asdict, replace
from types import MappingProxyType

from trace_harness.analyze.context import AnalysisContext
from trace_harness.analyze.diagnose.detectors import BUILTIN_DETECTORS
from trace_harness.analyze.diagnose.outliers import find_outliers
from trace_harness.analyze.diagnose.patterns import find_patterns
from trace_harness.analyze.diagnose.probes import probe
from trace_harness.analyze.diagnose.registry import DetectorRegistry, NodeDetector
from trace_harness.analyze.diagnose.series import find_trends
from trace_harness.analyze.measure import measure
from trace_harness.detectors import execute_detector
from trace_harness.model.context import TraceContext
from trace_harness.model.measurement import Measurements
from trace_harness.model.node import Finding, Node


def _error_findings(ctx: TraceContext) -> list[Finding]:
    return [
        Finding(n.node_id, "error", "error", note=ctx.error_text(n.error_anchor))
        for n in ctx.nodes
        if n.has_error
    ]


async def _rule_findings(analysis: AnalysisContext) -> list[Finding]:
    """per-kind rules：每个节点过其 kind 的 spec.rules（kind 绑定的便捷判读）。"""
    out: list[Finding] = []
    for n in analysis.trace.nodes:
        spec = analysis.trace.specs.get(n.kind)
        if spec is None:
            continue
        for rule in spec.rules:
            result = rule(n, analysis)
            out.extend((await result if isinstance(result, Awaitable) else result) or [])
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


async def diagnose_analysis(
    ctx: TraceContext,
    probes: bool = False,
    *,
    detector_registry: DetectorRegistry | None = None,
    measurements: Measurements | None = None,
    analysis: AnalysisContext | None = None,
) -> AnalysisContext:
    """执行 base 与注册 detector，返回观察和本次执行记录。"""
    analysis = analysis or AnalysisContext(
        ctx, measurements if measurements is not None else measure(ctx)
    )
    detector_registry = detector_registry or DetectorRegistry(BUILTIN_DETECTORS)
    plan = detector_registry.plan()
    base = (
        _error_findings(ctx)
        + await _rule_findings(analysis)
        + find_outliers(ctx)
        + find_trends(ctx)
        + find_patterns(ctx)
    )
    if probes:
        base += probe(ctx)
    return await run_node_detectors(analysis, plan, base)


async def run_node_detectors(
    analysis: AnalysisContext,
    plan: list[NodeDetector],
    base: Iterable[Finding] = (),
) -> AnalysisContext:
    """Post-order across nodes, dependency order within each node.

    Each node has a fresh completion map. A parent may read child findings through
    the accumulated view, but requires never imports a child's execution result.
    """
    found: dict[str, tuple[Finding, ...]] = defaultdict(tuple)
    for finding in base:
        found[finding.node_id] += (finding,)
    records = []
    for node in _post_order(analysis.trace):
        completed = {}
        for detector in plan:
            rows = []
            scoped = replace(
                analysis,
                findings=MappingProxyType(found),
                _detector=detector,
                _results=MappingProxyType(completed),
            )

            def emit(finding, rows=rows, detector=detector):
                rows.append({**asdict(finding), "detector_id": detector.id})
                found[finding.node_id] += (finding,)

            result = await execute_detector(
                detector, node, scoped, emit=emit, read=lambda rows=rows: iter(rows)
            )
            completed[detector.id] = result
            records.append(
                {
                    "id": detector.id,
                    "scope": "node",
                    "node_id": node.node_id,
                    "status": result.status,
                    "error": result.error,
                    "requires": list(detector.requires),
                }
            )
            if result.status == "failed":
                failure = Finding(
                    node.node_id,
                    "detector_execution_failed",
                    "warn",
                    note=f"Detector {detector.id}: {result.error['message']}",
                    data={"detector_id": detector.id, "error": result.error},
                )
                found[node.node_id] += (failure,)
    return replace(
        analysis,
        findings=MappingProxyType(found),
        detector_runs=tuple(records),
        _detector=None,
        _results={},
    )


async def diagnose(
    ctx: TraceContext,
    probes: bool = False,
    *,
    detector_registry: DetectorRegistry | None = None,
    measurements: Measurements | None = None,
    analysis: AnalysisContext | None = None,
) -> dict[str, list[Finding]]:
    result = await diagnose_analysis(
        ctx,
        probes,
        detector_registry=detector_registry,
        measurements=measurements,
        analysis=analysis,
    )
    return {key: list(value) for key, value in result.findings.items()}
