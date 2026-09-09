"""Trace 时间预算 —— 把一条 trace 的 wall-clock time 按 kind 拆成"占用"
（interval-union，非裸 sum）。

动机：分析"agent 在 LLM / 工具 / 其它上各花多少 wall-clock time"
（如 sandbox 占空比、多路复用潜力）时，
裸 `sum(span.duration)` 会把嵌套/并行 span 重复计——尤其 `delegate_agent` 这类**复合工具**
（其 span 区间包住子 agent 的 model-call/tool-call）。正确口径是**区间并集占用**：

    wall = 全 trace 时间跨度（min start → max end）
    llm  = 所有 model-call 区间并集
    tool = 所有**叶子** tool-call 区间并集（叶子 = 子孙无 model-call/tool-call 的真实执行；
           复合编排如 delegate_agent 不计为 tool 占用，其时间由子调用的 llm/tool 分解承担）
    gap  = wall − (llm ∪ tool)   —— 既不在模型也不在工具的 wall-clock time：
           node 编排 / 排队 / 流式回传

零域知识（只认 genai 通用 kind）。域层（如 trace-as 的 sandbox 归因）拿 `by_tool` 自己按
工具名求和即可——见 `tool_frac_for`。

与 `feature/builtins.py::self_ms`（单 node 减子并集）同源复用 `interval_union`，口径不漂移。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

from trace_harness.model.intervals import interval_union
from trace_harness.model.node import Node

# 参与"活跃占用"的叶子 kind；复合编排（含这些子孙的 tool-call）被判为非叶、不计 tool 占用。
_ACTIVITY_KINDS = ("model-call", "tool-call")


@dataclass
class TimeBudget:
    """一条 trace 的 wall-clock time budget（毫秒），及按工具名的占用明细。"""

    wall_ms: float
    llm_ms: float
    tool_ms: float
    gap_ms: float
    by_tool: dict[str, float] = field(default_factory=dict)  # 工具名 → 叶子占用并集(ms)
    n_llm: int = 0  # model-call 次数
    n_tool: int = 0  # 叶子 tool-call 次数

    def _frac(self, ms: float) -> float:
        return ms / self.wall_ms if self.wall_ms > 0 else 0.0

    @property
    def llm_frac(self) -> float:
        return self._frac(self.llm_ms)

    @property
    def tool_frac(self) -> float:
        return self._frac(self.tool_ms)

    @property
    def gap_frac(self) -> float:
        return self._frac(self.gap_ms)

    def tool_frac_for(self, names: Iterable[str]) -> float:
        """指定工具名子集的占用 / wall —— 域层算 sandbox 占空比的入口（传 sandbox 工具名）。"""
        names = set(names)
        return self._frac(sum(v for k, v in self.by_tool.items() if k in names))

    def to_dict(self) -> dict:
        return {
            "wall_ms": round(self.wall_ms, 1),
            "llm_ms": round(self.llm_ms, 1),
            "tool_ms": round(self.tool_ms, 1),
            "gap_ms": round(self.gap_ms, 1),
            "llm_frac": round(self.llm_frac, 4),
            "tool_frac": round(self.tool_frac, 4),
            "gap_frac": round(self.gap_frac, 4),
            "n_llm": self.n_llm,
            "n_tool": self.n_tool,
            "by_tool": {
                k: round(v, 1) for k, v in sorted(self.by_tool.items(), key=lambda kv: -kv[1])
            },
        }


def _leaf_tool_nodes(nodes: list[Node]) -> list[Node]:
    """叶子 tool-call = 子孙里没有 model-call/tool-call 的 tool-call node（真实执行）。

    复合编排（如 delegate_agent，其子树含子 agent 的 model/tool 调用）被排除——
    其 wall-clock time 由子调用的 llm/tool 占用去分解，若把它整段计为 tool
    会把子 agent 的 LLM 时间也错算成工具时间。
    """
    children: dict[str, list[Node]] = defaultdict(list)
    for n in nodes:
        if n.parent_node_id:
            children[n.parent_node_id].append(n)

    def has_activity_descendant(n: Node) -> bool:
        stack = list(children.get(n.node_id, []))
        while stack:
            c = stack.pop()
            if c.kind in _ACTIVITY_KINDS:
                return True
            stack.extend(children.get(c.node_id, []))
        return False

    return [n for n in nodes if n.kind == "tool-call" and not has_activity_descendant(n)]


def trace_time_budget(nodes: list[Node]) -> TimeBudget:
    """把 node 平集拆成 {wall, llm, tool, gap, by_tool} 时间预算（区间并集口径）。"""
    if not nodes:
        return TimeBudget(0.0, 0.0, 0.0, 0.0)

    wall = max(0.0, max(n.end_ms for n in nodes) - min(n.start_ms for n in nodes))

    llm_nodes = [n for n in nodes if n.kind == "model-call"]
    tool_nodes = _leaf_tool_nodes(nodes)

    llm_ms = interval_union([(n.start_ms, n.end_ms) for n in llm_nodes])
    tool_ms = interval_union([(n.start_ms, n.end_ms) for n in tool_nodes])
    busy = interval_union([(n.start_ms, n.end_ms) for n in (llm_nodes + tool_nodes)])
    gap = max(0.0, wall - busy)

    per_name: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for n in tool_nodes:
        per_name[n.name].append((n.start_ms, n.end_ms))
    by_tool = {name: interval_union(ivs) for name, ivs in per_name.items()}

    return TimeBudget(
        wall_ms=wall,
        llm_ms=llm_ms,
        tool_ms=tool_ms,
        gap_ms=gap,
        by_tool=by_tool,
        n_llm=len(llm_nodes),
        n_tool=len(tool_nodes),
    )


def _percentiles(values: list[float], qs=(0.1, 0.5, 0.9)) -> dict[str, float]:
    if not values:
        return {f"p{int(q * 100)}": 0.0 for q in qs}
    s = sorted(values)
    out = {}
    for q in qs:
        i = min(len(s) - 1, int(q * len(s)))
        out[f"p{int(q * 100)}"] = s[i]
    return out


def budget_distribution(budgets: list[TimeBudget], tool_names: Iterable[str] | None = None) -> dict:
    """跨多条 trace 的时间预算分布。

    同时给两种口径（两者常差很大、各答不同问题）：
      - **per-trace 分位**（mean/p10/p50/p90 of 各 trace fraction）：典型 trace 长什么样；
      - **sum/sum 聚合**（Σ占用 / Σwall）：容量口径——决定平均要几个并发槽（利用率恒等式）。

    `tool_names` 给定时，额外算这批工具（如 sandbox 工具集）的占空比分布/聚合。
    """
    n = len(budgets)
    tool_fracs = [b.tool_frac for b in budgets]
    llm_fracs = [b.llm_frac for b in budgets]
    sum_wall = sum(b.wall_ms for b in budgets)
    sum_tool = sum(b.tool_ms for b in budgets)
    sum_llm = sum(b.llm_ms for b in budgets)

    out = {
        "n_traces": n,
        "wall_ms_p50": _percentiles([b.wall_ms for b in budgets]).get("p50", 0.0),
        "tool_frac": {
            "mean": (sum(tool_fracs) / n if n else 0.0),
            **_percentiles(tool_fracs),
            "agg_sum_over_sum": (sum_tool / sum_wall if sum_wall else 0.0),
        },
        "llm_frac": {
            "mean": (sum(llm_fracs) / n if n else 0.0),
            **_percentiles(llm_fracs),
            "agg_sum_over_sum": (sum_llm / sum_wall if sum_wall else 0.0),
        },
    }
    if tool_names is not None:
        names = set(tool_names)
        sel_fracs = [b.tool_frac_for(names) for b in budgets]
        sum_sel = sum(sum(v for k, v in b.by_tool.items() if k in names) for b in budgets)
        out["selected_tool_frac"] = {
            "tools": sorted(names),
            "mean": (sum(sel_fracs) / n if n else 0.0),
            **_percentiles(sel_fracs),
            "agg_sum_over_sum": (sum_sel / sum_wall if sum_wall else 0.0),
        }
    return out
