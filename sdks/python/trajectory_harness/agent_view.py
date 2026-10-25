"""Project ATIF evidence into Agent Ledger View without reconstructing model prompts."""

from __future__ import annotations

from collections.abc import Sequence
from agent_ledger.common import CallError, CallRequest, CallResponse, Model, Usage
from agent_ledger.view import (
    Document,
    Input,
    ModelCall,
    Operation,
    Run,
    SourceRef,
    ToolCall,
)
from spec_case import link, spec
from atif import Trajectory
from trajectory_harness.model import (
    step_failure,
    step_input_messages,
    step_name,
    step_operation,
    step_status,
    trajectory_execution,
)


@spec(
    "Project recorded requests and executed tools; never reconstruct missing calls or turns"
)
@link("repo://docs/agent-view.md")
def agent_view(trajectories: Sequence[Trajectory]) -> Document:
    """One observed ATIF trajectory becomes one Run; source Step IDs remain evidence."""
    runs = []
    issues = []
    for trajectory in trajectories:
        identity = trajectory.trajectory_id or trajectory.session_id
        run_id = f"atif:{identity}"
        items = []
        user_inputs = 0
        for step in trajectory.steps:
            native = step.model_dump(mode="json", exclude_none=True)
            base = dict(
                id=f"{run_id}:step:{step.step_id}",
                name=step_name(step),
                evidence=[
                    SourceRef(
                        source="atif-step", id=str(step.step_id), uri=f"atif:{identity}"
                    )
                ],
                attributes={"atif": native},
            )
            status = step_status(step)
            status = {
                "ok": "completed",
                "success": "completed",
                "error": "failed",
                "failure": "failed",
                "canceled": "cancelled",
                "timeout": "failed",
            }.get(status, status)
            if status in {
                "running",
                "completed",
                "failed",
                "cancelled",
                "outcome_unknown",
            }:
                base["status"] = status
            if step.source == "user":
                items.append(
                    Input(
                        **base,
                        mode="initial" if user_inputs == 0 else "followup",
                        content=native.get("message"),
                    )
                )
                user_inputs += 1
                continue
            if step.source != "agent":
                items.append(
                    Operation(
                        **base, operation="system_context", data=native.get("message")
                    )
                )
                continue
            model_id = step.model_name
            metrics = native.get("metrics") or {}
            usage = {
                target: metrics[source]
                for source, target in (
                    ("prompt_tokens", "input_tokens"),
                    ("completion_tokens", "output_tokens"),
                    ("cached_tokens", "cache_read_input_tokens"),
                )
                if metrics.get(source) is not None
            }
            failure = step_failure(step)
            response = CallResponse(
                output={
                    "message": native.get("message"),
                    "tool_calls": native.get("tool_calls", []),
                },
                model=Model(id=model_id) if model_id else None,
                usage=Usage(**usage) if usage else None,
                error=CallError(
                    type=failure.error_type or failure.kind, message=failure.message
                )
                if failure
                else None,
            )
            model_call_id = None
            # ATIF may aggregate multiple calls. Do not manufacture one physical call per aggregate.
            if step.llm_call_count == 1:
                actual_input = step_input_messages(step)
                request = (
                    CallRequest(input={"messages": list(actual_input)})
                    if actual_input
                    else None
                )
                if request is None:
                    issues.append(f"{base['id']}: exact model request unavailable")
                items.append(ModelCall(**base, request=request, response=response))
                model_call_id = base["id"]
            else:
                items.append(
                    Operation(**base, operation=step_operation(step), data=native)
                )
                if step.llm_call_count and step.llm_call_count > 1:
                    issues.append(
                        f"{base['id']}: ATIF aggregates {step.llm_call_count} model calls"
                    )
            results = list(step.observation.results) if step.observation else []
            for index, call in enumerate(step.tool_calls or []):
                observed = [
                    r.model_dump(mode="json", exclude_none=True)
                    for r in results
                    if r.source_call_id == call.tool_call_id
                ]
                facts = dict(
                    id=f"{base['id']}:tool:{index}:{call.tool_call_id}",
                    name=call.function_name,
                    evidence=base["evidence"],
                    attributes={
                        "atif_tool_call": call.model_dump(
                            mode="json", exclude_none=True
                        )
                    },
                )
                if not observed:
                    items.append(
                        Operation(
                            **facts,
                            operation="tool_proposal",
                            data=call.model_dump(mode="json", exclude_none=True),
                        )
                    )
                    issues.append(f"{facts['id']}: tool execution not observed")
                    continue
                items.append(
                    ToolCall(
                        **facts,
                        tool_call_id=call.tool_call_id,
                        model_call_id=model_call_id,
                        request=CallRequest(
                            tool_name=call.function_name, input=call.arguments
                        ),
                        response=CallResponse(output=observed),
                    )
                )
        execution = trajectory_execution(trajectory)
        run_status = (
            {
                "success": "completed",
                "error": "failed",
                "failure": "failed",
                "canceled": "cancelled",
                "timeout": "failed",
            }.get(execution.outcome, execution.outcome)
            if execution
            else "unknown"
        )
        if run_status not in {
            "unknown",
            "running",
            "completed",
            "failed",
            "cancelled",
            "outcome_unknown",
        }:
            run_status = "unknown"
        runs.append(
            Run(
                id=run_id,
                session_id=trajectory.session_id,
                name=trajectory.agent.name,
                status=run_status,
                items=items,
                attributes={
                    "atif_agent": trajectory.agent.model_dump(
                        mode="json", exclude_none=True
                    ),
                    "atif_extra": trajectory.extra or {},
                },
            )
        )
    return Document(runs=runs, issues=issues)
