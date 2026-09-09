"""三表构建：N 个 (TraceContext, findings) → traces / facts / findings 扁平行。

- traces：一行一 trace（摘要：节点数 / 错误数 / wall-clock span）
- facts：一行一 node（骨架列 + KindSpec.build 抽出的命名 facts 列，业务字段已隔离在列名内）
- findings：一行一 Finding（判读输出，挂 node；trace 级结论由 traces 表/报告承载）

`metric_cols` 是"哪些 facts 列可做基线"的单一真相——来自各 ctx 的 spec.metrics（与 1b 单 trace
outlier 同口径），fleet_outlier 只对这些列建基线，http_status 这类非 metric 数值列不会被误当离群。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from trace_harness.model.context import TraceContext
from trace_harness.model.node import Finding


@dataclass
class CorpusTables:
    traces: list[dict] = field(default_factory=list)
    facts: list[dict] = field(default_factory=list)
    findings: list[dict] = field(default_factory=list)
    metric_cols: set[str] = field(default_factory=set)  # 可建基线的 facts 列（来自 spec.metrics）


def _trace_row(ctx: TraceContext, findings: dict[str, list[Finding]]) -> dict:
    starts = [n.start_ms for n in ctx.nodes]
    ends = [n.end_ms for n in ctx.nodes]
    span = (max(ends) - min(starts)) if ctx.nodes else 0.0
    return {
        "trace_id": ctx.trace_id,
        "n_nodes": len(ctx.nodes),
        "n_errors": sum(1 for n in ctx.nodes if n.has_error),
        "n_findings": sum(len(v) for v in findings.values()),
        "duration_ms": round(span, 3),
    }


def _fact_rows(ctx: TraceContext) -> Iterable[dict]:
    for n in ctx.nodes:
        row = {
            "trace_id": ctx.trace_id,
            "node_id": n.node_id,
            "parent_node_id": n.parent_node_id,
            "kind": n.kind,
            "name": n.name,
            "service": n.service,
            "start_ms": n.start_ms,
            "has_error": n.has_error,
        }
        row.update(n.facts)  # 命名 facts 列（duration_ms/in_tokens/http_status/…）
        yield row


def _finding_rows(ctx: TraceContext, findings: dict[str, list[Finding]]) -> Iterable[dict]:
    for node_findings in findings.values():
        for m in node_findings:
            yield {
                "trace_id": ctx.trace_id,
                "scope": m.scope,  # node | trace | cohort
                "ref": m.ref,
                "node_id": m.node_id,  # 向后兼容（pattern_rates join facts 用）
                "source": m.source,
                "severity": m.severity,
                "note": m.note,
            }


def build_tables(items: Iterable[tuple[TraceContext, dict[str, list[Finding]]]]) -> CorpusTables:
    """吃 (ctx, diagnose 产出的 findings) 对的可迭代物 → 三表。probes 等决策在调用方。"""
    t = CorpusTables()
    for ctx, findings in items:
        t.traces.append(_trace_row(ctx, findings))
        t.facts.extend(_fact_rows(ctx))
        t.findings.extend(_finding_rows(ctx, findings))
        for spec in ctx.specs.values():
            t.metric_cols.update(getattr(spec, "metrics", {}) or {})
    return t
