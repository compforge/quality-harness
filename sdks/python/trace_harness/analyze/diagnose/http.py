"""通用 HTTP 慢请求和连续串行同 API 调用判读。"""

from collections import defaultdict

from trace_harness.analyze.context import AnalysisContext
from trace_harness.kinds.http import HttpRequest, http_requests
from trace_harness.model.node import Finding, Node

_SLOW_HTTP_MS = 200
_MAX_FINDINGS = 10


def _serial_runs(requests: list[HttpRequest]) -> list[list[HttpRequest]]:
    groups: dict[tuple[str, str], list[HttpRequest]] = defaultdict(list)
    for request in requests:
        if request.span.parent_span_id:
            groups[(request.span.service, request.span.parent_span_id)].append(request)
    runs = []
    for siblings in groups.values():
        run = []
        busy_until = float("-inf")
        for request in sorted(siblings, key=lambda item: (item.start_ms, item.span.span_id)):
            serial = request.start_ms >= busy_until
            if request.ordinary and serial and run and request.api == run[-1].api:
                run.append(request)
            else:
                if len(run) >= 2:
                    runs.append(run)
                run = [request] if request.ordinary and serial else []
            # 并发区间里的后续短调用也不能被误认成一段串行序列。
            busy_until = max(busy_until, request.end_ms)
        if len(run) >= 2:
            runs.append(run)
    return runs


async def http_request_patterns(node: Node, analysis: AnalysisContext) -> list[Finding]:
    ctx = analysis.trace
    """同一 HTTP 调用 client/server 去重；两个独立 warn 各保留耗时最高的 10 条。"""
    if (
        not ctx.nodes
        or node.node_id != min(ctx.nodes, key=lambda item: (item.start_ms, item.node_id)).node_id
    ):
        return []
    await analysis.fact(node, "http_evidence")
    requests = http_requests(ctx)
    view = ctx.view()
    findings = []
    slow = sorted(
        (item for item in requests if item.ordinary and item.duration_ms > _SLOW_HTTP_MS),
        key=lambda item: (-item.duration_ms, item.span.span_id),
    )
    for request in slow[: analysis.finding_limit]:
        owner = next(
            (view.by_span[item.span_id] for item in request.spans if item.span_id in view.by_span),
            node,
        )
        findings.append(
            Finding(
                owner.node_id,
                "http_slow_request",
                "warn",
                symptoms=("慢",),
                note=(
                    f"普通 HTTP 请求 {request.label} "
                    f"耗时 {request.duration_ms:.1f}ms > {_SLOW_HTTP_MS}ms；"
                    f"检查 span {request.span.span_id} 的下游调用与请求内空档"
                ),
                data={
                    "method": request.api[0],
                    "target": request.api[1],
                    "route": request.api[2],
                    "duration_ms": request.duration_ms,
                    "threshold_ms": _SLOW_HTTP_MS,
                    "span_ids": [span.span_id for span in request.spans],
                },
            )
        )
    runs = sorted(_serial_runs(requests), key=lambda run: -(run[-1].end_ms - run[0].start_ms))
    for run in runs[: analysis.finding_limit]:
        first, last = run[0], run[-1]
        wall_ms = last.end_ms - first.start_ms
        gap_ms = sum(
            right.start_ms - left.end_ms for left, right in zip(run, run[1:], strict=False)
        )
        total_ms = sum(item.span.dur_ms for item in run)
        owner = (
            view.by_span.get(first.span.parent_span_id)
            or view.by_span.get(first.span.span_id)
            or node
        )
        findings.append(
            Finding(
                owner.node_id,
                "http_serial_same_api",
                "warn",
                symptoms=("慢",),
                note=(
                    f"{first.span.service or '?'} 连续串行调用 {first.label} {len(run)} 次，"
                    f"wall-clock {wall_ms:.1f}ms（请求累计 {total_ms:.1f}ms，"
                    f"间隔 {gap_ms:.1f}ms）；"
                    "检查批量接口、请求内结果复用与 client 生命周期；同一 API 不代表参数相同；"
                    f"检查父 span {first.span.parent_span_id} 的调用上下文"
                ),
                data={
                    "method": first.api[0],
                    "target": first.api[1],
                    "route": first.api[2],
                    "count": len(run),
                    "wall_ms": wall_ms,
                    "gap_ms": gap_ms,
                    "http_total_ms": total_ms,
                    "parent_span_id": first.span.parent_span_id,
                    "span_ids": [item.span.span_id for item in run],
                },
            )
        )
    for source, total in (
        ("http_slow_request", len(slow)),
        ("http_serial_same_api", len(runs)),
    ):
        if total > _MAX_FINDINGS:
            first = next(item for item in findings if item.source == source)
            first.note += f"（共 {total} 条，仅展示耗时最高的 {_MAX_FINDINGS} 条）"
    return findings
