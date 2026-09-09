"""ATIF trajectory types and quality-harness extension accessors.

Trajectory and Step are the official ``atif`` package models. This module does
not define a second trajectory schema; it centralizes access to optional,
namespaced evaluation provenance carried by ATIF's ``extra`` fields.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping, Sequence

from atif import Agent, Step, Trajectory

ATIF_SCHEMA_VERSION = "ATIF-v1.7"
QUALITY_HARNESS_EXTENSION = "quality_harness"

FailureKind = Literal["llm", "tool", "agent", "workflow", "unknown"]
ExecutionOutcome = Literal["completed", "failed", "timeout", "canceled", "unknown"]
AnalysisCategory = Literal["cost", "effect"]
RuleType = Literal["hard", "soft"]


@dataclass(frozen=True, slots=True)
class Failure:
    """A normalized failure read from optional ATIF extension data."""

    kind: FailureKind
    phase: str
    error_type: str
    code: str = ""
    message: str = ""

    @property
    def key(self) -> str:
        return ".".join(
            part for part in (self.kind, self.phase, self.error_type) if part
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "phase": self.phase,
            "error_type": self.error_type,
            "code": self.code,
            "message": self.message,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Failure:
        return cls(
            kind=value.get("kind", "unknown"),
            phase=str(value.get("phase") or ""),
            error_type=str(value.get("error_type") or "unknown"),
            code=str(value.get("code") or ""),
            message=str(value.get("message") or ""),
        )


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """An execution outcome read from optional ATIF extension data."""

    outcome: ExecutionOutcome
    duration_ms: float | None = None
    failure: Failure | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "duration_ms": self.duration_ms,
            "failure": self.failure.to_dict() if self.failure else None,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ExecutionResult:
        raw_failure = value.get("failure")
        return cls(
            outcome=value.get("outcome", "unknown"),
            duration_ms=(
                float(value["duration_ms"])
                if value.get("duration_ms") is not None
                else None
            ),
            failure=(
                Failure.from_dict(raw_failure)
                if isinstance(raw_failure, Mapping)
                else None
            ),
        )


def trajectory_to_dict(trajectory: Trajectory) -> dict[str, Any]:
    """Serialize the official ATIF model without introducing a local shape."""

    return trajectory.to_json_dict()


def trajectory_from_dict(value: Mapping[str, Any]) -> Trajectory:
    """Validate one official ATIF trajectory document."""

    trajectory = Trajectory.model_validate(value)
    if trajectory.schema_version != ATIF_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported ATIF schema {trajectory.schema_version!r}; "
            f"expected {ATIF_SCHEMA_VERSION!r}"
        )
    return trajectory


def require_trajectory_id(trajectory: Trajectory) -> str:
    """Return the ATIF document id required by trajectory_harness datasets."""

    if not trajectory.trajectory_id:
        raise ValueError("trajectory_harness requires ATIF trajectory_id")
    return trajectory.trajectory_id


def with_recording(
    trajectory: Trajectory, *, recording_id: str, source: str
) -> Trajectory:
    """Attach collection provenance through ATIF's namespaced ``extra`` field."""

    extension = dict(_extension(trajectory.extra))
    extension["recording"] = {"id": recording_id, "source": source}
    return trajectory.model_copy(
        update={"extra": _merge_extension(trajectory.extra, extension)}
    )


def trajectory_recording_id(trajectory: Trajectory) -> str:
    recording = _extension(trajectory.extra).get("recording")
    return str(recording.get("id") or "") if isinstance(recording, Mapping) else ""


def trajectory_source(trajectory: Trajectory) -> str:
    recording = _extension(trajectory.extra).get("recording")
    return str(recording.get("source") or "") if isinstance(recording, Mapping) else ""


def trajectory_metadata(trajectory: Trajectory) -> dict[str, Any]:
    value = _extension(trajectory.extra).get("metadata")
    return dict(value) if isinstance(value, Mapping) else {}


def trajectory_generation(trajectory: Trajectory) -> dict[str, str]:
    """Project standard ATIF agent identity plus optional generation versions."""

    result = {
        "agent": trajectory.agent.name,
        "agent_revision": trajectory.agent.version,
    }
    if trajectory.agent.model_name:
        result["model"] = trajectory.agent.model_name
    value = _extension(trajectory.extra).get("generation")
    if isinstance(value, Mapping):
        result.update({str(key): str(item) for key, item in value.items()})
    return result


def trajectory_execution(trajectory: Trajectory) -> ExecutionResult | None:
    value = _extension(trajectory.extra).get("execution")
    return ExecutionResult.from_dict(value) if isinstance(value, Mapping) else None


def step_id(step: Step) -> str:
    return str(step.step_id)


