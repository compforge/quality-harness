from __future__ import annotations

import json

from trajectory_harness.loaders.otel_json import OTelJsonLoader
from trajectory_harness.model import (
    step_attributes,
    step_duration_ms,
    step_failure,
    step_name,
    step_parent_id,
    step_status,
)


def _attr(key, value):
    if isinstance(value, int):
        wrapped = {"intValue": str(value)}
    else:
        wrapped = {"stringValue": value}
    return {"key": key, "value": wrapped}


def test_loads_ordered_genai_steps_and_synthesizes_tool_messages(tmp_path):
    output_messages = [
        {
            "role": "assistant",
            "parts": [
                {
                    "type": "tool_call",
                    "id": "call-1",
                    "name": "file_read",
                    "arguments": {"path": "a.go"},
                }
            ],
            "finish_reason": "tool_calls",
        }
    ]
    payload = {
        "resourceSpans": [
            {
                "resource": {"attributes": [_attr("service.name", "reviewer")]},
                "scopeSpans": [
                    {
                        "scope": {"name": "agentgo", "version": "0.1.0"},
                        "spans": [
                            {
                                "traceId": "trace-1",
                                "spanId": "model-1",
                                "name": "chat model-a",
                                "startTimeUnixNano": "1000000",
                                "endTimeUnixNano": "3000000",
                                "attributes": [
                                    _attr("gen_ai.operation.name", "chat"),
                                    _attr(
                                        "gen_ai.output.messages",
                                        json.dumps(output_messages),
                                    ),
                                ],
                            },
                            {
                                "traceId": "trace-1",
                                "spanId": "tool-1",
                                "parentSpanId": "model-1",
                                "name": "execute_tool file_read",
                                "startTimeUnixNano": "4000000",
                                "endTimeUnixNano": "7000000",
                                "attributes": [
                                    _attr("gen_ai.operation.name", "execute_tool"),
                                    _attr("gen_ai.tool.name", "file_read"),
                                    _attr("gen_ai.tool.call.id", "call-1"),
                                    _attr(
                                        "gen_ai.tool.call.arguments",
                                        json.dumps({"path": "a.go"}),
                                    ),
                                    _attr("gen_ai.tool.call.result", "package a"),
                                ],
                                "status": {"code": "STATUS_CODE_OK"},
                            },
                        ],
                    }
                ],
            }
        ]
    }
    path = tmp_path / "trace.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    trajectories = OTelJsonLoader().load(path)

    assert len(trajectories) == 1
    trajectory = trajectories[0]
    assert trajectory.trajectory_id == "trace-1"
    assert [step.step_id for step in trajectory.steps] == [1, 2]
    tool = trajectory.steps[1]
    assert step_parent_id(tool) == "model-1"
    assert step_name(tool) == "file_read"
    assert step_status(tool) == "ok"
    assert step_duration_ms(tool) == 3
    assert tool.tool_calls[0].arguments == {"path": "a.go"}
    assert tool.observation.results[0].content == "package a"
    assert step_attributes(tool)["service.name"] == "reviewer"
    assert step_attributes(tool)["otel.scope.name"] == "agentgo"


def test_promotes_message_attributes_from_events(tmp_path):
    messages = [{"role": "assistant", "parts": [{"type": "text", "content": "done"}]}]
    path = tmp_path / "trace.jsonl"
    path.write_text(
        json.dumps(
            {
                "traceId": "trace-1",
                "spanId": "model-1",
                "name": "chat",
                "startTimeUnixNano": "0",
                "endTimeUnixNano": "1",
                "attributes": [_attr("gen_ai.operation.name", "chat")],
                "events": [
                    {
                        "attributes": [
                            _attr("gen_ai.output.messages", json.dumps(messages))
                        ]
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    trajectory = OTelJsonLoader().load(path)[0]

    assert trajectory.steps[0].message == "done"


def test_preserves_nested_agent_workflow_step_tree(tmp_path):
    spans = [
        {
            "traceId": "trace-1",
            "spanId": "workflow-1",
            "name": "unit review",
            "startTimeUnixNano": "0",
            "endTimeUnixNano": "10000000",
            "attributes": [
                _attr("gen_ai.operation.name", "invoke_workflow"),
                _attr("gen_ai.workflow.name", "unit_review"),
            ],
        },
        {
            "traceId": "trace-1",
            "spanId": "planner",
            "parentSpanId": "workflow-1",
            "name": "plan",
            "startTimeUnixNano": "1000000",
            "endTimeUnixNano": "4000000",
            "attributes": [
                _attr("gen_ai.operation.name", "invoke_agent"),
                _attr("gen_ai.agent.name", "planner"),
            ],
        },
        {
            "traceId": "trace-1",
            "spanId": "planner-model",
            "parentSpanId": "planner",
            "name": "chat",
            "startTimeUnixNano": "2000000",
            "endTimeUnixNano": "3000000",
            "attributes": [_attr("gen_ai.operation.name", "chat")],
        },
        {
            "traceId": "trace-1",
            "spanId": "executor",
            "parentSpanId": "workflow-1",
            "name": "execute",
            "startTimeUnixNano": "5000000",
            "endTimeUnixNano": "9000000",
            "attributes": [
                _attr("gen_ai.operation.name", "invoke_agent"),
                _attr("gen_ai.agent.name", "executor"),
            ],
        },
        {
            "traceId": "trace-1",
            "spanId": "executor-model",
            "parentSpanId": "executor",
            "name": "chat",
            "startTimeUnixNano": "6000000",
            "endTimeUnixNano": "8000000",
            "attributes": [_attr("gen_ai.operation.name", "chat")],
        },
    ]
    path = tmp_path / "trace.jsonl"
    path.write_text(
        "\n".join(json.dumps(span) for span in spans) + "\n", encoding="utf-8"
    )

    trajectory = OTelJsonLoader().load(path)[0]
    steps = {step_attributes(step)["otel.span_id"]: step for step in trajectory.steps}

    assert len(steps) == 5
    assert "workflow-1" in steps
    assert step_name(steps["workflow-1"]) == "unit_review"
    assert step_parent_id(steps["planner"]) == "workflow-1"
    assert step_parent_id(steps["planner-model"]) == "planner"
    assert step_parent_id(steps["executor"]) == "workflow-1"
    assert step_parent_id(steps["executor-model"]) == "executor"
    assert step_name(steps["planner"]) == "planner"
    assert step_name(steps["executor"]) == "executor"


def test_normalizes_otel_error_type_on_the_failed_operation(tmp_path):
    path = tmp_path / "trace.jsonl"
    path.write_text(
        json.dumps(
            {
                "traceId": "trace-1",
                "spanId": "model-1",
                "name": "chat",
                "startTimeUnixNano": "0",
                "endTimeUnixNano": "1",
                "attributes": [
                    _attr("gen_ai.operation.name", "chat"),
                    _attr("error.type", "timeout"),
                ],
                "status": {
                    "code": "STATUS_CODE_ERROR",
                    "message": "provider deadline exceeded",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    step = OTelJsonLoader().load(path)[0].steps[0]

    failure = step_failure(step)
    assert failure is not None
    assert failure.key == "llm.request.timeout"
    assert failure.message == "provider deadline exceeded"
