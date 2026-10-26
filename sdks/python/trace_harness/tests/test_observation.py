"""One Observation model from domain extraction to saved analysis and call-site rendering."""

import json
from pathlib import Path

import pytest
from agent_ledger.common import CallRequest, CallResponse, SessionRef
from agent_ledger.observation import (
    Input,
    ModelCall,
    Operation,
    SessionView,
    SourceRef,
    ToolCall,
    Turn,
)

from trace_harness import (
    AnalysisContext,
    TraceContributions,
    TraceHarness,
    analysis_snapshot,
    build_view,
    dump_analysis,
    load_analysis,
)
from trace_harness.ingest.sources.jaeger_file import load_jaeger_file
from trace_harness.kinds import genai
from trace_harness.observation import validate_session_views, walk_nodes
from trace_harness.tests.view_fixture import prepared_html, report_html
from trace_harness.view.observation import session_roots

ROOT = Path(__file__).parents[4]
RAW = ROOT / "conformance/trace/fixtures/genai-basic.jsonl"
EXPECTED = ROOT / "conformance/trace/cases/genai-basic.session-views.json"


class FixtureExtractor:
    def extract(self, context):
        return tuple(
            SessionView.model_validate(value) for value in json.loads(EXPECTED.read_text())
        )


def harness(extractor=None):
    return TraceHarness(
        TraceContributions(specs=tuple(genai.specs()), session_view_extractor=extractor)
    )


def context():
    return harness().assemble(load_jaeger_file(RAW))


def view(*items, id="session"):
    return SessionView(session=SessionRef(source="fixture", id=id), nodes=list(items))


def rows(roots):
    for row in roots:
        yield row
        yield from rows(row["children"])


def test_extraction_uses_shared_observation_fixture_and_detaches_mutable_data():
    h = harness(FixtureExtractor())
    ctx = context()
    views = h.extract_session_views(ctx)
    assert [v.model_dump(mode="json") for v in views] == json.loads(EXPECTED.read_text())
    assert isinstance(views[0], SessionView)
    assert isinstance(
        next(i for i in walk_nodes(views[0].nodes) if i.kind == "model_call"), ModelCall
    )
    prepared = h.prepare(AnalysisContext(ctx), session_views=True)
    assert prepared.session_views == views
    assert h.prepare(prepared, session_views=True) is prepared
    copied = validate_session_views(views, ctx)
    copied[0].nodes.clear()
    assert views[0].nodes


def test_observations_roundtrip_without_extraction_or_loss(tmp_path):
    h = harness(FixtureExtractor())
    analysis = h.prepare(AnalysisContext(context()), session_views=True)
    before = analysis_snapshot(analysis)
    path = dump_analysis(analysis, tmp_path / "analysis.json")
    loaded = load_analysis(path)
    assert analysis_snapshot(loaded) == before
    index = json.loads((tmp_path / "analysis.session-views/index.json").read_text())
    assert len(index["sessions"]) == 2
    for record, expected in zip(index["sessions"], analysis.session_views, strict=True):
        assert (
            SessionView.model_validate_json(
                (tmp_path / "analysis.session-views" / record["file"]).read_text()
            )
            == expected
        )
    assert build_view(loaded, {"scope": "trace", "view": "agent"})["rows"]["total"] > 0
    assert analysis_snapshot(analysis) == before


def test_tool_sessions_expand_at_call_site_without_merging_execution_models():
    ctx = context()
    views = harness(FixtureExtractor()).extract_session_views(ctx)
    original = [v.model_dump(mode="json") for v in views]
    roots = session_roots(ctx, views, {})
    tool = next(row for row in rows([roots[0]]) if row["kind"] == "tool_call")
    assert tool["children"][0]["kind"] == "session"
    assert tool["children"][0]["name"] == views[1].session.id
    assert any(row["name"] == "worker-model" for row in rows(tool["children"]))
    assert len([i for i in walk_nodes(views[0].nodes) if isinstance(i, ModelCall)]) == 2
    assert len({row["node_id"] for row in rows(roots)}) == len(list(rows(roots)))
    assert [v.model_dump(mode="json") for v in views] == original


def test_session_reference_cycles_missing_targets_and_repeated_calls():
    a = view(
        ToolCall(id="first", sessions=[SessionRef(source="fixture", id="b")]),
        ToolCall(
            id="second",
            sessions=[
                SessionRef(source="fixture", id="b"),
                SessionRef(source="remote", id="absent"),
            ],
        ),
        id="a",
    )
    a.nodes[0].sessions.append(a.nodes[0].sessions[0])
    b = view(ToolCall(id="back", sessions=[a.session]), id="b")
    result = list(rows(session_roots(context(), (a, b), {})))
    assert len(result) < 30
    assert any('"cycle"' in row["details"].get("sessions", "") for row in result)
    assert any('"unavailable"' in row["details"].get("sessions", "") for row in result)
    assert len({row["node_id"] for row in result}) == len(result)


