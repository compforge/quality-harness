"""feature 层回归：Feature + pull+memo 引擎（按需算、缓存、依赖自解，无 order/bottom-up）。"""

from __future__ import annotations

from pathlib import Path

from trace_harness.feature import Feature, bake_features, lazy_features
from trace_harness.feature.builtins import BUILTIN_FEATURES
from trace_harness.feature.registry import FeatureRegistry
from trace_harness.ingest.load import build_context
from trace_harness.model.intervals import interval_union
from trace_harness.model.node import Node
from trace_harness.model.viewtree import build_view

FIXTURE = Path(__file__).parents[4] / "conformance" / "trace" / "fixtures" / "genai-basic.jsonl"


def _node(nid: str, start: float, dur: float, parent: str | None = None) -> Node:
    return Node(
        kind="x",
        name=nid,
        primary_span_id=nid,
        span_ids=[nid],
        facts={},
        start_ms=start,
        duration_ms=dur,
        service=None,
        node_id=nid,
        parent_node_id=parent,
    )


def test_interval_union():
    assert interval_union([(0, 10), (5, 15)]) == 15  # 重叠 → [0,15)
    assert interval_union([(0, 10), (20, 30)]) == 20  # 不相接 → 10+10
    assert interval_union([(0, 10), (2, 5)]) == 10  # 包含 → 10
    assert interval_union([(0, 30), (20, 50)]) == 50  # 部分重叠 → [0,50)


def test_self_ms_via_bake():
    # 父 self_ms = dur − 子并集；叶子不写
    nodes = [_node("p", 0, 100), _node("a", 0, 30, "p"), _node("b", 20, 30, "p")]
    bake_features(nodes)
    assert nodes[0].facts["self_ms"] == 50.0  # 子并集 [0,50)=50 → 100−50
    assert "self_ms" not in nodes[1].facts  # 叶子不写


def test_self_ms_on_fixture():
    ctx = build_context(FIXTURE)
    withkids = [n for n in ctx.nodes if "self_ms" in n.facts]
    assert withkids  # 有子的 node 带 self_ms
    for n in withkids:
        assert 0.0 <= n.facts["self_ms"] <= n.duration_ms


def test_pull_memo_resolves_dependency():
    # B 依赖 A：bake 时 pull 自解，不靠注册/遍历顺序；memo 保证 A 只算一次
    calls = {"a": 0}

    def fa(node: Node, ctx: object) -> dict:
        calls["a"] += 1
        return {"a": node.duration_ms * 2}

    def fb(node: Node, ctx) -> dict:
        return {"b": ctx.get(node, "a") + 1}  # B 拉 A

    registry = FeatureRegistry(
        (
            *BUILTIN_FEATURES,
            Feature(("b",), lambda n: True, fb),  # 故意先放依赖方 B
            Feature(("a",), lambda n: True, fa),
        )
    )
    nodes = [_node("n", 0, 10)]
    bake_features(nodes, registry=registry)
    assert nodes[0].facts["a"] == 20 and nodes[0].facts["b"] == 21  # 依赖解对
    assert calls["a"] == 1  # memo：A 只算一次（B 拉一次 + bake 再拉 → 命中缓存）


def test_lazy_feature_not_baked_but_on_demand():
    # bake=False 的不进 facts，经 lazy_features 现取
    registry = FeatureRegistry(
        (
            *BUILTIN_FEATURES,
            Feature(
                ("greeting",),
                lambda n: True,
                lambda node, ctx: {"greeting": "hi"},
                bake=False,
            ),
        )
    )
    nodes = [_node("n", 0, 10)]
    bake_features(nodes, registry=registry)
    assert "greeting" not in nodes[0].facts  # lazy 不烤
    out = lazy_features(nodes[0], build_view(nodes), registry=registry)
    assert out["greeting"] == "hi"
