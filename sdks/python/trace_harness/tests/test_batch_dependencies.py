"""Executable composition contract; identity is independent of handler names."""

import json
from pathlib import Path

import pytest

from trace_harness import BatchDetector, Finding
from trace_harness.batch_detectors import plan_detectors
from trace_harness.tests.test_loading import MemorySource, harness

CONTRACT = Path(__file__).resolve().parents[4] / "conformance/trace/detector-dependencies.json"


@pytest.mark.parametrize("case", json.loads(CONTRACT.read_text()))
def test_plan_contract(case):
    definitions = [
        BatchDetector(d["id"], lambda d, c: [], tuple(d.get("requires", [])))
        for d in case["detectors"]
    ]
    if "error" in case:
        with pytest.raises((ValueError, KeyError), match=case["error"]):
            plan_detectors(definitions, case["selected"])
    else:
        assert [d.id for d in plan_detectors(definitions, case["selected"])] == case["order"]


async def test_shared_dependency_results_and_new_run(tmp_path):
    calls = []

    async def leaf(ds, ctx):
        calls.append("leaf")
        async for analysis in ctx.trees(ds):
            size = await analysis.fact(analysis.trace.nodes[0], "size")
            ctx.emit(Finding(None, "payload_size", "info", data={"size": size}))
        return []

    def branch(ds, ctx):
        calls.append("branch")
        result = ctx.result("leaf")
        assert result.status == "succeeded"
        assert sum(r["data"]["size"] for r in result.findings()) == 20000
        return []

    def aggregate(ds, ctx):
        calls.append("aggregate")
        assert ctx.result("branch").status == "succeeded"
        assert ctx.result("leaf").error is None
        return [Finding(None, "summary", "info", scope="dataset")]

    definitions = (
        BatchDetector("aggregate", aggregate, ("branch", "leaf")),
        BatchDetector("branch", branch, ("leaf",)),
        BatchDetector("leaf", leaf),
    )
    source = MemorySource()
    async with harness(batch_detectors=definitions).open(source, work_dir=tmp_path) as session:
        ds = await session.select()
        run = await session.detect(
            ds,
            detectors=[],
            metrics=[],
            batch_detectors=["aggregate", "leaf", "aggregate"],
        )
        assert calls == ["leaf", "branch", "aggregate"]
        assert [r["id"] for r in run.rows("detector_runs")] == calls
        manifest = json.loads((run.path / "manifest.json").read_text())
        assert manifest["resolved_batch_detectors"] == calls
        assert len(list(run.rows("findings"))) == 3
        assert {r["detector_id"] for r in run.rows("findings")} == {"leaf", "aggregate"}
        replay = await session.detect(ds, detectors=[], metrics=[], batch_detectors=["aggregate"])
        assert calls == ["leaf", "branch", "aggregate"] * 2
        assert replay.summary["loading"]["reads"] == 0
        assert replay.path != run.path


async def test_failure_partial_empty_and_undeclared_dependency(tmp_path):
    def broken(ds, ctx):
        ctx.emit(Finding(None, "partial", "info"))
        raise RuntimeError("cannot finish")

    def empty(ds, ctx):
        return []

    def aggregate(ds, ctx):
        assert ctx.result("broken").status == "failed"
        assert ctx.result("broken").error["type"] == "RuntimeError"
        assert len(list(ctx.result("broken").findings())) == 1
        assert ctx.result("empty").status == "succeeded"
        assert list(ctx.result("empty").findings()) == []
        return [Finding(None, "partial_summary", "info", data={"complete": False}, scope="dataset")]

    def unauthorized(ds, ctx):
        ctx.result("empty")

    definitions = (
        BatchDetector("summary", aggregate, ("broken", "empty")),
        BatchDetector("broken", broken),
        BatchDetector("empty", empty),
        BatchDetector("unauthorized", unauthorized),
    )
    async with harness(batch_detectors=definitions).open(
        MemorySource(), work_dir=tmp_path
    ) as session:
        ds = await session.select()
        run = await session.detect(ds, detectors=[], metrics=[])
        statuses = {r["id"]: r for r in run.rows("detector_runs")}
        assert statuses["summary"]["status"] == "succeeded"
        assert statuses["unauthorized"]["error"]["type"] == "KeyError"
        assert run.summary["detector_failures"] == 2
        assert json.loads((run.path / "manifest.json").read_text())["status"] == "partial"
        assert "cannot finish" in (run.path / "report.html").read_text()


async def test_invalid_plan_before_source_fetch(tmp_path):
    source = MemorySource()
    configured = harness(batch_detectors=(BatchDetector("a", lambda d, c: [], ("missing",)),))
    async with configured.open(source, work_dir=tmp_path) as session:
        ds = await session.select()
        with pytest.raises(KeyError):
            await session.detect(ds, detectors=[], metrics=[])
        assert source.fetches == source.reads == 0
