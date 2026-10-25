"""Export prepared trace agent evidence using the shared Agent Ledger View contract."""

from __future__ import annotations

from datetime import datetime, timezone

from agent_ledger.common import CallRequest, CallResponse
from agent_ledger.view import Document, ModelCall, Operation, Run, SourceRef, ToolCall, Turn
from spec_case import link, spec

from trace_harness.model import agent as trace
from trace_harness.model.context import TraceContext


@spec("Export only observed trace facts; preserve source identities and unknown values")
@link("repo://docs/agent-view.md")
def agent_view(ir: trace.AgentRunIR, context: TraceContext) -> Document:
    """Project existing evidence only; never fetch spans or infer agent turns."""
    trace.validate_agent_run_ir(ir, context)

    def base(node, kind):
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
        return facts

    def item(node):
        if isinstance(node, trace.AgentRun):
            return Run(**base(node, "run"), items=[item(x) for x in node.items])
        if isinstance(node, trace.AgentTurn):
            return Turn(**base(node, "turn"), items=[item(x) for x in node.items])
        if isinstance(node, trace.Operation):
            return Operation(
                **base(node, "operation"),
                operation=node.name or "operation",
                data={"input": node.input, "output": node.output},
                items=[item(x) for x in (*node.operations, *node.agent_runs)],
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
            runs=[item(x) for x in node.agent_runs],
        )

    return Document(runs=[item(run) for run in ir.runs])
