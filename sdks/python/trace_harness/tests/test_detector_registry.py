"""全局 detector 注册表 + Finding 的 symptoms/causes 字段回归。"""

from __future__ import annotations

from pathlib import Path

from trace_harness.analyze.diagnose import diagnose
from trace_harness.analyze.diagnose.detectors import BUILTIN_DETECTORS
from trace_harness.analyze.diagnose.registry import DetectorRegistry
from trace_harness.ingest.load import build_context
from trace_harness.model.node import Finding

FIXTURE = Path(__file__).parents[4] / "conformance" / "trace" / "fixtures" / "genai-basic.jsonl"


def test_finding_symptoms_causes_default_empty():
    f = Finding("n1", "error", "error")
    assert f.symptoms == () and f.causes == ()
    f2 = Finding("n1", "ux:sluggish", "warn", symptoms=("慢",), causes=("c1", "c2"))
    assert f2.symptoms == ("慢",) and f2.causes == ("c1", "c2")


async def test_registered_detector_runs_with_found_accumulator():
    saw_base = {"yes": False}

    def det(node, ctx):
        found = ctx.findings
        # found 里应已有 base findings（diagnose 先产 base 再跑注册 detector）
        if sum(len(v) for v in found.values()) > 0:
            saw_base["yes"] = True
        if node.kind == "agent":  # 自 gate：只在 agent 上产
            return [
                Finding(
                    node.node_id,
                    "test:symptom",
                    "warn",
                    scope="trace",
                    symptoms=("慢",),
                    causes=(node.node_id,),
                )
            ]
        return []

    registry = DetectorRegistry((*BUILTIN_DETECTORS, det))
    ctx = build_context(FIXTURE)
    grouped = await diagnose(ctx, detector_registry=registry)
    agent = next(n for n in ctx.nodes if n.kind == "agent")
    mine = [x for x in grouped.get(agent.node_id, []) if x.source == "test:symptom"]
    assert mine, "scoped detector 没产出 finding"
    assert mine[0].symptoms == ("慢",) and mine[0].causes == (agent.node_id,)
    assert saw_base["yes"], "found 累加器没把 base findings 传给 detector"
