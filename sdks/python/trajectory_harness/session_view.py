"""Project ATIF evidence into Agent Ledger View without reconstructing model prompts."""

from __future__ import annotations

from collections.abc import Sequence
from agent_ledger.common import (
    CallError,
    CallRequest,
    CallResponse,
    Model,
    Usage,
    SessionRef,
)
from agent_ledger.observation import (
    SessionView,
    Input,
    ModelCall,
    Operation,
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
@link("repo://docs/session-view.md")
def session_views(trajectories: Sequence[Trajectory]) -> tuple[SessionView, ...]:
    """Group only explicitly declared ATIF Sessions; retain source Step evidence."""
    grouped = {}
    for trajectory in trajectories:
        if not trajectory.session_id:
            continue
        ref = SessionRef(source="atif", id=trajectory.session_id)
        key = (ref.source, ref.id)
        view = grouped.setdefault(key, SessionView(session=ref, nodes=[]))
        issues = view.issues
        identity = trajectory.trajectory_id or trajectory.session_id
        run_id = f"atif:{identity}"
        nodes = []
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
                nodes.append(
                    Input(
                        **base,
                        content=native.get("message"),
                    )
                )
                continue
            if step.source != "agent":
                nodes.append(
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
                nodes.append(ModelCall(**base, request=request, response=response))
                model_call_id = base["id"]
            else:
                nodes.append(
                    Operation(**base, operation=step_operation(step), data=native)
                )
                if step.llm_call_count and step.llm_call_count > 1:
                    issues.append(
                        f"{base['id']}: ATIF aggregates {step.llm_call_count} model calls"
                    )
            results = list(step.observation.results) if step.observation else []
            calls = list(step.tool_calls or [])
            matched: dict[int, list[int]] = {}
            used: set[int] = set()
            for result_index, result in enumerate(results):
                candidates = [
                    i
                    for i, call in enumerate(calls)
                    if call.tool_call_id and call.tool_call_id == result.source_call_id
                ]
                if len(candidates) == 1:
                    matched.setdefault(candidates[0], []).append(result_index)
                    used.add(result_index)
            # A single call/result in one source step is unambiguous unless explicit IDs conflict.
            singleton = (
                len(calls) == len(results) == 1
                and not used
                and (not calls[0].tool_call_id or not results[0].source_call_id)
            )
            if singleton:
                matched[0] = [0]
                used.add(0)
            for index, call in enumerate(calls):
                observed = [
                    results[i].model_dump(mode="json", exclude_none=True)
                    for i in matched.get(index, [])
                ]
                facts = dict(
                    id=f"{base['id']}:tool:{index}",
                    name=call.function_name,
                    evidence=base["evidence"],
                    attributes={
                        "atif_tool_call": call.model_dump(
                            mode="json", exclude_none=True
                        )
                    },
                )
                if not observed:
                    nodes.append(
                        Operation(
                            **facts,
                            operation="tool_proposal",
                            data=call.model_dump(mode="json", exclude_none=True),
                        )
                    )
                    issues.append(
                        f"{facts['id']}: tool result association unavailable"
                        if results
                        else f"{facts['id']}: tool execution not observed"
                    )
                    continue
                if singleton:
                    facts["attributes"]["association"] = "single-call-step"
                nodes.append(
                    ToolCall(
                        **facts,
                        tool_call_id=call.tool_call_id or None,
                        model_call_id=model_call_id,
                        request=CallRequest(
                            tool_name=call.function_name, input=call.arguments
                        ),
                        response=CallResponse(output=observed),
                    )
                )
            # Unmatched results are evidence of an outcome, even when request ownership is unknown.
            for index, result in enumerate(results):
                if index in used:
                    continue
                nodes.append(
                    ToolCall(
                        id=f"{base['id']}:result:{index}",
                        evidence=base["evidence"],
                        tool_call_id=result.source_call_id or None,
                        model_call_id=model_call_id,
                        response=CallResponse(
                            output=[result.model_dump(mode="json", exclude_none=True)]
                        ),
                    )
                )
                issues.append(
                    f"{base['id']}:result:{index}: tool request association unavailable"
                )
        execution = trajectory_execution(trajectory)
        view.nodes.extend(nodes)
        view.attributes.setdefault("atif_trajectories", {})[identity] = {
            "agent": trajectory.agent.model_dump(mode="json", exclude_none=True),
            "extra": trajectory.extra or {},
            "execution": execution.to_dict() if execution else None,
        }
    return tuple(
        SessionView.model_validate(view.model_dump(mode="json")).model_copy(deep=True)
        for view in grouped.values()
    )
