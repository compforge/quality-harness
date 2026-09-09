"""时序视图 —— 某 (kind, metric) 跨迭代的 sparkline，配 trend 判读看渐变恶化。

把同 kind 节点按 name 分组、按开始时间排序，metric 值画成 unicode sparkline；和
diagnose.series 的 trend Finding 互补：一个给眼睛看曲线、一个给机器下结论。
"""

from __future__ import annotations

from collections import defaultdict

from trace_harness.model.context import TraceContext

_BARS = "▁▂▃▄▅▆▇█"


def _spark(vals: list[float]) -> str:
    lo, hi = min(vals), max(vals)
    if hi <= lo:
        return _BARS[0] * len(vals)
    span = hi - lo
    return "".join(
        _BARS[min(len(_BARS) - 1, int((v - lo) / span * (len(_BARS) - 1)))] for v in vals
    )


def render_series(ctx: TraceContext, kind: str, metric: str) -> str:
    spec = ctx.specs.get(kind)
    fn = (getattr(spec, "metrics", {}) or {}).get(metric)
    if fn is None:
        return f"(kind '{kind}' 无 metric '{metric}')"
    groups: dict[str, list] = defaultdict(list)
    for n in ctx.nodes:
        if n.kind == kind:
            groups[n.name].append(n)
    lines = [f"series {kind}:{metric}  trace {ctx.trace_id}"]
    for name, nodes in groups.items():
        ordered = sorted(nodes, key=lambda x: x.start_ms)
        vals = [v for v in (fn(x) for x in ordered) if v is not None]
        if not vals:
            continue
        lines.append(
            f"  {name}  [{_spark(vals)}]  n={len(vals)}  min={min(vals):.0f} max={max(vals):.0f}"
        )
    if len(lines) == 1:
        lines.append(f"  (无 {kind} 节点有 {metric} 值)")
    return "\n".join(lines)
