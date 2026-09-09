"""Fact transformations share output ownership, dependency resolution and materialization."""

import pytest

from trace_harness import (
    FactTransform,
    KindSpec,
    Node,
    NormSpan,
    TraceContributions,
    TraceHarness,
    TransformContext,
    dump_analysis,
    load_analysis,
)
from trace_harness.model.node import Field
from trace_harness.model.viewtree import build_view


def node(name="n"):
    return Node("test", name, name, [name], {}, 0, 10, service=None, node_id=name)


def test_dependency_is_computed_once_and_only_when_requested():
    n = node()
    calls = []

    def compute(n, ctx):
        assert not hasattr(ctx, "raw")
        calls.append(n.node_id)
        return {"a": 2, "b": 3}

    ctx = TransformContext(
        build_view([n]),
        [
            FactTransform(
                ("c",), lambda n: True, lambda n, ctx: {"c": ctx.get(n, "a") + ctx.get(n, "b")}
            ),
            FactTransform(("a", "b"), lambda n: True, compute),
        ],
    )
    assert n.facts == {} and calls == []
    ctx.materialize([(n, "c")])
    assert n.facts == {"a": 2, "b": 3, "c": 5}
    ctx.materialize([(n, "a"), (n, "b"), (n, "c")])
    assert calls == ["n"]


@pytest.mark.parametrize("conflict", ["duplicate", "base", "cycle", "undeclared"])
def test_invalid_transform_fails_without_partial_facts(conflict):
    n = node()
    producer = FactTransform(("a",), lambda n: True, lambda n, ctx: {"a": 1})
    items = [producer]
    if conflict == "duplicate":
        items.append(producer)
    elif conflict == "base":
        n.facts["a"] = 9
    elif conflict == "cycle":
        items = [
            FactTransform(("a",), lambda n: True, lambda n, ctx: {"a": ctx.get(n, "b")}),
            FactTransform(("b",), lambda n: True, lambda n, ctx: {"b": ctx.get(n, "a")}),
        ]
    else:
        items = [FactTransform(("a",), lambda n: True, lambda n, ctx: {"other": 1})]
    before = dict(n.facts)
    with pytest.raises(ValueError):
        TransformContext(build_view([n]), items).materialize([(n, "a")])
    assert n.facts == before


def test_failed_batch_can_retry_without_cached_partial_dependencies():
    n = node()
    calls = []

    def finish(n, ctx):
        value = ctx.get(n, "a")
        if len(calls) == 1:
            raise ValueError("temporary computation failure")
        return {"b": value + 1}

    ctx = TransformContext(
        build_view([n]),
        [
            FactTransform(
                ("a",), lambda n: True, lambda n, ctx: calls.append(n.node_id) or {"a": 1}
            ),
            FactTransform(("b",), lambda n: True, finish),
        ],
    )
    with pytest.raises(ValueError):
        ctx.materialize([(n, "b")])
    assert n.facts == {}
    ctx.materialize([(n, "b")])
    assert calls == ["n", "n"] and n.facts == {"a": 1, "b": 2}


def test_request_to_curl_is_a_fact_and_projection_requests_only_its_inputs(tmp_path):
    calls = []

    def curl(n, ctx):
        calls.append(n.node_id)
        request = ctx.get(n, "request")
        return {"curl": f"curl -X {request['method']} {request['url']}"}

    harness = TraceHarness(
        TraceContributions(
            specs=(
                KindSpec(
                    "test",
                    lambda span: True,
                    build=lambda span, satellites: {
                        "request": {"method": "POST", "url": "https://example.test/api"}
                    },
                    project=lambda n: [Field("label", n.facts["label"])],
                    project_requires=("label",),
                ),
            ),
            transforms=(
                FactTransform(
                    ("label",),
                    lambda n: True,
                    lambda n, ctx: {"label": ctx.get(n, "request")["method"]},
                ),
                FactTransform(("curl",), lambda n: True, curl),
                FactTransform(
                    ("repro",),
                    lambda n: True,
                    lambda n, ctx: {"repro": {"command": ctx.get(n, "curl")}},
                ),
            ),
        )
    )
    span = NormSpan("n", None, "request", 0, 10, None, False, {}, {"traceID": "t"})
    trace = harness.assemble({"n": span})
    n = trace.nodes[0]
    assert n.brief[0].value == "POST" and calls == []
    harness.analyze(trace)
    assert "curl -X" not in harness.render_interactive(trace)
    assert calls == []
    result = harness.transform(n, trace, "repro")
    assert result == {"repro": {"command": "curl -X POST https://example.test/api"}}
    assert n.facts["curl"] == "curl -X POST https://example.test/api"
    harness.transform_all(trace, "curl", "repro")
    assert calls == ["n"]
    assert "curl -X POST" in harness.render_interactive(trace)
    path = dump_analysis(harness.analyze(trace), tmp_path / "analysis.json")
    loaded = load_analysis(path)
    assert loaded.trace.nodes[0].facts == n.facts
    assert "curl -X POST" in harness.render_interactive(loaded.trace)
    assert calls == ["n"]
    # Same harness, independent trace: projection does not precompute the requested curl.
    other = harness.assemble({"n": span})
    assert "curl" not in other.nodes[0].facts
    harness.transform(other.nodes[0], other, "curl")
    assert calls == ["n", "n"]


def test_missing_outputs_are_cached_and_unknown_nodes_rejected():
    n = node()
    calls = []
    ctx = TransformContext(
        build_view([n]),
        [FactTransform(("optional",), lambda n: True, lambda n, ctx: calls.append(1) or {})],
    )
    ctx.materialize([(n, "optional")])
    ctx.materialize([(n, "optional")])
    assert calls == [1] and n.facts == {}
    with pytest.raises(ValueError, match="does not belong"):
        ctx.materialize([(node("other"), "optional")])