def step_operation(step: Step) -> str:
    value = _step_extension(step).get("operation")
    if value:
        return str(value)
    if step.source == "agent" and step.llm_call_count != 0:
        return "inference"
    if step.tool_calls:
        return "execute_tool"
    return step.source


def step_name(step: Step) -> str:
    value = _step_extension(step).get("name")
    if value:
        return str(value)
    if step.tool_calls:
        return step.tool_calls[0].function_name
    return step.model_name or step.source


def step_parent_id(step: Step) -> str | None:
    value = _step_extension(step).get("parent_step_id")
    return str(value) if value not in (None, "") else None


def step_start_ms(step: Step) -> float:
    return _number(_step_extension(step).get("start_ms"))


def step_duration_ms(step: Step) -> float:
    return _number(_step_extension(step).get("duration_ms"))


def step_status(step: Step) -> str:
    return str(_step_extension(step).get("status") or "")


def step_failure(step: Step) -> Failure | None:
    value = _step_extension(step).get("failure")
    return Failure.from_dict(value) if isinstance(value, Mapping) else None


def step_attributes(step: Step) -> dict[str, Any]:
    value = _step_extension(step).get("attributes")
    return dict(value) if isinstance(value, Mapping) else {}


def step_input_messages(step: Step) -> tuple[dict[str, Any], ...]:
    return _message_dicts(_step_extension(step).get("input_messages"))


def step_output_messages(step: Step) -> tuple[dict[str, Any], ...]:
    return _message_dicts(_step_extension(step).get("output_messages"))


def make_atif_trajectory(
    *,
    trajectory_id: str,
    steps: Sequence[Step],
    agent: Agent | None = None,
    recording_id: str = "",
    source: str = "",
    generation: Mapping[str, str] | None = None,
    execution: ExecutionResult | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> Trajectory:
    """Construct ATIF output for a recording adapter."""

    extension: dict[str, Any] = {}
    if recording_id or source:
        extension["recording"] = {"id": recording_id, "source": source}
    if generation:
        extension["generation"] = dict(generation)
    if execution:
        extension["execution"] = execution.to_dict()
    if metadata:
        extension["metadata"] = dict(metadata)
    normalized_steps = [
        step.model_copy(update={"step_id": index})
        for index, step in enumerate(steps, start=1)
    ]
    return Trajectory(
        schema_version=ATIF_SCHEMA_VERSION,
        trajectory_id=trajectory_id,
        session_id=trajectory_id,
        agent=agent or Agent(name="unknown", version="unknown"),
        steps=normalized_steps,
        extra={QUALITY_HARNESS_EXTENSION: extension} if extension else None,
    )


def make_atif_step(
    *,
    step_id: int | str,
    source: Literal["system", "user", "agent"] = "agent",
    message: str = "",
    operation: str = "",
    name: str = "",
    parent_step_id: str | None = None,
    start_ms: float = 0,
    duration_ms: float = 0,
    status: str = "",
    failure: Failure | None = None,
    input_messages: tuple[dict[str, Any], ...] = (),
    output_messages: tuple[dict[str, Any], ...] = (),
    attributes: Mapping[str, Any] | None = None,
    **standard_fields: Any,
) -> Step:
    """Construct an official ATIF step from adapter facts."""

    extension: dict[str, Any] = {
        "operation": operation,
        "name": name,
        "start_ms": start_ms,
        "duration_ms": duration_ms,
    }
    if parent_step_id:
        extension["parent_step_id"] = parent_step_id
    if status:
        extension["status"] = status
    if failure:
        extension["failure"] = failure.to_dict()
    if input_messages:
        extension["input_messages"] = list(input_messages)
    if output_messages:
        extension["output_messages"] = list(output_messages)
    if attributes:
        extension["attributes"] = dict(attributes)
    extra = dict(standard_fields.pop("extra", {}) or {})
    extra[QUALITY_HARNESS_EXTENSION] = extension
    try:
        atif_step_id = int(step_id)
    except (TypeError, ValueError):
        atif_step_id = 1
    return Step(
        step_id=max(1, atif_step_id),
        source=source,
        message=message,
        extra=extra,
        **standard_fields,
    )


def _extension(extra: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if not isinstance(extra, Mapping):
        return {}
    value = extra.get(QUALITY_HARNESS_EXTENSION)
    return value if isinstance(value, Mapping) else {}


def _step_extension(step: Step) -> Mapping[str, Any]:
    return _extension(step.extra)


def _merge_extension(
    extra: Mapping[str, Any] | None, extension: Mapping[str, Any]
) -> dict[str, Any]:
    result = dict(extra or {})
    result[QUALITY_HARNESS_EXTENSION] = dict(extension)
    return result


def _message_dicts(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list):
        return ()
    return tuple(dict(item) for item in value if isinstance(item, Mapping))


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
