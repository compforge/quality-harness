"""Shared display contract: service → API sequence → original calls."""

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from trace_harness import TraceContributions, TraceHarness
from trace_harness.kinds import genai
from trace_harness.model.ir import dump_view, load_view
from trace_harness.model.span import NormSpan
from trace_harness.view.engine import render

CASES = json.loads(
    (Path(__file__).parents[4] / "conformance/trace/cases/http-groups.json").read_text()
)


def outline(node):
    children = [outline(child) for child in node.children]
    if node.kind:
        return {"node": node.node_ids[0], "children": children} if children else node.node_ids[0]
    return {
        "name": node.name,
        "members": node.node_ids,
        "folded": bool(node.folded),
        "brief": {field.label: field.value for field in node.brief},
        "children": children,
    }


def walk(nodes):
    for node in nodes:
        yield node
        yield from walk(node["children"])


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_http_group_conformance(case, tmp_path):
    harness = TraceHarness(TraceContributions(specs=tuple(genai.specs())))
    context = harness.assemble(
        {
            item["span_id"]: NormSpan(**item, has_error=False, raw={"traceID": case["name"]})
            for item in case["spans"]
        }
    )
    findings = harness.diagnose(context) if case.get("diagnose", True) else {}
    before = [asdict(node) for node in context.nodes]
    display = harness.render_display(context, findings)
    assert [outline(node) for node in display] == case["expected"]
    assert [asdict(node) for node in context.nodes] == before
    # The grouping projection must work from saved IR, with no raw HTTP attributes.
    saved = load_view(dump_view(context, tmp_path / "nodes.json"))
    assert [outline(node) for node in render(saved.view(), findings)] == case["expected"]
    html = harness.render_interactive(context, findings)
    payload = json.loads(html.split("const TREES=", 1)[1].split(",SPANS=", 1)[0])
    nodes = list(walk(payload["full"]["roots"]))
    ids = [node["node_id"] for node in nodes]
    assert len(ids) == len(set(ids))  # Service and API group can contain identical members.
    assert {node["primary_span_id"] for node in nodes if node["kind"]} == {
        item["span_id"] for item in case["spans"]
    }
    if case["name"] == "server-clock-skew":
        group = next(node for node in nodes if node["name"].startswith("⚠"))
        assert group["duration_ms"] == 100
