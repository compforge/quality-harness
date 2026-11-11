"""Shared topology contracts exercised through package-root exports."""

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from harness_common import Graph, Node, Relation, operation_node_id, service_node_id

FIXTURE = json.loads(
    (Path(__file__).resolve().parents[5] / "conformance/common/graphs.json").read_text()
)


def test_shared_topology_roundtrip_and_call_traversal():
    """@case Shared downstream and cyclic calls preserve one node and terminate traversal."""
    graph = Graph.from_dict(FIXTURE["graph"])
    assert Graph.from_dict(json.loads(json.dumps(graph.to_dict()))) == graph
    assert graph.to_dict() == FIXTURE["graph"]
    assert [
        n.id for n in graph.reachable_from(FIXTURE["root"], kind="calls")
    ] == FIXTURE["reachable"]
    assert len(graph.relations_to("operation:d/api4", kind="calls")) == 2
    assert len(graph.relations_from(FIXTURE["root"], kind="calls")) == 2
    assert graph.node("missing") is None
    assert graph.relations_to("missing") == ()
    with pytest.raises(ValueError, match="unknown root"):
        graph.reachable_from("missing")


def test_subgraph_preserves_owners_without_sibling_apis():
    graph = Graph.from_dict(FIXTURE["graph"])
    subset = graph.subgraph(
        n.id for n in graph.reachable_from("operation:a/api1", kind="calls")
    )
    assert {n.id for n in subset.nodes} == {
        "operation:a/api1",
        "operation:d/api4",
        "service:a",
        "service:d",
    }
    assert subset.environment == graph.environment
    assert subset.source == graph.source and subset.version == graph.version
    assert Graph.from_dict(subset.to_dict()) == subset
    assert graph.subgraph([]).nodes == ()
    with pytest.raises(ValueError, match="unknown nodes"):
        graph.subgraph(["missing"])


@pytest.mark.parametrize("case", FIXTURE["invalid"], ids=lambda c: c["name"])
def test_invalid_topology_rejected(case):
    with pytest.raises(ValueError):
        Graph.from_dict(case["graph"])


def test_name_ids_are_shared_and_segment_safe():
    for case in FIXTURE["ids"]:
        assert service_node_id(case["service"]) == case["service_id"]
        assert (
            operation_node_id(case["service"], case["operation"])
            == case["operation_id"]
        )
    assert operation_node_id("a/b", "c") != operation_node_id("a", "b/c")
    assert operation_node_id("a", "same") != operation_node_id("b", "same")
    with pytest.raises(ValueError):
        operation_node_id("a", "")


def test_snapshot_detaches_input_and_supports_open_kinds():
    nodes = [Node("model:x", "model")]
    relations = [Relation("model:x", "model:x", "uses")]
    graph = Graph(environment="qa", nodes=nodes, relations=relations)
    nodes.clear()
    relations.clear()
    assert graph.node("model:x") == Node("model:x", "model")
    assert len(graph.reachable_from("model:x")) == 1
    with pytest.raises(FrozenInstanceError):
        graph.environment = "prod"
    wire = graph.to_dict()
    wire["nodes"][0]["kind"] = "changed"
    assert graph.nodes[0].kind == "model"
    assert Graph.from_dict(graph.to_dict()) == graph
    # JSON tuple encoding keeps IDs unambiguous even when names contain delimiters.
    assert Relation("a,b", "c", "uses").id != Relation("a", "b,c", "uses").id
