from __future__ import annotations

import json

import pytest
from atif import Agent, Metrics, Observation, ObservationResult, Step, ToolCall
from atif import Trajectory as OfficialTrajectory
from pydantic import ValidationError

from trajectory_harness import (
    ATIFJsonLoader,
    ModelUsageMeasurer,
    RepeatedToolCallDetector,
    ToolSuccessVerifier,
    ToolUsageMeasurer,
    Trajectory,
)


def _trajectory() -> OfficialTrajectory:
    call = ToolCall(
        tool_call_id="call-1",
        function_name="read_file",
        arguments={"path": "README.md"},
    )
    return OfficialTrajectory(
        schema_version="ATIF-v1.7",
        trajectory_id="trajectory-1",
        session_id="session-1",
        agent=Agent(name="reviewer", version="1.2.3", model_name="gpt-5"),
        steps=[
            Step(step_id=1, source="user", message="Review the repository."),
            Step(
                step_id=2,
                source="agent",
                message="I will inspect the README.",
                tool_calls=[call],
                observation=Observation(
                    results=[
                        ObservationResult(
                            source_call_id="call-1", content="Project documentation"
                        )
                    ]
                ),
                metrics=Metrics(
                    prompt_tokens=100,
                    completion_tokens=20,
                    cached_tokens=40,
                ),
                llm_call_count=1,
            ),
            Step(
                step_id=3,
                source="agent",
                message="I will inspect it again.",
                tool_calls=[call.model_copy(update={"tool_call_id": "call-2"})],
                observation=Observation(
                    results=[
                        ObservationResult(
                            source_call_id="call-2", content="Project documentation"
                        )
                    ]
                ),
                llm_call_count=1,
            ),
        ],
    )


def test_public_trajectory_is_official_atif_model() -> None:
    assert Trajectory is OfficialTrajectory


def test_native_atif_loads_without_quality_harness_extension() -> None:
    loaded = ATIFJsonLoader().loads(json.dumps(_trajectory().to_json_dict()))[0]

    assert loaded == _trajectory()
    usage = ModelUsageMeasurer().measure(loaded)
    assert usage.measurements["total_tokens"] == 120
    assert usage.measurements["cached_input_tokens"] == 40
    finding = RepeatedToolCallDetector().detect(loaded).findings[0]
    assert finding.step_ids == ("3",)
    tools = ToolUsageMeasurer().measure(loaded)
    assert tools.measurements["tool_call_count"] == 2
    assert tools.measurements["result_reported_call_count"] == 2
    assert ToolSuccessVerifier().verify(loaded).verdict == "pass"


def test_native_atif_loader_accepts_jsonl_and_enforces_schema() -> None:
    payload = json.dumps(_trajectory().to_json_dict())
    assert len(ATIFJsonLoader().loads(f"{payload}\n{payload}\n")) == 2

    invalid = json.dumps({"schema_version": "ATIF-v1.7", "steps": []})
    with pytest.raises(ValidationError):
        ATIFJsonLoader().loads(invalid)

    unsupported = _trajectory().model_copy(update={"schema_version": "ATIF-v1.6"})
    with pytest.raises(ValueError, match="unsupported ATIF schema"):
        ATIFJsonLoader().loads(json.dumps(unsupported.to_json_dict()))


def test_quality_harness_extension_roundtrip() -> None:
    from trajectory_harness import TrajectoryDataset
    from trajectory_harness.model import (
        Failure,
        make_atif_step,
        make_atif_trajectory,
        step_failure,
        step_operation,
        trajectory_recording_id,
    )

    step = make_atif_step(
        step_id=1,
        operation="review",
        failure=Failure(kind="llm", phase="routing", error_type="timeout"),
        extra={"producer": {"keep": True}},
    )
    trajectory = make_atif_trajectory(
        trajectory_id="review",
        steps=[step],
        recording_id="recording",
    )
    dataset = TrajectoryDataset(
        dataset_id="extension", version="1", trajectories=(trajectory,)
    )
    encoded = dataset.to_dict()
    document = encoded["trajectories"][0]
    assert document["extra"] == {
        "quality_harness": {
            "recording": {"id": "recording", "source": ""},
        }
    }
    assert (
        document["steps"][0]["extra"]["quality_harness"]["failure"]["error_type"]
        == "timeout"
    )
    assert document["steps"][0]["extra"]["producer"] == {"keep": True}
    restored = TrajectoryDataset.from_dict(encoded).trajectories[0]
    assert trajectory_recording_id(restored) == "recording"
    assert step_operation(restored.steps[0]) == "review"
    assert step_failure(restored.steps[0]).error_type == "timeout"
