"""trace_time_budget / budget_distribution 单测。

合成树验：区间并集口径、叶子 tool 判定（复合 delegate 不计 tool）、gap 计算；
fixture 冒烟验：真实 trace 上 wall>0、fracs∈[0,1]、llm+tool≤wall。
"""

from __future__ import annotations

from pathlib import Path

from trace_harness.analyze.budget import (
    budget_distribution,
    trace_time_budget,
)
from trace_harness.ingest.load import build_context
from trace_harness.model.node import Node

FIXTURE = Path(__file__).parents[4] / "conformance" / "trace" / "fixtures" / "genai-basic.jsonl"


def _mk(kind, name, start, dur, nid, parent=None):
    return Node(
        kind=kind,
        name=name,
        primary_span_id=nid,
        span_ids=[nid],
        facts={},
        start_ms=float(start),
        duration_ms=float(dur),
        service=None,
        node_id=nid,
        parent_node_id=parent,
    )


def test_budget_synthetic_leaf_and_gap():
    # root agent[0,100]
    #  ├ model-call[0,30]                       (llm)
    #  ├ tool shell[30,50]                      (leaf tool)
    #  └ delegate_agent[50,100]                 (复合 tool，不计 tool 占用)
    #      ├ sub model-call[50,80]              (llm)
    #      └ sub tool shell[80,95]              (leaf tool)
    nodes = [
        _mk("agent", "root", 0, 100, "a"),
        _mk("model-call", "m1", 0, 30, "m1", "a"),
        _mk("tool-call", "shell", 30, 20, "t1", "a"),
        _mk("tool-call", "delegate_agent", 50, 50, "d", "a"),
        _mk("model-call", "m2", 50, 30, "m2", "d"),
        _mk("tool-call", "shell", 80, 15, "t2", "d"),
    ]
    b = trace_time_budget(nodes)
    assert b.wall_ms == 100
    assert b.llm_ms == 60  # [0,30] ∪ [50,80]
    assert b.tool_ms == 35  # shell [30,50] ∪ [80,95]，delegate 不计
    assert b.gap_ms == 5  # 100 − union(llm∪tool)=95
    assert b.n_llm == 2
    assert b.n_tool == 2  # 两个叶子 shell；delegate 复合被排除
    assert b.by_tool == {"shell": 35}
    assert abs(b.llm_frac - 0.6) < 1e-9
    assert abs(b.tool_frac_for(["shell"]) - 0.35) < 1e-9
    assert b.tool_frac_for(["nonexistent"]) == 0.0


def test_budget_empty():
    b = trace_time_budget([])
    assert b.wall_ms == 0 and b.llm_frac == 0.0 and b.to_dict()["by_tool"] == {}


def test_budget_on_fixture():
    ctx = build_context(FIXTURE)
    b = trace_time_budget(ctx.nodes)
    assert b.wall_ms > 0
    for f in (b.llm_frac, b.tool_frac, b.gap_frac):
        assert 0.0 <= f <= 1.0 + 1e-6
    # 占用不超过 wall-clock time（并集口径的基本自洽）
    assert b.llm_ms <= b.wall_ms + 1e-6
    assert b.tool_ms <= b.wall_ms + 1e-6
    d = b.to_dict()
    assert set(d) >= {"wall_ms", "llm_frac", "tool_frac", "gap_frac", "by_tool"}


def test_budget_distribution():
    b1 = trace_time_budget(
        [
            _mk("agent", "r", 0, 100, "a1"),
            _mk("model-call", "m", 0, 90, "m", "a1"),
            _mk("tool-call", "shell", 90, 10, "t", "a1"),
        ]
    )
    b2 = trace_time_budget(
        [
            _mk("agent", "r", 0, 100, "a2"),
            _mk("model-call", "m", 0, 50, "m", "a2"),
            _mk("tool-call", "shell", 50, 50, "t", "a2"),
        ]
    )
    dist = budget_distribution([b1, b2], tool_names=["shell"])
    assert dist["n_traces"] == 2
    # per-trace 均值 = (0.1 + 0.5)/2 = 0.3；sum/sum = (10+50)/(100+100) = 0.3（此例恰好相等）
    assert abs(dist["tool_frac"]["mean"] - 0.3) < 1e-9
    assert abs(dist["tool_frac"]["agg_sum_over_sum"] - 0.3) < 1e-9
    assert abs(dist["selected_tool_frac"]["agg_sum_over_sum"] - 0.3) < 1e-9
    assert dist["selected_tool_frac"]["tools"] == ["shell"]
