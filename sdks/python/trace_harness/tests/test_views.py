"""Shared ownership and offline View boundary cases."""

import json
from dataclasses import asdict
from pathlib import Path

from trace_harness import (
    AnalysisContext,
    Measurement,
    Measurements,
    MeasurementSpec,
    Measurer,
    Node,
    TraceContributions,
    TraceHarness,
    build_view,
    dump_analysis,
    load_analysis,
    render,
)
from trace_harness.model.context import TraceContext
from trace_harness.model.measurement import CallSource
from trace_harness.model.node import Finding

FIXTURES = Path(__file__).parents[4] / "conformance/trace/fixtures"


async def test_trace_node_owners_and_detector_inputs(tmp_path):
    contract = json.loads((FIXTURES / "view-ownership.json").read_text())
    node = Node(
        node_id=contract["node_id"],
        kind="node",
        name="intention",
        service="test",
        primary_span_id="s",
        span_ids=["s"],
        facts={},
        start_ms=0,
        duration_ms=10,
    )
    trace = TraceContext(contract["trace_id"], {}, [node], {})
    measurer = Measurer(
        MeasurementSpec(contract["trace_kind"], "trace", {"count": "call"}, "Whole trace count"),
        lambda context, sources: [
            Measurement(contract["trace_kind"], None, "measured", {"count": len(context.nodes)})
        ],
    )

    def detector(node, analysis):
        if analysis.measurements.get(None, contract["trace_kind"]).values["count"] == 1:
            return [
                Finding(ref=node.node_id, source="policy", severity="info", note="chosen policy")
            ]
        return []

    harness = TraceHarness(TraceContributions(measurers=(measurer,), detectors=(detector,)))
    before = asdict(node)
    analysis = await harness.analyze(trace)
    assert len(analysis.measurements.trace) == contract["expected_trace_count"]
    assert (
        analysis.measurements.get(node.node_id, contract["node_kind"]).kind == contract["node_kind"]
    )
    assert any(f.source == "policy" for f in analysis.findings[node.node_id])
    view = harness.build_view(analysis, {"scope": "trace"})
    assert view["measurements"][0]["scope"] == "trace"
    loaded = load_analysis(dump_analysis(analysis, tmp_path / "analysis.json"))
    assert loaded.measurements.trace == analysis.measurements.trace
    for format in contract["formats"]:
        assert contract["trace_id"] in render(
            json.loads(render(view, format="json")), format=format
        )
    assert asdict(node) == before


def test_node_views_use_prepared_measurement_and_shared_window(tmp_path):
    fixture = json.loads((FIXTURES / "inspection-window.json").read_text())
    nodes = [
        Node(
            node_id=c["node_id"],
            kind=c["kind"],
            name=c["name"],
            primary_span_id=c["span_ids"][0],
            span_ids=c["span_ids"],
            facts={},
            service=c["service"],
            start_ms=c["start_ms"],
            duration_ms=c["end_ms"] - c["start_ms"],
        )
        for c in fixture["calls"]
    ]
    trace = TraceContext("offline", {}, nodes, {})
    sources = [
        CallSource(**{k: v for k, v in c.items() if k in CallSource.__dataclass_fields__})
        for c in fixture["calls"]
    ]
    measurement = Measurement(
        "calls_until_node_end",
        "anchor",
        "measured",
        {"node": {"count": 4}},
        {"source": "calls", "start_ms": fixture["window"][0], "end_ms": fixture["window"][1]},
    )
    measurements = Measurements(
        [MeasurementSpec(measurement.kind, "node", {"count": "calls"}, "prefix", ("call_kind",))],
        sources,
        {"anchor": [measurement]},
    )
    harness = TraceHarness(TraceContributions())
    analysis = AnalysisContext(trace, measurements)
    request = {"scope": "node", "view": "timeline", "nodeId": "anchor", "limit": 2}
    assert build_view(analysis, request)["timing"] is None
    analysis = harness.prepare(analysis, call_breakdowns=["anchor"])
    loaded = load_analysis(dump_analysis(analysis, tmp_path / "analysis.json"))
    timeline = build_view(loaded, request)
    assert timeline["node"]["duration_ms"] == 10
    assert timeline["timing"]["window"]["elapsed_ms"] == 30
    assert timeline["calls"]["total"] == 4
    assert timeline["calls"]["next_offset"] == 2
    assert timeline["calls"]["items"][1]["duration"] == 15
    view = build_view(loaded, {**request, "view": "measurements", "limit": 1})
    group = view["groups"]["items"][0]
    assert all(group[k] == v for k, v in fixture["expected_group"].items())
    assert "facts" not in view["node"]
    assert "calls" not in view["measurements"][-1]["values"]
    before = json.dumps(asdict(loaded.measurements))
    for format in ("text", "html", "json"):
        assert "anchor" in render(json.loads(render(view, format="json")), format=format)
    assert json.dumps(asdict(loaded.measurements)) == before


