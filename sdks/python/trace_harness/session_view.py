"""Project explicitly attributed trace evidence into per-Session observations."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from datetime import datetime, timezone
from typing import Any

from agent_ledger.common import CallRequest, CallResponse, SessionRef
from agent_ledger.observation import (
    Item,
    ModelCall,
    Operation,
    SessionView,
    SourceRef,
    ToolCall,
    Turn,
)
from spec_case import link, spec

from trace_harness.model import agent as trace
from trace_harness.model.context import TraceContext


@spec("Export only observed trace facts; preserve source identities and unknown values")
@link("repo://docs/session-view.md")
def session_views(ir: trace.AgentRunIR, context: TraceContext) -> tuple[SessionView, ...]:
    """Project existing evidence only; never fetch spans or infer agent turns."""
    trace.validate_agent_run_ir(ir, context)

    def base(node: trace.AgentRun | trace.AgentTurn | trace.TurnItem, kind: str) -> dict[str, Any]:
        raw_status = node.status.lower()
        status = {
            "ok": "completed",
            "success": "completed",
            "error": "failed",
            "failure": "failed",
        }.get(raw_status, raw_status)
        facts = dict(
            id=f"{ir.trace_id}:{kind}:{node.id}",
            name=node.name or node.id,
            evidence=[
                SourceRef(source="trace-node", id=value, uri=f"trace:{ir.trace_id}")
                for value in node.source_node_ids
            ],
            attributes={**node.attributes, "trace_id": ir.trace_id},
        )
        if status in {"running", "completed", "failed", "cancelled", "outcome_unknown"}:
            facts["status"] = status
        elif raw_status:
            facts["attributes"]["source_status"] = node.status
        # Zero is the ingestion sentinel for unavailable timestamps.
        if node.start_ms > 0:
            facts["started_at"] = (
                datetime.fromtimestamp(node.start_ms / 1000, timezone.utc)
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z")
            )
            facts["finished_at"] = (
                datetime.fromtimestamp((node.start_ms + node.duration_ms) / 1000, timezone.utc)
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z")
            )
        facts["attributes"]["trace_timing"] = {
            "start_ms": node.start_ms,
            "duration_ms": node.duration_ms,
        }
        if isinstance(node, (trace.ToolCall, trace.Operation)) and node.agent_runs:
            facts["attributes"]["trace_child_runs"] = [
                {"id": run.id, "session": run.session.model_dump() if run.session else None}
                for run in node.agent_runs
            ]
        return facts

    def item(node: trace.AgentTurn | trace.TurnItem, owner: SessionRef) -> Item:
        if isinstance(node, trace.AgentTurn):
            return Turn(**base(node, "turn"), items=[item(x, owner) for x in node.items])
        if isinstance(node, trace.Operation):
            return Operation(
                **base(node, "operation"),
                operation=node.name or "operation",
                data={"input": node.input, "output": node.output},
                items=[item(x, owner) for x in node.operations],
            )
        request = {}
        response = {}
        # The trace IR uses None as absence and cannot prove an explicit JSON null.
        if node.input is not None:
            request["input"] = node.input
        if node.output is not None:
            response["output"] = node.output
        if isinstance(node, trace.ModelCall):
            facts = base(node, "model_call")
            if node.model:
                facts["attributes"]["trace_model"] = node.model
            return ModelCall(
                **facts,
                request=CallRequest(**request) if request else None,
                response=CallResponse(**response) if response else None,
            )
        request["tool_name"] = node.name
        return ToolCall(
            **base(node, "tool_call"),
            tool_call_id=node.tool_call_id,
            request=CallRequest(**request),
            response=CallResponse(**response) if response else None,
            sessions=[
                run.session
                for run in node.agent_runs
                if run.session is not None and run.session != owner
            ],
        )

    grouped: dict[tuple[str, str], SessionView] = {}
    for run in observed_runs(ir):
        if run.session is None:
            continue
        key = (run.session.source, run.session.id)
        view = grouped.setdefault(
            key, SessionView(session=run.session.model_copy(deep=True), items=[])
        )
        group = Operation(
            **base(run, "run"),
            operation="trace.run",
            items=[item(x, run.session) for x in run.items],
        )
        group.attributes["run_id"] = run.id
        view.items.append(group)
    return tuple(
        SessionView.model_validate(view.model_dump(mode="json")).model_copy(deep=True)
        for view in grouped.values()
    )


def observed_runs(ir: trace.AgentRunIR) -> Iterator[trace.AgentRun]:
    """Walk source containment without assigning or inheriting Session identities."""

    def children(items: Iterable[trace.AgentTurn | trace.TurnItem]) -> Iterator[trace.AgentRun]:
        for node in items:
            if isinstance(node, trace.AgentTurn):
                yield from children(node.items)
            elif isinstance(node, trace.Operation):
                yield from children(node.operations)
                for run in node.agent_runs:
                    yield from visit(run)
            elif isinstance(node, trace.ToolCall):
                for run in node.agent_runs:
                    yield from visit(run)

    def visit(run: trace.AgentRun) -> Iterator[trace.AgentRun]:
        yield run
        yield from children(run.items)

    for run in ir.runs:
        yield from visit(run)
