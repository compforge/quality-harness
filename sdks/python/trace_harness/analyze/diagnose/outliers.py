"""分布判读 —— 单 trace 内同类离群，per-metric 双策略（来自 spec.strategy）。

- **ratio**（默认）：对每个 (kind, metric) 建同类中位基线，标 ≥N× 中位的节点；时间类
  metric 另设绝对下限（避免把毫秒级波动当离群——5ms 是 1ms 的 5× 但没人关心）。
- **topn**：永远标最大的 N 个——适合 result_bytes/in_chars 这类"无自然基线、看最反常
  几个"的规模度量（ratio 在长尾分布上要么全标要么全不标）。

metric 与策略都来自 RuleFacet（spec.metrics / spec.strategy），不跨 kind 泄漏。需要同类
节点够多（min_peers）才判——单 trace 内一两个同类不足以判离群，那是 corpus 层
fleet_outlier 的活。
"""

from __future__ import annotations

from collections import defaultdict

from trace_harness.model.context import TraceContext
from trace_harness.model.node import Finding

_RATIO = 3.0  # ratio 策略：value ≥ ratio × 同类中位 → 离群
_MIN_PEERS = 3  # 同类节点少于此数不判（无基线）
_TOP_N = 3  # topn 策略：标最大的 N 个
# 时间类 metric 的绝对下限（ms）：低于它不算离群，压毫秒级噪声
_MIN_ABS = {"duration_ms": 3000.0}


def _median(xs: list[float]) -> float:
    xs = sorted(xs)
    n = len(xs)
    mid = n // 2
    return xs[mid] if n % 2 else (xs[mid - 1] + xs[mid]) / 2


def find_outliers(
    ctx: TraceContext,
    ratio: float = _RATIO,
    min_peers: int = _MIN_PEERS,
    top_n: int = _TOP_N,
) -> list[Finding]:
    out: list[Finding] = []
    by_kind: dict[str, list] = defaultdict(list)
    for n in ctx.nodes:
        by_kind[n.kind].append(n)
    for kind, nodes in by_kind.items():
        spec = ctx.specs.get(kind)
        metrics = getattr(spec, "metrics", {}) or {}
        strategies = getattr(spec, "strategy", {}) or {}
        for mname, fn in metrics.items():
            if strategies.get(mname, "ratio") == "topn":
                # topn：同 kind 取最大 N（本就区分性，不细分 name）
                pairs = [(n, fn(n)) for n in nodes]
                pairs = [(n, v) for n, v in pairs if v is not None]
                if len(pairs) < min_peers:
                    continue
                hot = sorted(pairs, key=lambda x: -x[1])[:top_n]
                for rank, (n, v) in enumerate(hot):
                    out.append(
                        Finding(
                            n.node_id,
                            f"outlier:{mname}",
                            "warn",
                            rank=rank,
                            note=f"{mname}={v:.0f}（top{rank + 1}/{len(pairs)}）",
                        )
                    )
                continue
            # ratio：**组内相对**——同 kind 再按 name 细分中位，避免同 kind 异 name 互相拉低基线
            # （否则"每个 CodePlannerNode 都比 node 全类中位慢"这类全类膨胀，信号失去区分性）。
            by_name: dict[str, list] = defaultdict(list)
            for n in nodes:
                by_name[n.name].append(n)
            for peers in by_name.values():
                pairs = [(n, fn(n)) for n in peers]
                pairs = [(n, v) for n, v in pairs if v is not None]
                if len(pairs) < min_peers:
                    continue
                med = _median([v for _, v in pairs])
                if med <= 0:
                    continue
                floor = _MIN_ABS.get(mname, 0.0)
                for n, v in pairs:
                    if v >= ratio * med and v >= floor:
                        out.append(
                            Finding(
                                n.node_id,
                                f"outlier:{mname}",
                                "warn",
                                note=f"{mname}={v:.0f} ≈ {v / med:.1f}× 同名中位({med:.0f})",
                            )
                        )
    return out
