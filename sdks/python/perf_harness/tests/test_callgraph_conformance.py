"""Both SDKs render the same persisted operation evidence without generating load."""

import json
from pathlib import Path

import pytest

from perf_harness import load_run
from perf_harness.callgraph import build_callgraph


def test_shared_operation_callgraph_fixture():
    fixture = Path(__file__).resolve().parents[4] / "conformance/perf/fixtures/operation-metrics"
    snapshot = build_callgraph(load_run(fixture))
    assert snapshot is not None
    assert snapshot.to_dict() == json.loads((fixture / "callgraph.json").read_text())


@pytest.mark.parametrize(
    "error, warnings, reason",
    [
        (None, (), "empty_result"),
        ("ValueError: Prometheus query returned no samples", (), "empty_result"),
        ("query cancelled", (), "query_error"),
        (None, ("partial response",), "query_error"),
    ],
)
def test_empty_result_does_not_hide_warnings_or_unrelated_errors(error, warnings, reason):
    fixture = Path(__file__).resolve().parents[4] / "conformance/perf/fixtures/operation-metrics"
    run = load_run(fixture)
    query = next(
        q
        for q in run.arm_runs[0].prometheus_observations
        if q.labels.get("operation") == "operation:mock/empty"
    )
    query.error, query.warnings = error, warnings
    snapshot = build_callgraph(run).to_dict()
    node = next(
        n for n in snapshot["arms"][0]["windows"][0]["nodes"] if n["id"] == "operation:mock/empty"
    )
    metric = node["properties"]["perf"]["metrics"]["qps"]
    assert metric["reason"] == reason
    assert metric["status"] == ("error" if reason == "query_error" else "missing")
