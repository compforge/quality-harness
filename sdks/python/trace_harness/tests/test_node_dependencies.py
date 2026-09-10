"""Node and Dataset share dependency semantics while retaining distinct units."""

import json
from collections import Counter
from pathlib import Path

import pytest

from trace_harness import (
    Detector,
    Finding,
    dump_analysis,
    load_analysis,
)
from trace_harness.analyze.diagnose.registry import DetectorRegistry
from trace_harness.tests.test_detector_registry import FIXTURE
from trace_harness.tests.test_loading import MemorySource, harness

CONTRACT = Path(__file__).resolve().parents[4] / "conformance/trace/detector-dependencies.json"


@pytest.mark.parametrize("case", json.loads(CONTRACT.read_text()))
def test_shared_planning_contract(case):
    definitions = [
        Detector(d["id"], lambda n, c: [], tuple(d.get("requires", []))) for d in case["detectors"]
    ]
    registry = DetectorRegistry(definitions)
    if "error" in case:
        with pytest.raises((KeyError, ValueError), match=case["error"]):
            registry.plan(case["selected"])
    else:
        assert [d.id for d in registry.plan(case["selected"])] == case["order"]


async def test_node_result_scope_postorder_and_repeat_analysis(tmp_path):
    calls = []

    def leaf(node, context):
        calls.append((context.trace.trace_id, node.node_id, "leaf"))
        return [Finding(node.node_id, "leaf-rule", "info", data={"node": node.node_id})]

    async def summary(node, context):
        calls.append((context.trace.trace_id, node.node_id, "summary"))
        result = context.result("leaf")
        assert result.status == "succeeded"
        assert [r["data"]["node"] for r in result.findings()] == [node.node_id]
        for child in context.trace.view().children(node):
            assert any(f.source == "summary-rule" for f in context.findings[child.node_id])
        return [Finding(node.node_id, "summary-rule", "info")]

    configured = harness(
        detectors=(Detector("summary", summary, ("leaf",)), Detector("leaf", leaf))
    )
    trace = configured.build_context(FIXTURE)
    # Both complete snapshots and explicit lazy selection use the same executor.
    full = await configured.analyze(trace)
    count = len(trace.nodes)
    assert len(calls) == count * 2
    assert len({(tid, node) for tid, node, _ in calls}) == count
    assert len([r for r in full.detector_runs if r["id"] in {"leaf", "summary"}]) == count * 2
    artifact = dump_analysis(full, tmp_path / "analysis.json")
    assert load_analysis(artifact).detector_runs == full.detector_runs
    with pytest.raises(RuntimeError, match="during detector"):
        full.result("leaf")
    source = MemorySource(2)  # Same node id in two traces must not share results.
    async with configured.open(source, work_dir=tmp_path / "work") as session:
        dataset = await session.select()
        for _ in range(2):
            run = await session.detect(
                dataset, detectors=["summary", "leaf", "summary"], metrics=[], batch_detectors=[]
            )
            assert run.summary["detector_failures"] == 0
            assert len(list(run.rows("detector_runs"))) == 4
        assert Counter(calls)[("t0", "s", "leaf")] == 2
        assert Counter(calls)[("t1", "s", "leaf")] == 2


async def test_node_failure_is_local_and_batch_result_is_partial(tmp_path):
    def broken(node, context):
        yield Finding(node.node_id, "partial", "info")
        raise RuntimeError("node evidence unavailable")

    def summary(node, context):
        result = context.result("broken")
        assert result.status == "failed"
        assert len(list(result.findings())) == 1
        assert context.result("empty").status == "succeeded"
        assert list(context.result("empty").findings()) == []
        return [Finding(node.node_id, "partial-summary", "info", data={"complete": False})]

    def unauthorized(node, context):
        context.result("empty")

    configured = harness(
        detectors=(
            Detector("summary", summary, ("broken", "empty")),
            Detector("broken", broken),
            Detector("empty", lambda n, c: []),
            Detector("unauthorized", unauthorized),
        )
    )
    async with configured.open(MemorySource(2), work_dir=tmp_path) as session:
        dataset = await session.select()
        run = await session.detect(
            dataset, detectors=["summary", "unauthorized"], metrics=[], batch_detectors=[]
        )
        assert run.summary["coverage"]["succeeded"] == 2
        assert run.summary["detector_failures"] == 4
        executions = list(run.rows("detector_runs"))
        assert sum(r["id"] == "summary" and r["status"] == "succeeded" for r in executions) == 2
        assert json.loads((run.path / "manifest.json").read_text())["status"] == "partial"
        assert "node evidence unavailable" in (run.path / "report.html").read_text()


async def test_node_cannot_require_dataset_detector_before_fetch(tmp_path):
    source = MemorySource()
    configured = harness(
        detectors=(Detector("node", lambda n, c: [], ("dataset-only",)),),
        batch_detectors=(Detector("dataset-only", lambda d, c: []),),
    )
    async with configured.open(source, work_dir=tmp_path) as session:
        dataset = await session.select()
        with pytest.raises(KeyError, match="dataset-only"):
            await session.detect(dataset, detectors=["node"], metrics=[])
        assert source.fetches == source.reads == 0
