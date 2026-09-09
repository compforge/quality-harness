"""Measurement conformance, isolated evaluation, rendering and offline reload."""

import json
from pathlib import Path
from random import Random

import pytest

from trace_harness import (
    Measurement,
    MeasurementSpec,
    Measurer,
    Node,
    TraceContext,
    TraceContributions,
    TraceHarness,
    analysis_snapshot,
    dump_analysis,
    load_analysis,
)
from trace_harness.analyze.measure import prefix_values
from trace_harness.kinds import genai
from trace_harness.model.measurement import CallSource
from trace_harness.model.node import Finding
from trace_harness.model.span import NormSpan
from trace_harness.view.measurements import measurements_md

ROOT = Path(__file__).parents[4]
CASES = json.loads((ROOT / "conformance/trace/cases/measurements.json").read_text())


def build(case, contributions=None):
    harness = TraceHarness(contributions or TraceContributions(specs=tuple(genai.specs())))
    spans = {
        item["span_id"]: NormSpan(**item, has_error=False, raw={"traceID": case["name"]})
        for item in case["spans"]
    }
    return harness, harness.assemble(spans)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_shared_measurement_semantics(case):
    harness, trace = build(case)
    result = harness.analyze(trace, diagnosis=False)
    assert result.findings == {}
    for span_id, expected in case["expected"].items():
        node = trace.view().by_span[span_id]
        measurement = result.measurements.get(node.node_id, "calls_until_node_end")
        assert measurement.status == "measured"
        assert measurement.values == expected
        assert measurement.evidence["end_ms"] == node.end_ms
    assert all("self_ms" not in node.facts for node in trace.nodes)
    for source in result.measurements.sources:
        assert source.node_id in trace.view().by_id
    if case["name"] == "paired-caller-clock":
        assert result.measurements.sources[0].span_ids == ("a", "server")


def test_self_clips_children_to_parent_and_leaf_has_full_duration():
    parent = Node("tool", "p", "p", ["p"], {}, 0, 10, service=None, node_id="p")
    child = Node(
        "tool", "c", "c", ["c"], {}, -10, 15, service=None, node_id="c", parent_node_id="p"
    )
    trace = TraceContext("t", {}, [parent, child], {})
    result = TraceHarness(TraceContributions()).measure(trace)
    assert result.get("p", "self_ms").values == {"self_ms": 5}
    assert result.get("c", "self_ms").values == {"self_ms": 15}


def test_measurers_run_once_and_detectors_read_results_and_previous_findings():
    calls, seen = [], []
    spec = MeasurementSpec("custom", "node", {"size": "item"}, "Custom measured value")

    def compute(trace, sources):
        calls.append(trace.trace_id)
        return [Measurement(spec.id, n.node_id, "measured", {"size": 7}) for n in trace.nodes]

    def first(node, analysis):
        assert analysis.measurements.get(node.node_id, "custom").values == {"size": 7}
        assert analysis.trace.view().by_id[node.node_id] is node
        return [Finding(node.node_id, "custom", "info")]

    def second(node, analysis):
        seen.append(any(f.source == "custom" for f in analysis.findings[node.node_id]))
        with pytest.raises(TypeError):
            analysis.findings[node.node_id] = ()
        return []

    harness, trace = build(
        CASES[0],
        TraceContributions(
            specs=tuple(genai.specs()),
            measurers=(Measurer(spec, compute),),
            detectors=(first, second),
        ),
    )
    a = harness.analyze(trace)
    b = harness.analyze(trace, diagnosis=False)
    assert len(calls) == 2 and all(seen)
    assert a.measurements is not b.measurements and b.findings == {}
    assert all("custom" not in node.facts for node in trace.nodes)
    assert (
        TraceHarness(TraceContributions()).measure(trace).get(trace.nodes[0].node_id, "custom")
        is None
    )


