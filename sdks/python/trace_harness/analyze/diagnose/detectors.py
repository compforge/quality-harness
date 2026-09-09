"""内置通用拓扑 detector —— 纯结构判读，每个 TraceHarness 独立持有。

- detached：节点的 primary span 的父 span 不在本 trace —— 跨服务断链 / 采样丢失。
- obs_hole：有子节点的容器节点，wall-clock time 里未被子节点覆盖的空隙 —— 疑似未埋点的耗时。
- propagated：错误沿父链向上重复记的副本（源头在更深 node）—— 标出副本、留源头那一跳。

都不依赖业务知识（纯 Node 字段 + 父子边），故作为内置 contribution；
domain detector 与它们走同一套机制。
"""

from __future__ import annotations

from trace_harness.model.context import TraceContext
from trace_harness.model.intervals import interval_union
from trace_harness.model.node import Finding, Node

# obs_hole 阈值：空隙既要够大（绝对）又要占父节点够大比例（相对），双门槛压误报。
_HOLE_MIN_GAP_MS = 1000.0
_HOLE_MIN_FRAC = 0.2


def _error_sig(ctx: TraceContext, n: Node) -> str:
    """节点错误签名：异常类型优先（error.type），否则错误文案前缀。"""
    s = ctx.spans.get(n.error_anchor)
    if s and s.error_events:
        t = s.error_events[0].get("type")
        if t:
            return str(t)
    return ctx.error_text(n.error_anchor)[:60]


def detached(node: Node, ctx: TraceContext, found: dict) -> list[Finding]:
    psid = ctx.spans[node.primary_span_id].parent_span_id
    if psid and psid not in ctx.spans:
        return [
            Finding(
                node.node_id,
                "detached",
                "warn",
                note=f"父 span {psid[:8]}… 不在本 trace（跨服务断链 / 采样丢失）",
            )
        ]
    return []


def obs_hole(node: Node, ctx: TraceContext, found: dict) -> list[Finding]:
    spec = ctx.specs.get(node.kind)
    if spec is not None and not spec.obs_hole:
        return []  # 聚合容器 kind 自行声明退出（误报教训见 spec.obs_hole 注释）
    kids = ctx.view().children(node)
    if not kids:
        return []  # 叶子在干活、不是编排，整段都不是"洞"
    lo, hi = node.start_ms, node.end_ms
    span = hi - lo
    if span <= 0:
        return []
    # 子区间钳到父节点 wall-clock interval 内（异步子节点可能越界），并集求覆盖，剩下即空隙
    clamped = [(max(lo, k.start_ms), min(hi, k.end_ms)) for k in kids]
    clamped = [(s, e) for s, e in clamped if e > s]
    gap = span - interval_union(clamped)
    if gap >= _HOLE_MIN_GAP_MS and gap >= _HOLE_MIN_FRAC * span:
        return [
            Finding(
                node.node_id,
                "obs_hole",
                "info",
                note=f"{gap:.0f}ms 未被子节点覆盖（疑似未埋点的耗时）",
            )
        ]
    return []


def propagated(node: Node, ctx: TraceContext, found: dict) -> list[Finding]:
    """本节点若有 error 且子树里有同签名 error 后代 → 它是传播副本（源头在更深 node）。
    naive 计数会把一份错误沿 model-call→node→agent 重复算 N 倍，标出副本、留最深源头那一跳。"""
    if not node.has_error:
        return []
    sig = _error_sig(ctx, node)
    view = ctx.view()
    stack = list(view.children(node))
    while stack:
        d = stack.pop()
        if d.has_error and _error_sig(ctx, d) == sig:
            return [
                Finding(
                    node.node_id,
                    "propagated",
                    "info",
                    note=f"错误传播副本（源头在更深 node；sig={sig}）",
                )
            ]
        stack.extend(view.children(d))
    return []


BUILTIN_DETECTORS = (detached, obs_hole, propagated)
