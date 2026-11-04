"""ATIF v1.8 input, persistence and evidence projection contracts."""

import json
from pathlib import Path

import pytest
from agent_ledger.observation import Input, ModelCall, ToolCall
from atif import ContentPart, Trajectory
from pydantic import ValidationError
from spec_case import case, link

from trajectory_harness import ATIFJsonLoader, TrajectoryDataset, session_views
from trajectory_harness.build import DatasetBuildResult, DatasetBuildSummary
from trajectory_harness.model import (
    make_atif_step,
    make_atif_trajectory,
    trajectory_to_dict,
)
from trajectory_harness.runio import load_dataset_artifact, write_dataset_artifact


def _document(identity="root", session="run"):
    return {
        "schema_version": "ATIF-v1.8",
        "trajectory_id": identity,
        "session_id": session,
        "agent": {"name": "agent", "version": "1"},
        "steps": [{"step_id": 1, "source": "user", "message": "inspect"}],
    }


def _parts():
    return [
        {"type": "text", "text": "Listen and compare."},
        {
            "type": "image",
            "source": {"media_type": "image/png", "path": "images/1.png"},
        },
        {
            "type": "audio",
            "source": {
                "media_type": "audio/wav",
                "path": "audio/1.wav",
                "duration_sec": 2.5,
            },
        },
    ]


@case(
    "media_roundtrip",
    "v1.8 media survives loading, Dataset persistence and SessionView projection",
)
@link("repo://docs/trajectory-harness.md")
def test_multimodal_roundtrip(tmp_path: Path):
    document = _document()
    document["steps"][0]["message"] = _parts()
    document["steps"].append(
        {
            "step_id": 2,
            "source": "agent",
            "message": _parts(),
            "llm_call_count": 1,
            "tool_calls": [
                {"tool_call_id": "call", "function_name": "inspect", "arguments": {}}
            ],
            "observation": {
                "results": [{"source_call_id": "call", "content": _parts()}]
            },
        }
    )
    source = tmp_path / "trajectory.json"
    source.write_text(json.dumps(document))
    trajectory = ATIFJsonLoader().load(source)[0]
    dataset = TrajectoryDataset(
        dataset_id="media", version="1", trajectories=(trajectory,)
    )
    build = DatasetBuildResult(dataset=dataset, summary=DatasetBuildSummary())
    write_dataset_artifact(tmp_path / "saved", build)
    restored = load_dataset_artifact(tmp_path / "saved").dataset
    assert restored.to_dict() == dataset.to_dict()
    assert len(restored.trajectories) == 1
    view = session_views(restored.trajectories)[0]
    assert isinstance(view.nodes[0], Input)
    assert view.nodes[0].content == _parts()
    model = next(node for node in view.nodes if isinstance(node, ModelCall))
    tool = next(node for node in view.nodes if isinstance(node, ToolCall))
    assert model.response.output["message"] == _parts()
    assert model.response.usage is None  # Media paths provide no token evidence.
    assert tool.response.output[0]["content"] == _parts()
    assert view.attributes["atif_trajectories"]["root"]["source"] == str(source)
    assert trajectory.steps[0].message[2].source.path == "audio/1.wav"


