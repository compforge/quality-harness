"""OTLP/JSON + OTel GenAI semantic-convention trajectory loader."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from atif import (
    Agent,
    Metrics,
    Observation,
    ObservationResult,
    Step,
    ToolCall,
    Trajectory,
)

from trajectory_harness.model import (
    Failure,
    FailureKind,
    make_atif_step,
    make_atif_trajectory,
    require_trajectory_id,
    step_attributes,
    step_start_ms,
)

_INPUT_MESSAGES = "gen_ai.input.messages"
_OUTPUT_MESSAGES = "gen_ai.output.messages"
_OPERATION = "gen_ai.operation.name"
_TOOL_NAME = "gen_ai.tool.name"
_TOOL_ID = "gen_ai.tool.call.id"
_TOOL_ARGUMENTS = "gen_ai.tool.call.arguments"
_TOOL_RESULT = "gen_ai.tool.call.result"
_ERROR_TYPE = "error.type"
_EXCEPTION_TYPE = "exception.type"
_EXCEPTION_MESSAGE = "exception.message"


class OTelJsonLoader:
    """Load OTel GenAI spans from OTLP JSON, Tempo wrappers, or flat JSONL."""

    def load(self, source: str | Path) -> list[Trajectory]:
        path = Path(source)
        text = path.read_text(encoding="utf-8").strip()
        return self.loads(text, source=str(path))

    def loads(self, text: str, *, source: str = "") -> list[Trajectory]:
        """Load trajectories from in-memory recording content."""

        text = text.strip()
        if not text:
            return []

        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            value = [json.loads(line) for line in text.splitlines() if line.strip()]

        spans = self._spans(value)
        by_trace: dict[str, list[Step]] = defaultdict(list)
        for index, (raw, resource, scope) in enumerate(spans, start=1):
            step = self._step(raw, resource, scope, step_id=index)
            if step is not None:
                by_trace[str(raw.get("traceId") or raw.get("trace_id") or "")].append(
                    step
                )

        trajectories = []
        for trace_id, steps in by_trace.items():
            steps.sort(key=lambda step: (step_start_ms(step), step.step_id))
            steps = [
                step.model_copy(update={"step_id": index})
                for index, step in enumerate(steps, start=1)
            ]
            trajectories.append(
                make_atif_trajectory(
                    trajectory_id=trace_id,
                    agent=_agent(steps),
                    steps=steps,
                    source=source,
                    metadata={"format": "otel-genai"},
                )
            )
        trajectories.sort(key=require_trajectory_id)
        return trajectories

    def _spans(
        self, value: Any
    ) -> list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]]:
        if isinstance(value, list):
            return [(span, {}, {}) for span in value if isinstance(span, dict)]
        if not isinstance(value, dict):
            raise ValueError("OTel JSON must be an object or a JSONL sequence of spans")

        inner = value.get("trace")
        if isinstance(inner, dict):
            value = inner
        resource_spans = value.get("resourceSpans") or value.get("batches")
        if resource_spans is None:
            return [(value, {}, {})]

        result = []
        for resource_span in resource_spans:
            resource = _attributes(
                resource_span.get("resource", {}).get("attributes", [])
            )
            scope_spans = resource_span.get("scopeSpans") or resource_span.get(
                "instrumentationLibrarySpans", []
            )
            for scope_span in scope_spans:
                scope = (
                    scope_span.get("scope")
                    or scope_span.get("instrumentationLibrary")
                    or {}
                )
                for span in scope_span.get("spans", []):
                    result.append((span, resource, scope))
        return result

    def _step(
        self,
        raw: dict[str, Any],
        resource: dict[str, Any],
        scope: dict[str, Any],
        *,
        step_id: int,
    ) -> Step | None:
        attrs = _attributes(raw.get("attributes", []))
        _promote_message_events(raw, attrs)
        _promote_exception_events(raw, attrs)
        attrs = {**resource, **attrs}

        operation = str(attrs.get(_OPERATION) or "")
        if not operation:
            if attrs.get(_TOOL_NAME):
                operation = "execute_tool"
            elif attrs.get(_INPUT_MESSAGES) or attrs.get(_OUTPUT_MESSAGES):
                operation = "inference"
            else:
                return None

        input_messages = _messages(attrs.pop(_INPUT_MESSAGES, None))
        output_messages = _messages(attrs.pop(_OUTPUT_MESSAGES, None))
        if operation == "execute_tool":
            input_messages, output_messages = _tool_messages(
                attrs, input_messages, output_messages
            )

        if scope.get("name"):
            attrs.setdefault("otel.scope.name", scope["name"])
        if scope.get("version"):
            attrs.setdefault("otel.scope.version", scope["version"])
        span_id = str(raw.get("spanId") or raw.get("span_id") or "")
        parent_span_id = str(raw.get("parentSpanId") or raw.get("parent_span_id") or "")
        if span_id:
            attrs.setdefault("otel.span_id", span_id)
        if parent_span_id:
            attrs.setdefault("otel.parent_span_id", parent_span_id)

        start_ns = int(
            raw.get("startTimeUnixNano") or raw.get("start_time_unix_nano") or 0
        )
        end_ns = int(
            raw.get("endTimeUnixNano") or raw.get("end_time_unix_nano") or start_ns
        )
        status = _status(raw, attrs)
        tool_calls = _atif_tool_calls(input_messages, attrs)
        observation = _atif_observation(output_messages, attrs)
        model_operation = operation in {
            "inference",
            "chat",
            "generate_content",
            "text_completion",
            "embeddings",
        }
        return make_atif_step(
            step_id=step_id,
            parent_step_id=(parent_span_id or None),
            operation=operation,
            name=_step_name(raw, attrs, operation),
            message=_step_message(output_messages, operation, attrs),
            timestamp=_timestamp(start_ns),
            start_ms=start_ns / 1_000_000,
            duration_ms=max(0, end_ns - start_ns) / 1_000_000,
            status=status,
            failure=_failure(raw, attrs, operation) if status == "error" else None,
            input_messages=input_messages,
            output_messages=output_messages,
            attributes=attrs,
            model_name=(
                str(
                    attrs.get("gen_ai.response.model")
                    or attrs.get("gen_ai.request.model")
                )
                if model_operation
                and (
                    attrs.get("gen_ai.response.model")
                    or attrs.get("gen_ai.request.model")
                )
                else None
            ),
            tool_calls=tool_calls or None,
            observation=observation,
            metrics=_metrics(attrs) if model_operation else None,
            llm_call_count=1 if model_operation else 0,
        )


def _attributes(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return _flatten(value)
    result = {}
    for item in value or []:
        if not isinstance(item, dict) or not item.get("key"):
            continue
        result[item["key"]] = _any_value(item.get("value", {}))
    return result


def _any_value(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    for key in ("stringValue", "intValue", "doubleValue", "boolValue", "bytesValue"):
        if key in value:
            raw = value[key]
            return int(raw) if key == "intValue" else raw
    if "arrayValue" in value:
        values = value["arrayValue"].get("values", [])
        return [_any_value(item) for item in values]
    if "kvlistValue" in value:
        values = value["kvlistValue"].get("values", [])
        return {item["key"]: _any_value(item.get("value", {})) for item in values}
    return value


def _flatten(value: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    result = {}
    for key, item in value.items():
        name = f"{prefix}.{key}" if prefix else key
        if isinstance(item, dict):
            result.update(_flatten(item, name))
        else:
            result[name] = item
    return result


def _promote_message_events(raw: dict[str, Any], attrs: dict[str, Any]) -> None:
    for event in raw.get("events", []):
        event_attrs = _attributes(event.get("attributes", []))
        for key in (_INPUT_MESSAGES, _OUTPUT_MESSAGES):
            if key in event_attrs and key not in attrs:
                attrs[key] = event_attrs[key]


def _promote_exception_events(raw: dict[str, Any], attrs: dict[str, Any]) -> None:
    for event in raw.get("events", []):
        if event.get("name") != "exception":
            continue
        event_attrs = _attributes(event.get("attributes", []))
        for key in (_EXCEPTION_TYPE, _EXCEPTION_MESSAGE):
            if key in event_attrs and key not in attrs:
                attrs[key] = event_attrs[key]


def _messages(raw: Any) -> tuple[dict[str, Any], ...]:
    if raw in (None, ""):
        return ()
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return ()
    if not isinstance(raw, list):
        return ()
    return tuple(_message(message) for message in raw if isinstance(message, dict))


def _message(message: dict[str, Any]) -> dict[str, Any]:
    if isinstance(message.get("parts"), list):
        return dict(message)

    parts = []
    content = message.get("content")
    if message.get("role") != "tool" and isinstance(content, str) and content:
        parts.append({"type": "text", "content": content})
    for call in message.get("tool_calls") or []:
        function = call.get("function", call)
        parts.append(
            {
                "type": "tool_call",
                "id": call.get("id"),
                "name": function.get("name", ""),
                "arguments": _json_or_value(function.get("arguments")),
            }
        )
    if message.get("role") == "tool":
        parts.append(
            {
                "type": "tool_call_response",
                "id": message.get("tool_call_id"),
                "response": content,
            }
        )
    result = {"role": message.get("role", ""), "parts": parts}
    if message.get("name") is not None:
        result["name"] = message["name"]
    if message.get("finish_reason") is not None:
        result["finish_reason"] = message["finish_reason"]
    return result


def _tool_messages(
    attrs: dict[str, Any],
    input_messages: tuple[dict[str, Any], ...],
    output_messages: tuple[dict[str, Any], ...],
) -> tuple[tuple[dict[str, Any], ...], tuple[dict[str, Any], ...]]:
    name = str(attrs.get(_TOOL_NAME) or "")
    call_id = attrs.get(_TOOL_ID)
    if not input_messages:
        input_messages = (
            {
                "role": "assistant",
                "parts": [
                    {
                        "type": "tool_call",
                        "id": call_id,
                        "name": name,
                        "arguments": _json_or_value(attrs.get(_TOOL_ARGUMENTS)),
                    }
                ],
            },
        )
    if not output_messages and _TOOL_RESULT in attrs:
        output_messages = (
            {
                "role": "tool",
                "parts": [
                    {
                        "type": "tool_call_response",
                        "id": call_id,
                        "response": _json_or_value(attrs.get(_TOOL_RESULT)),
                    }
                ],
            },
        )
    return input_messages, output_messages


def _json_or_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _agent(steps: list[Step]) -> Agent:
    attributes = [step_attributes(step) for step in steps]
    name = next(
        (
            str(value)
            for attrs in attributes
            for value in (attrs.get("gen_ai.agent.name"), attrs.get("service.name"))
            if value
        ),
        "unknown",
    )
    version = next(
        (
            str(attrs["service.version"])
            for attrs in attributes
            if attrs.get("service.version")
        ),
        "unknown",
    )
    model_name = next((step.model_name for step in steps if step.model_name), None)
    return Agent(name=name, version=version, model_name=model_name)


def _timestamp(start_ns: int) -> str | None:
    if not start_ns:
        return None
    return datetime.fromtimestamp(start_ns / 1_000_000_000, tz=timezone.utc).isoformat()


def _step_message(
    output_messages: tuple[dict[str, Any], ...],
    operation: str,
    attrs: dict[str, Any],
) -> str:
    chunks = []
    for message in output_messages:
        for part in message.get("parts") or []:
            if not isinstance(part, dict) or part.get("type") != "text":
                continue
            content = part.get("content")
            if content:
                chunks.append(str(content))
    if chunks:
        return "\n".join(chunks)
    if operation == "execute_tool" and attrs.get(_TOOL_NAME):
        return f"Execute {attrs[_TOOL_NAME]}"
    return _step_name({}, attrs, operation)


def _atif_tool_calls(
    input_messages: tuple[dict[str, Any], ...], attrs: dict[str, Any]
) -> list[ToolCall]:
    result = []
    for message in input_messages:
        for part in message.get("parts") or []:
            if not isinstance(part, dict) or part.get("type") != "tool_call":
                continue
            raw_arguments = part.get("arguments")
            arguments = raw_arguments if isinstance(raw_arguments, dict) else {}
            extra = (
                {"otel.arguments": raw_arguments}
                if raw_arguments is not None and not isinstance(raw_arguments, dict)
                else None
            )
            result.append(
                ToolCall(
                    tool_call_id=str(part.get("id") or ""),
                    function_name=str(part.get("name") or ""),
                    arguments=arguments,
                    extra=extra,
                )
            )
    if not result and attrs.get(_TOOL_NAME):
        raw_arguments = _json_or_value(attrs.get(_TOOL_ARGUMENTS))
        result.append(
            ToolCall(
                tool_call_id=str(attrs.get(_TOOL_ID) or ""),
                function_name=str(attrs[_TOOL_NAME]),
                arguments=raw_arguments if isinstance(raw_arguments, dict) else {},
                extra=(
                    {"otel.arguments": raw_arguments}
                    if raw_arguments is not None and not isinstance(raw_arguments, dict)
                    else None
                ),
            )
        )
    return result


def _atif_observation(
    output_messages: tuple[dict[str, Any], ...], attrs: dict[str, Any]
) -> Observation | None:
    results = []
    for message in output_messages:
        for part in message.get("parts") or []:
            if not isinstance(part, dict) or part.get("type") != "tool_call_response":
                continue
            content = part.get("response")
            if content is not None and not isinstance(content, str):
                content = json.dumps(content, ensure_ascii=False, sort_keys=True)
            results.append(
                ObservationResult(
                    source_call_id=(
                        str(part["id"]) if part.get("id") is not None else None
                    ),
                    content=content,
                )
            )
    if not results and _TOOL_RESULT in attrs:
        content = _json_or_value(attrs[_TOOL_RESULT])
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False, sort_keys=True)
        results.append(
            ObservationResult(
                source_call_id=(
                    str(attrs[_TOOL_ID]) if attrs.get(_TOOL_ID) is not None else None
                ),
                content=content,
            )
        )
    return Observation(results=results) if results else None


def _metrics(attrs: dict[str, Any]) -> Metrics | None:
    prompt = _token(attrs, "gen_ai.usage.input_tokens", "gen_ai.usage.prompt_tokens")
    completion = _token(
        attrs, "gen_ai.usage.output_tokens", "gen_ai.usage.completion_tokens"
    )
    cached = _token(
        attrs,
        "gen_ai.usage.cached_input_tokens",
        "gen_ai.usage.cache_read.input_tokens",
        "gen_ai.usage.cache_read_tokens",
    )
    if prompt is None and completion is None and cached is None:
        return None
    return Metrics(
        prompt_tokens=prompt,
        completion_tokens=completion,
        cached_tokens=cached,
    )


def _token(attrs: dict[str, Any], *names: str) -> int | None:
    for name in names:
        value = attrs.get(name)
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed >= 0:
            return parsed
    return None


def _step_name(raw: dict[str, Any], attrs: dict[str, Any], operation: str) -> str:
    if operation == "execute_tool" and attrs.get(_TOOL_NAME):
        return str(attrs[_TOOL_NAME])
    for key in ("gen_ai.agent.name", "gen_ai.workflow.name", "gen_ai.response.model"):
        if attrs.get(key):
            return str(attrs[key])
    return str(raw.get("name") or operation)


def _status(raw: dict[str, Any], attrs: dict[str, Any]) -> str:
    if attrs.get(_ERROR_TYPE) or attrs.get(_EXCEPTION_TYPE):
        return "error"
    status = raw.get("status") or {}
    code = status.get("code") if isinstance(status, dict) else status
    if code in (2, "2", "STATUS_CODE_ERROR", "ERROR"):
        return "error"
    if code in (1, "1", "STATUS_CODE_OK", "OK"):
        return "ok"
    return ""


def _failure(raw: dict[str, Any], attrs: dict[str, Any], operation: str) -> Failure:
    status = raw.get("status") or {}
    message = status.get("message", "") if isinstance(status, dict) else ""
    error_type = str(attrs.get(_ERROR_TYPE) or attrs.get(_EXCEPTION_TYPE) or "unknown")
    return Failure(
        kind=_failure_kind(operation),
        phase=_failure_phase(operation),
        error_type=error_type,
        code=str(attrs.get(_EXCEPTION_TYPE) or ""),
        message=str(attrs.get(_EXCEPTION_MESSAGE) or message or ""),
    )


def _failure_kind(operation: str) -> FailureKind:
    if operation == "execute_tool":
        return "tool"
    if operation in {
        "inference",
        "chat",
        "generate_content",
        "text_completion",
        "embeddings",
    }:
        return "llm"
    if "workflow" in operation:
        return "workflow"
    if operation in {"create_agent", "invoke_agent", "plan"}:
        return "agent"
    return "unknown"


def _failure_phase(operation: str) -> str:
    if operation == "execute_tool":
        return "execute"
    if operation in {
        "inference",
        "chat",
        "generate_content",
        "text_completion",
        "embeddings",
    }:
        return "request"
    return operation