def test_agent_derivation_survives_snapshot_without_extraction(tmp_path):
    from agent_ledger.common import SessionRef
    from agent_ledger.observation import Operation, SessionView, SourceRef

    node = Node(
        node_id="source",
        kind="agent",
        name="agent",
        primary_span_id="s",
        span_ids=["s"],
        service="test",
        facts={},
        start_ms=0,
        duration_ms=10,
        error_span_ids=["s"],
        error_text="saved error without raw spans",
    )
    trace = TraceContext("agent", {}, [node], {})
    views = (
        SessionView(
            session=SessionRef(source="fixture", id="session"),
            nodes=[
                Operation(
                    id="op",
                    operation="tool preparation",
                    evidence=[SourceRef(source="trace-node", id="source")],
                )
            ],
        ),
    )
    analysis = AnalysisContext(trace, session_views=views)
    loaded = load_analysis(dump_analysis(analysis, tmp_path / "agent.json"))
    view = build_view(loaded, {"scope": "trace", "view": "agent", "offset": 1})
    assert view["rows"]["items"][0]["parent_id"] == 'session:["fixture","session"]'
    assert view["rows"]["items"][0]["depth"] == 1
    assert view["rows"]["items"][0]["error"] == "saved error without raw spans"
    assert "tool preparation" in render(view, format="text")
    assert "tool preparation" in render(json.loads(render(view, format="json")), format="html")


def test_html_perspective_buttons_identify_the_initial_view():
    from html.parser import HTMLParser

    import pytest

    class Buttons(HTMLParser):
        def __init__(self):
            super().__init__()
            self.items = []
            self.current = None

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == "button" and "data-perspective" in attrs:
                self.current = {**attrs, "label": ""}
                self.items.append(self.current)

        def handle_data(self, data):
            if self.current is not None:
                self.current["label"] += data

        def handle_endtag(self, tag):
            if tag == "button":
                self.current = None

    contract = json.loads((FIXTURES / "view-composition.json").read_text())
    analysis = AnalysisContext(TraceContext(contract["trace_id"], {}, [], {}))
    default = build_view(analysis, {"scope": "trace"})
    agent = build_view(analysis, {"scope": "trace", "view": "agent"})
    by_name = {"default": default, "agent": agent}
    for order in contract["orders"]:
        views = [by_name[name] for name in order]
        buttons = Buttons()
        buttons.feed(render(views, format="html"))
        assert [b["data-perspective"] for b in buttons.items] == [v["view"] for v in views]
        assert [b["data-perspective"] for b in buttons.items if b.get("class") == "active"] == [
            views[0]["view"]
        ]
        assert [b["label"] for b in buttons.items] == [contract["labels"][v["view"]] for v in views]

    for format in contract["rejected_formats"]:
        with pytest.raises(ValueError, match="require HTML"):
            render([default, agent], format=format)
    for views, message in [
        ([], "requires a trace view"),
        ([default, default], "Duplicate"),
        ([default, {**agent, "trace_id": "other"}], "same trace"),
        ([{**default, "scope": "node"}], "requires trace views"),
    ]:
        with pytest.raises(ValueError, match=message):
            render(views, format="html")