@case(
    "embedded_sessions",
    "embedded documents inherit run identity without losing their local identities",
)
@link("repo://docs/trajectory-harness.md")
def test_nested_trajectories_preserve_identity_and_references(tmp_path: Path):
    document = _document()
    left = _document("left", None)
    right = _document("right", None)
    left["subagent_trajectories"] = [_document("worker", None)]
    right["subagent_trajectories"] = [_document("worker", "other-run")]
    document["subagent_trajectories"] = [left, right, _document("left/worker", None)]
    document["notes"] = "parent scope"
    document["final_metrics"] = {"total_prompt_tokens": 20}
    document["continued_trajectory_ref"] = "continuation.json"
    document["steps"].append(
        {
            "step_id": 2,
            "source": "agent",
            "message": "delegate",
            "llm_call_count": 0,
            "observation": {
                "results": [
                    {
                        "subagent_trajectory_ref": [
                            {"trajectory_id": "left"},
                            {"trajectory_path": "external.json"},
                        ]
                    }
                ]
            },
        }
    )
    trajectory = ATIFJsonLoader().loads(json.dumps(document), source="recording.json")[
        0
    ]
    before = trajectory.to_json_dict()
    dataset = TrajectoryDataset(
        dataset_id="nested", version="1", trajectories=(trajectory,)
    )
    write_dataset_artifact(tmp_path, DatasetBuildResult(dataset, DatasetBuildSummary()))
    restored = load_dataset_artifact(tmp_path).dataset
    assert restored.trajectories[0].to_json_dict() == before
    assert len(restored.trajectories) == 1
    views = session_views(restored.trajectories)
    assert [view.session.id for view in views] == ["run", "other-run"]
    metadata = views[0].attributes["atif_trajectories"]
    assert set(metadata) == {
        "root",
        "root/left",
        "root/left/worker",
        "root/right",
        "root/left%2Fworker",
    }
    assert metadata["root/left/worker"]["trajectory_id"] == "worker"
    assert metadata["root/left/worker"]["parent_trajectory"] == "root/left"
    assert metadata["root/left/worker"]["source"] == "recording.json"
    assert metadata["root"]["final_metrics"] == {"total_prompt_tokens": 20}
    assert metadata["root"]["notes"] == "parent scope"
    assert metadata["root"]["continued_trajectory_ref"] == "continuation.json"
    assert (
        views[1].attributes["atif_trajectories"]["root/right/worker"][
            "parent_trajectory"
        ]
        == "root/right"
    )
    ids = [node.id for view in views for node in view.nodes]
    assert len(ids) == len(set(ids))
    delegation = next(node for node in views[0].nodes if node.id == "atif:root:step:2")
    assert (
        delegation.attributes["atif"]["observation"]
        == document["steps"][1]["observation"]
    )
    assert trajectory.to_json_dict() == before


def test_child_with_explicit_session_is_visible_under_unassigned_root():
    document = _document(session=None)
    document["subagent_trajectories"] = [
        _document("unassigned", None),
        _document("assigned", "child-run"),
    ]
    views = session_views([Trajectory.model_validate(document)])
    assert len(views) == 1
    assert views[0].session.id == "child-run"
    assert [node.id for node in views[0].nodes] == ["atif:root/assigned:step:1"]


@case(
    "unsupported_versions",
    "reject unsupported ATIF versions at every document boundary, including embedded children",
)
@link("repo://docs/trajectory-harness.md")
@pytest.mark.parametrize("version", ["ATIF-v1.6", "ATIF-v1.7", "ATIF-v1.9"])
@pytest.mark.parametrize("nested", [False, True])
def test_reject_unsupported_versions(version, nested):
    document = _document()
    if nested:
        child = _document("child", None)
        child["schema_version"] = version
        document["subagent_trajectories"] = [child]
    else:
        document["schema_version"] = version
    trajectory = Trajectory.model_validate(document)
    for action in (
        lambda: ATIFJsonLoader().loads(json.dumps(document)),
        lambda: TrajectoryDataset(
            dataset_id="invalid", version="1", trajectories=(trajectory,)
        ),
        lambda: trajectory_to_dict(trajectory),
        lambda: session_views([trajectory]),
    ):
        with pytest.raises(ValueError, match="unsupported ATIF schema"):
            action()


@pytest.mark.parametrize(
    "source",
    [
        {"media_type": "audio/wav", "duration_sec": 1},
        {"media_type": "audio/wav", "path": "a.wav", "duration_sec": -1},
        {"media_type": "image/png", "path": "a.png"},
    ],
)
def test_audio_uses_upstream_schema_validation(source):
    document = _document()
    document["steps"][0]["message"] = [{"type": "audio", "source": source}]
    with pytest.raises(ValidationError):
        ATIFJsonLoader().loads(json.dumps(document))


def test_factory_supports_audio_and_generates_v18():
    parts = [ContentPart.model_validate(part) for part in _parts()]
    trajectory = make_atif_trajectory(
        trajectory_id="new",
        steps=[make_atif_step(step_id=1, source="user", message=parts)],
    )
    assert trajectory.schema_version == "ATIF-v1.8"
    assert trajectory.to_json_dict()["steps"][0]["message"] == _parts()
