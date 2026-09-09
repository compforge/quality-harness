"""AgentRun IR extraction and generic rendering."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trace_harness import (
    AgentRun,
    AgentRunIR,
    AgentTurn,
    ModelCall,
    Operation,
    ToolCall,
    TraceContributions,
    TraceHarness,
    agent_run_snapshot,
)
from trace_harness.ingest.sources.jaeger_file import load_jaeger_file
from trace_harness.kinds import genai
from trace_harness.view.agent_run import agent_run_roots

ROOT = Path(__file__).parents[4]
RAW = ROOT / "conformance" / "trace" / "fixtures" / "genai-basic.jsonl"
EXPECTED = ROOT / "conformance" / "trace" / "cases" / "genai-basic.agent-run.json"


class _FixtureAgentRunExtractor:
    """Sanitized business extraction rule used only by the shared conformance case."""

    def extract(self, context):
        by_name = {node.name: node for node in context.nodes}
        agent = by_name["invoke_agent main"]
        planner = by_name["chat planner"]
        tool = by_name["execute_tool web_search"]
        synth = by_name["chat synth"]
        return AgentRunIR(
            trace_id=context.trace_id,
            runs=(
                AgentRun(
                    id="run-main",
                    name="main-agent",
                    start_ms=agent.start_ms,
                    duration_ms=agent.duration_ms,
                    status="error",
                    source_node_ids=(agent.node_id,),
                    items=(
                        Operation(
                            id="initialize-1",
                            name="run.initialize",
                            start_ms=agent.start_ms,
                            duration_ms=100,
                            status="completed",
                            source_node_ids=(agent.node_id,),
                            operations=(
                                Operation(
                                    id="load-context-1",
                                    name="context.load",
                                    start_ms=agent.start_ms + 10,
                                    duration_ms=50,
                                    status="completed",
                                    source_node_ids=(agent.node_id,),
                                ),
                            ),
                        ),
                        AgentTurn(
                            id="turn-plan",
                            name="Plan and search",
                            start_ms=planner.start_ms,
                            duration_ms=4750,
                            items=(
                                ModelCall(
                                    id="model-plan",
                                    name="planner",
                                    model="model-alpha-seed-2",
                                    start_ms=planner.start_ms,
                                    duration_ms=planner.duration_ms,
                                    status="completed",
                                    input=[{"role": "user", "content": "Find a source"}],
                                    output={"tool_calls": [{"name": "web_search"}]},
                                    attributes={"input_tokens": 1820, "output_tokens": 640},
                                    source_node_ids=(planner.node_id,),
                                ),
                                ToolCall(
                                    id="tool-search",
                                    name="web_search",
                                    tool_call_id="call-search-1",
                                    start_ms=tool.start_ms,
                                    duration_ms=tool.duration_ms,
                                    status="completed",
                                    input={"query": "example"},
                                    output={"matches": 1},
                                    source_node_ids=(tool.node_id,),
                                    agent_runs=(
                                        AgentRun(
                                            id="run-worker",
                                            name="research-worker",
                                            start_ms=1700000003650,
                                            duration_ms=1100,
                                            source_node_ids=(tool.node_id,),
                                            items=(
                                                AgentTurn(
                                                    id="turn-worker",
                                                    name="Research",
                                                    start_ms=1700000003650,
                                                    duration_ms=1100,
                                                    items=(
                                                        ModelCall(
                                                            id="model-worker",
                                                            name="worker-model",
                                                            start_ms=1700000003650,
                                                            duration_ms=1100,
                                                            model="model-alpha-seed-2",
                                                            status="completed",
                                                            source_node_ids=(tool.node_id,),
                                                        ),
                                                    ),
                                                ),
                                            ),
                                        ),
                                    ),
                                ),
                                Operation(
                                    id="compact-1",
                                    name="context.compact",
                                    start_ms=1700000004800,
                                    duration_ms=50,
                                    status="completed",
                                    input={"messages": 12},
                                    output={"messages": 4},
                                    source_node_ids=(agent.node_id,),
                                ),
                            ),
                        ),
                        Operation(
                            id="checkpoint-1",
                            name="framework.checkpoint",
                            start_ms=1700000004850,
                            duration_ms=25,
                            status="completed",
                            source_node_ids=(agent.node_id,),
                        ),
                        AgentTurn(
                            id="turn-answer",
                            name="Answer",
                            start_ms=1700000004875,
                            duration_ms=2525,
                            items=(
                                Operation(
                                    id="wrap-up-1",
                                    name="turn.wrap_up",
                                    start_ms=1700000004875,
                                    duration_ms=25,
                                    status="completed",
                                    source_node_ids=(agent.node_id,),
                                ),
                                ModelCall(
                                    id="model-answer",
                                    name="synthesizer",
                                    model="model-alpha-seed-2",
                                    start_ms=synth.start_ms,
                                    duration_ms=synth.duration_ms,
                                    status="error",
                                    input=[{"role": "user", "content": "Answer with the source"}],
                                    source_node_ids=(synth.node_id,),
                                ),
                            ),
                        ),
                        Operation(
                            id="finalize-1",
                            name="run.finalize",
                            start_ms=1700000007400,
                            duration_ms=600,
                            status="completed",
                            source_node_ids=(agent.node_id,),
                        ),
                    ),
                ),
            ),
        )


def _harness(extractor=None):
    return TraceHarness(
        TraceContributions(
            specs=tuple(genai.specs()),
            agent_run_extractor=extractor,
        )
    )


def test_agent_run_ir_matches_shared_conformance_case():
    harness = _harness(_FixtureAgentRunExtractor())
    context = harness.assemble(load_jaeger_file(RAW))

    actual = harness.extract_agent_runs(context)

    assert actual is not None
    assert agent_run_snapshot(actual) == json.loads(EXPECTED.read_text(encoding="utf-8"))


def test_interactive_agent_view_is_owned_by_agent_run_renderer():
    harness = _harness(_FixtureAgentRunExtractor())
    context = harness.assemble(load_jaeger_file(RAW))

    html = harness.render_interactive(context, harness.diagnose(context))

    assert 'data-perspective="agent"' in html
    assert "agent-run:run-main" in html
    assert "run.initialize" in html
    assert "context.load" in html
    assert "context.compact" in html
    assert "turn.wrap_up" in html
    assert "framework.checkpoint" in html
    assert "run.finalize" in html
    assert "tool-search" in html
    assert "agent-run:run-worker" in html
    assert "worker-model" in html
    assert "agentNameLayout(depth,rowHeight)" in html
    assert "compactName(n,nameLayout?nameLayout.budget:48)" in html
    assert "timeHeight(n.duration_ms,stackMaxDuration)" in html
    assert "const timedLeaf=perspective==='agent'&&!n.children.length" in html
    assert "maxLeafDuration(tree.roots)" in html
    assert "const base=22,max=base*4" in html
    assert ".row.agent-row::before" in html
    assert "row.classList.add('agent-row')" in html
    assert "row.style.setProperty('--node-color',KCOLOR[n.kind]||'#9ca3af')" in html
    assert "row.style.setProperty('--node-indent',(depth*16+4)+'px')" in html


def test_tool_display_details_prefer_file_and_command_over_call_id():
    harness = _harness()
    context = harness.assemble(load_jaeger_file(RAW))
    ir = AgentRunIR(
        trace_id=context.trace_id,
        runs=(
            AgentRun(
                id="run",
                name="run",
                start_ms=0,
                duration_ms=1,
                items=(
                    AgentTurn(
                        id="turn",
                        name="turn",
                        start_ms=0,
                        duration_ms=1,
                        items=(
                            ToolCall(
                                id="read",
                                name="read_file",
                                start_ms=0,
                                duration_ms=0,
                                tool_call_id="call-read",
                                input={"path": "/workspace/references/market.md"},
                            ),
                            ToolCall(
                                id="shell",
                                name="shell",
                                start_ms=0,
                                duration_ms=0,
                                tool_call_id="call-shell",
                                input={
                                    "command": (
                                        "cd /workspace/skills/demo && python3 "
                                        "references/analysis/scripts/stream_query.py "
                                        "--question example 2>&1"
                                    )
                                },
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )

    items = agent_run_roots(context, ir, {})[0]["children"][0]["children"]

    assert items[0]["name_variants"] == ["read_file · market.md", "market.md"]
    assert items[1]["name_variants"] == ["shell · stream_query.py", "stream_query.py", "shell"]
    assert items[0]["brief"] == ""
    assert items[1]["brief"] == ""
    assert items[0]["facts"]["tool_call_id"] == "call-read"
    assert items[1]["facts"]["tool_call_id"] == "call-shell"


def test_turn_source_fallback_includes_nested_operation_sources():
    harness = _harness()
    context = harness.assemble(load_jaeger_file(RAW))
    agent = next(node for node in context.nodes if node.name == "invoke_agent main")
    nested = Operation(
        id="nested",
        name="nested",
        start_ms=agent.start_ms,
        duration_ms=1,
        source_node_ids=(agent.node_id,),
    )
    ir = AgentRunIR(
        trace_id=context.trace_id,
        runs=(
            AgentRun(
                id="run",
                name="run",
                start_ms=agent.start_ms,
                duration_ms=1,
                items=(
                    AgentTurn(
                        id="turn",
                        name="turn",
                        start_ms=agent.start_ms,
                        duration_ms=1,
                        items=(
                            Operation(
                                id="outer",
                                name="outer",
                                start_ms=agent.start_ms,
                                duration_ms=1,
                                operations=(nested,),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )

    turn = agent_run_roots(context, ir, {})[0]["children"][0]

    assert turn["facts"]["source_nodes"] == agent.node_id
    assert turn["span_ids"] == list(agent.span_ids)
    assert turn["children"][0]["collapsed"] is True


def test_operation_with_error_descendant_stays_expanded():
    harness = _harness()
    context = harness.assemble(load_jaeger_file(RAW))
    ir = AgentRunIR(
        trace_id=context.trace_id,
        runs=(
            AgentRun(
                id="run",
                name="run",
                start_ms=0,
                duration_ms=1,
                items=(
                    Operation(
                        id="outer",
                        name="outer",
                        start_ms=0,
                        duration_ms=1,
                        operations=(
                            Operation(
                                id="failed",
                                name="failed",
                                start_ms=0,
                                duration_ms=1,
                                status="error",
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )

    operation = agent_run_roots(context, ir, {})[0]["children"][0]

    assert operation["collapsed"] is False


def test_interactive_hides_agent_view_without_an_extractor():
    harness = _harness()
    context = harness.assemble(load_jaeger_file(RAW))

    assert 'data-perspective="agent"' not in harness.render_interactive(context)


def test_extractor_output_rejects_unknown_node_references():
    class BrokenExtractor:
        def extract(self, context):
            return AgentRunIR(
                trace_id=context.trace_id,
                runs=(
                    AgentRun(
                        id="broken",
                        name="broken",
                        start_ms=0,
                        duration_ms=0,
                        source_node_ids=("missing-node",),
                        items=(),
                    ),
                ),
            )

    harness = _harness(BrokenExtractor())
    context = harness.assemble(load_jaeger_file(RAW))

    with pytest.raises(ValueError, match="unknown node IDs"):
        harness.extract_agent_runs(context)


def test_extractor_output_rejects_items_outside_parent_time_window():
    class BrokenExtractor:
        def extract(self, context):
            return AgentRunIR(
                trace_id=context.trace_id,
                runs=(
                    AgentRun(
                        id="broken",
                        name="broken",
                        start_ms=100,
                        duration_ms=100,
                        items=(
                            Operation(
                                id="outside",
                                name="outside",
                                start_ms=50,
                                duration_ms=10,
                            ),
                        ),
                    ),
                ),
            )

    harness = _harness(BrokenExtractor())
    context = harness.assemble(load_jaeger_file(RAW))

    with pytest.raises(ValueError, match="outside AgentRun broken time window"):
        harness.extract_agent_runs(context)


def test_extractor_output_rejects_nested_operations_outside_parent_time_window():
    class BrokenExtractor:
        def extract(self, context):
            return AgentRunIR(
                trace_id=context.trace_id,
                runs=(
                    AgentRun(
                        id="broken",
                        name="broken",
                        start_ms=0,
                        duration_ms=300,
                        items=(
                            Operation(
                                id="outer",
                                name="outer",
                                start_ms=100,
                                duration_ms=100,
                                operations=(
                                    Operation(
                                        id="nested-outside",
                                        name="nested-outside",
                                        start_ms=50,
                                        duration_ms=10,
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            )

    harness = _harness(BrokenExtractor())
    context = harness.assemble(load_jaeger_file(RAW))

    with pytest.raises(ValueError, match="outside Operation outer time window"):
        harness.extract_agent_runs(context)