def test_failed_and_inapplicable_measurements_are_not_zero():
    spec = MeasurementSpec("broken", "node", {"n": "item"}, "Failure")

    def broken(trace, sources):
        raise ValueError("missing input")

    harness, trace = build(
        CASES[0],
        TraceContributions(
            specs=tuple(genai.specs()),
            measurers=(
                Measurer(spec, broken),
                Measurer(MeasurementSpec("skip", "node", {}, "Skip"), lambda trace, sources: []),
            ),
        ),
    )
    result = harness.measure(trace)
    for node in trace.nodes:
        assert result.get(node.node_id, "broken").status == "error"
        assert result.get(node.node_id, "broken").values == {}
        assert result.get(node.node_id, "broken").error == "missing input"
        assert result.get(node.node_id, "skip").status == "not_applicable"
    with pytest.raises(ValueError, match="duplicate"):
        TraceHarness(
            TraceContributions(measurers=(Measurer(spec, broken), Measurer(spec, broken)))
        ).measure(trace)


def test_roundtrip_renders_saved_measurements_without_execution(tmp_path):
    harness, trace = build(CASES[1])
    analysis = harness.analyze(trace, diagnosis=False)
    path = dump_analysis(analysis, tmp_path / "analysis.json")
    loaded = load_analysis(path)
    assert analysis_snapshot(loaded) == analysis_snapshot(analysis)
    assert not loaded.trace.spans
    assert loaded.trace.span_count == trace.span_count
    html = harness.render_interactive(loaded.trace, measurements=loaded.measurements)
    assert '"duration_sum_ms": 50' in html
    assert '"scope": "trace_prefix"' in html
    assert "calls_until_node_end" in measurements_md(loaded.trace, loaded.measurements)
    # Rendering never computes absent measurements or invokes transforms.
    assert '"measurements": []' in harness.render_interactive(trace)


def test_index_matches_independent_naive_oracle():
    rng = Random(17)
    sources = []
    for i in range(100):
        start = rng.randrange(0, 50)
        sources.append(
            CallSource(str(i), "http", start, start + rng.randrange(0, 20), str(i), (str(i),))
        )
    cuts = list(range(80))
    actual = prefix_values(sources, cuts, 0)
    for cut in cuts:
        started = [source for source in sources if source.start_ms <= cut]
        total = sum(max(0, min(source.end_ms, cut) - source.start_ms) for source in started)
        covered = sum(
            any(source.start_ms <= t and source.end_ms > t for source in sources)
            for t in range(cut)
        )
        assert actual[cut]["http"] == {
            "count": len(started),
            "duration_sum_ms": total,
            "covered_ms": covered,
        }


def test_evidence_size_is_linear_in_trace_size():
    nodes = [
        Node("tool", str(i), str(i), [str(i)], {}, i, 1, service=None, node_id=str(i))
        for i in range(2000)
    ]
    result = TraceHarness(TraceContributions()).measure(TraceContext("large", {}, nodes, {}))
    assert len(result.sources) == len(nodes)
    assert sum(len(m.evidence) for group in result.results.values() for m in group) == 5 * len(
        nodes
    )


def test_invalid_interval_is_an_error_not_zero():
    node = Node("tool", "bad", "bad", ["bad"], {}, 0, -1, service=None, node_id="bad")
    result = TraceHarness(TraceContributions()).measure(TraceContext("bad", {}, [node], {}))
    assert all(m.status == "error" and not m.values for m in result.results["bad"])


def test_failed_result_cannot_carry_numeric_values():
    spec = MeasurementSpec("invalid", "node", {"n": "item"}, "Invalid output")

    def compute(trace, sources):
        return [Measurement(spec.id, n.node_id, "error", {"n": 0}) for n in trace.nodes]

    harness, trace = build(
        CASES[0],
        TraceContributions(
            specs=tuple(genai.specs()),
            measurers=(Measurer(spec, compute),),
        ),
    )
    result = harness.measure(trace)
    assert all(result.get(n.node_id, "invalid").values == {} for n in trace.nodes)
