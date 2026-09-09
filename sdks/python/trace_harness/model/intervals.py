"""区间工具：节点 wall-clock interval "覆盖"计算的单一实现。

`self_ms`（derive）与 `obs_hole`（detector）都要算
"子区间并集覆盖了多少 wall-clock time"——并行/重叠的子不能重复计。
两处曾各写一遍并集算法（口径漂移风险），统一到这里。clamp
（是否把子区间钳回父 wall-clock interval 内）是调用方各自的事：
self_ms 用原始区间，obs_hole 钳到父 [start, end) 内再传进来。
"""

from __future__ import annotations


def interval_union(intervals: list[tuple[float, float]]) -> float:
    """一组 [start, end) 区间的并集长度。重叠/并行不重复计（先并集再求长）。"""
    total = 0.0
    cur_s = cur_e = None
    for s, e in sorted(intervals):
        if cur_e is None or s > cur_e:
            if cur_e is not None:
                total += cur_e - cur_s
            cur_s, cur_e = s, e
        elif e > cur_e:
            cur_e = e
    if cur_e is not None:
        total += cur_e - cur_s
    return total