def test_all_item_kinds_and_explicit_null_survive_render_and_snapshot(tmp_path):
    native = view(
        Input(id="input", content=None),
        Turn(
            id="turn",
            nodes=[
                ToolCall(
                    id="read",
                    request=CallRequest(
                        tool_name="read_file", input={"path": "/workspace/market.md"}
                    ),
                    response=CallResponse(output=None),
                ),
                ModelCall(id="model", response=CallResponse(output="partial")),
            ],
        ),
        Operation(id="op", operation="compact", data=None, nodes=[]),
    )
    analysis = AnalysisContext(context(), session_views=(native,))
    loaded = load_analysis(dump_analysis(analysis, tmp_path / "saved.json"))
    assert loaded.session_views == (native,)
    rendered = list(rows(session_roots(context(), (native,), {})))
    tool = next(row for row in rendered if row["kind"] == "tool_call")
    assert tool["name_variants"] == ["read_file · market.md", "market.md"]
    assert json.loads(tool["details"]["response"]) == {"output": None}
    assert next(row for row in rendered if row["kind"] == "input")["details"]["content"] == "null"
    assert native.nodes[1].nodes[1].request is None
    assert all(row["timing_known"] is False for row in rendered)


def test_evidence_validation_is_local_and_identity_is_source_scoped():
    ctx = context()
    with pytest.raises(ValueError, match="unknown node IDs"):
        validate_session_views(
            (
                view(
                    Operation(
                        id="bad",
                        operation="bad",
                        evidence=[SourceRef(source="trace-node", id="missing")],
                    )
                ),
            ),
            ctx,
        )
    foreign = view(
        Operation(
            id="foreign",
            operation="read",
            evidence=[SourceRef(source="trace-node", id="missing", uri="trace:other")],
        )
    )
    assert validate_session_views((foreign,), ctx)
    assert session_roots(ctx, (foreign,), {})[0]["children"][0]["source_node_ids"] == []
    other = foreign.model_copy(deep=True)
    other.session.source = "other"
    assert len(validate_session_views((foreign, other), ctx)) == 2
    with pytest.raises(ValueError, match="duplicate Session"):
        validate_session_views((foreign, foreign), ctx)


def test_shared_validation_allows_async_children_but_rejects_invalid_calls():
    async_work = view(
        Operation(
            id="parent",
            operation="dispatch",
            started_at="2026-10-25T00:00:01Z",
            finished_at="2026-10-25T00:00:02Z",
            nodes=[Operation(id="child", operation="async", started_at="2026-10-25T00:00:03Z")],
        )
    )
    assert validate_session_views((async_work,), context())
    async_work.nodes.append(ToolCall(id="bad", model_call_id="missing"))
    with pytest.raises(ValueError, match="missing model call"):
        validate_session_views((async_work,), context())


def test_unavailable_and_prepared_empty_observations_stay_distinct(tmp_path):
    ctx = context()
    assert harness().extract_session_views(ctx) is None
    assert (
        build_view(AnalysisContext(ctx), {"scope": "trace", "view": "agent"})["status"]
        == "not_available"
    )
    empty = AnalysisContext(ctx, session_views=())
    restored = load_analysis(dump_analysis(empty, tmp_path / "empty.json"))
    assert restored.session_views == ()
    assert build_view(restored, {"scope": "trace", "view": "agent"})["status"] == "available"
    assert 'data-perspective="agent"' not in report_html(harness(), ctx)
    assert 'data-perspective="agent"' in prepared_html(ctx, session_views=())
    assert 'data-perspective="agent"' in prepared_html(ctx, session_views=[])
    assert 'data-perspective="agent"' in report_html(harness(FixtureExtractor()), ctx)


def test_nested_operation_sources_and_errors_survive_rendering():
    ctx = context()
    source = next(n for n in ctx.nodes if n.name == "invoke_agent main")
    native = view(
        Turn(
            id="turn",
            nodes=[
                Operation(
                    id="outer",
                    operation="outer",
                    nodes=[
                        Operation(
                            id="nested",
                            operation="nested",
                            status="failed",
                            evidence=[SourceRef(source="trace-node", id=source.node_id)],
                        )
                    ],
                )
            ],
        )
    )
    turn = session_roots(ctx, (native,), {})[0]["children"][0]
    assert turn["source_node_ids"] == [source.node_id]
    assert turn["span_ids"] == list(source.span_ids)
    assert turn["children"][0]["collapsed"] is False
