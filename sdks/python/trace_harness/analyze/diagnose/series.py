"""时序判读 —— 渐变恶化：循环型节点（同 (kind, name) 多次出现）按迭代序看 metric 是否整体走高。

outlier 抓"某一步突然离群"；trend 抓"每步都不离群、但整体在变坏"——loop agent 常见的慢性
退化（context 越滚越大、中间结果越积越多）。按 (kind, name) 分组，比**后段中位 vs 前段中位**，
比单点斜率稳，对个别噪点不敏感。Finding 落在最后一个（最差）节点上。
"""

from __future__ import annotations

from collections import defaultdict

from trace_harness.model.context import TraceContext
from trace_harness.model.node import Finding

_MIN_POINTS = 4  # 少于此数谈不上"趋势"
_TREND_RATIO = 2.0  # 后段中位 ≥ ratio × 前段中位 → 渐变恶化


def _median(xs: list[float]) -> float:
    xs = sorted(xs)
    n = len(xs)
    mid = n // 2
    return xs[mid] if n % 2 else (xs[mid - 1] + xs[mid]) / 2


def find_trends(
    ctx: TraceContext, min_points: int = _MIN_POINTS, ratio: float = _TREND_RATIO
) -> list[Finding]:
    out: list[Finding] = []
    groups: dict[tuple[str, str], list] = defaultdict(list)
    for n in ctx.nodes:
        groups[(n.kind, n.name)].append(n)
    for (kind, name), nodes in groups.items():
        if len(nodes) < min_points:
            continue
        spec = ctx.specs.get(kind)
        metrics = getattr(spec, "metrics", {}) or {}
        ordered = sorted(nodes, key=lambda x: x.start_ms)
        for mname, fn in metrics.items():
            vals = [v for v in (fn(x) for x in ordered) if v is not None]
            if len(vals) < min_points:
                continue
            k = max(1, len(vals) // 3)  # 前 1/3 vs 后 1/3
            first, last = _median(vals[:k]), _median(vals[-k:])
            if first > 0 and last / first >= ratio:
                worst = ordered[-1]
                out.append(
                    Finding(
                        worst.node_id,
                        f"trend:{mname}",
                        "warn",
                        note=f"{name} {mname} 渐变恶化 ×{last / first:.1f}（{len(vals)} 次）",
                    )
                )
    return out
