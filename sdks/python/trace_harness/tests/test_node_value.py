"""Business search text survives assembly and offline transport."""

import json
from pathlib import Path

from trace_harness.ingest.assemble import assemble
from trace_harness.kinds import genai
from trace_harness.model.analysis import load_analysis
from trace_harness.model.ir import dump_view, load_view
from trace_harness.model.span import NormSpan
from trace_harness.model.spec import KindSpec, SpecSet

ROOT = Path(__file__).parents[4]


def span(sid, attrs, parent=None):
    return NormSpan(sid, parent, "http.server", 0, 1, "sandbox-server", False, attrs, {})


def test_http_value_shared_contract(tmp_path):
    cases = json.loads((ROOT / "conformance/trace/cases/node-value.json").read_text())
    for case in cases:
        primary = span("root", {"http.request.method": "POST", **case["attrs"]})
        for prepare in (True, False):
            context = assemble({"root": primary}, genai.specs(), prepare=prepare)
            assert context.nodes[0].kind == "http"
            assert context.nodes[0].value == case["value"]
            path = dump_view(context, tmp_path / "nodes.json")
            assert load_view(path).nodes[0].value == case["value"]


def test_business_value_uses_claimed_spans_without_child_concatenation():
    specs = SpecSet(
        [
            KindSpec(
                kind="business",
                matches=lambda s: s.span_id == "root",
                claims=lambda p, candidates: {"sat"},
                value=lambda p, satellites: p.name + ":" + satellites[0].attrs["text"],
            ),
            KindSpec(kind="child", matches=lambda s: s.span_id == "child"),
        ]
    )
    context = assemble(
        {
            "root": span("root", {}),
            "sat": span("sat", {"text": "selected"}, "root"),
            "child": span("child", {"text": "not selected"}, "root"),
        },
        specs,
    )
    assert {n.node_id: n.value for n in context.nodes} == {
        "root": "http.server:selected",
        "child": "",
    }


def test_old_snapshots_default_to_empty_value(tmp_path):
    payload = json.loads((ROOT / "conformance/trace/cases/genai-basic.analysis.json").read_text())
    path = tmp_path / "analysis.json"
    path.write_text(json.dumps(payload))
    expected = [n["value"] for n in payload["nodes"]]
    assert [n.value for n in load_analysis(path).trace.nodes] == expected
    for node in payload["nodes"]:
        del node["value"]
    path.write_text(json.dumps(payload))
    context = load_analysis(path).trace
    assert all(n.value == "" for n in context.nodes)
    nodes_path = dump_view(context, tmp_path / "nodes.json")
    nodes = json.loads(nodes_path.read_text())
    for node in nodes["nodes"]:
        del node["value"]
    nodes_path.write_text(json.dumps(nodes))
    assert all(n.value == "" for n in load_view(nodes_path).nodes)
