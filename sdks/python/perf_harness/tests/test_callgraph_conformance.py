"""Both SDKs render the same persisted operation evidence without generating load."""

import json
from pathlib import Path

from perf_harness import load_run
from perf_harness.callgraph import build_callgraph


def test_shared_operation_callgraph_fixture():
    fixture = Path(__file__).resolve().parents[4] / "conformance/perf/fixtures/operation-metrics"
    snapshot = build_callgraph(load_run(fixture))
    assert snapshot is not None
    assert snapshot.to_dict() == json.loads((fixture / "callgraph.json").read_text())
