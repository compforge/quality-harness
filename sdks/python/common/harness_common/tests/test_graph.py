"""Shared topology contracts exercised through package-root exports."""

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from harness_common import (
    Graph,
    Node,
    NodeKind,
    Relation,
    RelationKind,
    Service,
    Operation,
    Component,
    Environment,
    Forge,
    Repository,
    Identity,
)

FIXTURE = json.loads(
    (Path(__file__).resolve().parents[5] / "conformance/common/graphs.json").read_text()
)


def test_shared_topology_roundtrip_and_call_traversal():
    """@case Shared downstream and cyclic calls preserve one node and terminate traversal."""
    graph = Graph.from_dict(FIXTURE["graph"])
    assert Graph.from_dict(json.loads(json.dumps(graph.to_dict()))) == graph
    assert graph.to_dict() == FIXTURE["graph"]
    assert [
        n.id for n in graph.reachable_from(FIXTURE["root"], kind=RelationKind.CALLS)
    ] == FIXTURE["reachable"]
    assert len(graph.relations_to("operation:d/api4", kind=RelationKind.CALLS)) == 2
    assert len(graph.relations_from(FIXTURE["root"], kind=RelationKind.CALLS)) == 2
    assert graph.node("missing") is None
    assert graph.relations_to("missing") == ()
    with pytest.raises(ValueError, match="unknown root"):
        graph.reachable_from("missing")


def test_subgraph_preserves_owners_without_sibling_apis():
    graph = Graph.from_dict(FIXTURE["graph"])
    subset = graph.subgraph(
        n.id for n in graph.reachable_from("operation:a/api1", kind=RelationKind.CALLS)
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


def _service(name: str) -> Service:
    return Service(
        name=name,
        component=Component(
            repository=Repository(Forge("github"), "example/app"), name="api"
        ),
        environment=Environment("qa"),
    )


def test_model_identity_is_shared_and_segment_safe():
    for case in FIXTURE["ids"]:
        service: Identity = _service(case["service"])
        operation: Identity = Operation(
            name=case["operation"], service_name=case["service"]
        )
        assert service.id() == case["service_id"]
        assert operation.id() == case["operation_id"]
    assert (
        Operation("c", service_name="a/b").id()
        != Operation("b/c", service_name="a").id()
    )
    assert (
        Operation("same", service_name="a").id()
        != Operation("same", service_name="b").id()
    )
    with pytest.raises(ValueError):
        Operation("", service_name="a").id()
    with pytest.raises(ValueError):
        _service("").id()


def test_snapshot_detaches_input_and_supports_open_kinds():
    nodes = [Node("model:x", NodeKind("model"))]
    relations = [Relation("model:x", "model:x", RelationKind("uses"))]
    graph = Graph(environment="qa", nodes=nodes, relations=relations)
    nodes.clear()
    relations.clear()
    assert graph.node("model:x") == Node("model:x", NodeKind("model"))
    assert len(graph.reachable_from("model:x")) == 1
    with pytest.raises(FrozenInstanceError):
        graph.environment = "prod"
    wire = graph.to_dict()
    wire["nodes"][0]["kind"] = "changed"
    assert graph.nodes[0].kind == "model"
    assert Graph.from_dict(graph.to_dict()) == graph
    # JSON tuple encoding keeps IDs unambiguous even when names contain delimiters.
    assert (
        Relation("a,b", "c", RelationKind("uses")).id
        != Relation("a", "b,c", RelationKind("uses")).id
    )


def test_open_enums_serialize_and_enforce_constructor_types():
    """Constructors take enum values; wire decoding restores known and custom kinds."""
    nodes = (
        Node("s", NodeKind.SERVICE),
        Node("o", NodeKind.OPERATION),
        Node("m", NodeKind("model")),
    )
    relations = (
        Relation("o", "s", RelationKind.BELONGS_TO),
        Relation("o", "o", RelationKind.CALLS),
        Relation("o", "m", RelationKind("uses")),
    )
    graph = Graph(environment="qa", nodes=nodes, relations=relations)
    loaded = Graph.from_dict(json.loads(json.dumps(graph.to_dict())))
    assert loaded == graph
    assert loaded.node("s").kind is NodeKind.SERVICE
    assert loaded.node("m").kind == "model"
    assert loaded.relations_from("o", kind=RelationKind("uses")) == (relations[2],)
    assert graph.reachable_from("o", kind=RelationKind.CALLS) == (nodes[1],)
    assert str(NodeKind("model")) == "model"
    with pytest.raises(TypeError, match="NodeKind"):
        Node("s", "service")
    with pytest.raises(TypeError, match="RelationKind"):
        Relation("o", "o", "calls")
    assert Relation("o", "o", RelationKind.CALLS).id == '["o","calls","o"]'
    assert list(NodeKind) == [NodeKind.SERVICE, NodeKind.OPERATION]
    assert list(RelationKind) == [RelationKind.BELONGS_TO, RelationKind.CALLS]
    for kind_type in (NodeKind, RelationKind):
        with pytest.raises(ValueError):
            kind_type("")
    with pytest.raises(ValueError, match="calls requires"):
        Graph(
            environment="qa",
            nodes=nodes,
            relations=relations + (Relation("s", "o", RelationKind.CALLS),),
        )
