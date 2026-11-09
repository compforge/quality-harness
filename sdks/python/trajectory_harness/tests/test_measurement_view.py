from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, replace
from unittest.mock import patch

import pytest
from spec_case import case, link

from trajectory_harness import (
    MeasurementResult,
    MeasurementSpec,
    MeasurerSpec,
    ModelUsageMeasurer,
    ToolUsageMeasurer,
    TrajectoryAnalysisRunner,
    TrajectoryDataset,
    TrajectoryReportBuilder,
    measurement_views,
    write_run_artifact,
)
from trajectory_harness.build import DatasetBuildResult, DatasetBuildSummary
from trajectory_harness.model import (
    make_atif_step as Step,
    make_atif_trajectory as Trajectory,
    step_name,
)
from trajectory_harness.runio import TrajectoryRunArtifact


@dataclass(frozen=True)
class AdjacentCallsMeasurer:
    spec: MeasurerSpec = MeasurerSpec(
        measurer_id="adjacent_calls",
        title="Adjacent tool calls",
        description="Count adjacent pairs with the same tool name.",
        category="cost",
        measurements=(
            MeasurementSpec("same_name_count", "count", aggregations=("sum", "mean")),
        ),
    )

    def measure(self, trajectory):
        names = [step_name(step) for step in trajectory.steps]
        return MeasurementResult(
            self.spec.measurer_id,
            "measured",
            {"same_name_count": sum(a == b for a, b in zip(names, names[1:]))},
            step_ids=tuple(str(step.step_id) for step in trajectory.steps),
        )


def trajectory(identity, session="shared", count=3):
    return Trajectory(
        trajectory_id=identity,
        steps=tuple(
            Step(step_id=i, operation="execute_tool", name="read") for i in range(count)
        ),
    ).model_copy(update={"session_id": session})


def artifact(*trajectories):
    dataset = TrajectoryDataset("views", "snapshot-1", tuple(trajectories))
    run = TrajectoryAnalysisRunner(measurers=(AdjacentCallsMeasurer(),)).run(
        dataset, run_id="run-1"
    )
    return TrajectoryRunArtifact(
        DatasetBuildResult(dataset, DatasetBuildSummary()), run
    )


@case(
    "measurement_session_global",
    "Several loop trajectories share one Session",
    expect="One Session projection and one Global total without distributions or repeated calls",
)
@link("repo://docs/trajectory-harness.md")
def test_session_grouping_and_global_total_cross_targets():
    current = artifact(
        trajectory("r1"),
        trajectory("r2", count=2),
        trajectory("other", "other", count=2),
    )
    envelopes = tuple(
        replace(item, target="R1" if i == 0 else "R2")
        for i, item in enumerate(current.run.measurements)
    )
    views = measurement_views(
        replace(current.run, measurements=envelopes),
        trajectories=current.dataset.trajectories,
    )
    assert views.global_view.values[0]["value"] == 4
    assert len(views.sessions) == 2
    shared = next(view for view in views.sessions if view.session.id == "shared")
    assert shared.values[0]["value"] == 3
    assert shared.values[0]["coverage"]["measured"] == 2
    assert shared.values[0]["evidence"][0]["uri"] == "atif:r1"
    assert "evidence" not in views.global_view.values[0]
    assert "sessions" not in views.global_view.to_dict()
    assert views.to_dict()["dataset_version"] == "snapshot-1"


def test_zero_missing_error_and_not_applicable_remain_distinct():
    current = artifact(
        *(
            trajectory(name, name, count=1)
            for name in ("zero", "missing", "error", "na")
        )
    )
    envelopes = []
    for item in current.run.measurements:
        identity = item.trajectory.trajectory_id
        result = item.results[0]
        if identity == "missing":
            result = replace(result, measurements={})
        elif identity == "error":
            result = replace(
                result, status="error", measurements={}, explanation="Unavailable"
            )
        elif identity == "na":
            result = replace(result, status="not_applicable", measurements={})
        envelopes.append(replace(item, results=(result,)))
    views = measurement_views(replace(current.run, measurements=tuple(envelopes)))
    assert views.global_view.values[0]["value"] == 0
    assert views.global_view.values[0]["status"] == "partial"
    assert views.global_view.values[0]["coverage"] == {
        "measured": 1,
        "missing": 1,
        "error": 1,
        "not_applicable": 1,
    }
    sessions = {view.session.id: view.values[0] for view in views.sessions}
    assert sessions["zero"]["status"] == "measured"
    assert sessions["missing"]["value"] is None
    assert sessions["missing"]["status"] == "missing"
    assert sessions["error"]["status"] == "error"
    assert sessions["na"]["status"] == "not_applicable"


def test_unassigned_session_and_embedded_children_do_not_inflate_measurements():
    child = trajectory("child", "child-session", count=100)
    parent = trajectory("parent", "parent-session").model_copy(
        update={"subagent_trajectories": [child]}
    )
    current = artifact(parent, trajectory("unknown", None, count=1))
    views = measurement_views(current.run)
    assert views.unassigned_trajectories == ("unknown",)
    assert [view.session.id for view in views.sessions] == ["parent-session"]
    assert views.global_view.values[0]["value"] == 2


def test_duplicate_results_and_conflicting_session_identity_are_rejected():
    current = artifact(trajectory("one"))
    with pytest.raises(ValueError, match="duplicate measurement"):
        measurement_views(
            replace(current.run, measurements=current.run.measurements * 2)
        )
    with pytest.raises(ValueError, match="conflicting Session"):
        measurement_views(current.run, trajectories=(trajectory("one", "different"),))


def test_summary_aggregation_roundtrips_and_reuses_the_declared_operation():
    current = artifact(trajectory("a", count=3), trajectory("b", count=5))
    definition = replace(
        current.run.measurer_specs[0].measurements[0], summary_aggregation="mean"
    )
    assert MeasurementSpec.from_dict(definition.to_dict()) == definition
    catalog = replace(current.run.measurer_specs[0], measurements=(definition,))
    views = measurement_views(replace(current.run, measurer_specs=(catalog,)))
    assert views.global_view.values[0]["value"] == 3
    assert views.global_view.values[0]["aggregation"] == "mean"
    with pytest.raises(ValueError, match="must be declared"):
        MeasurementSpec("bad", aggregations=("sum",), summary_aggregation="mean")


@case(
    "automatic_measurement_rendering",
    "A consumer registers a new Measurer",
    expect="Session and Global JSON and HTML include its values; rerender runs no analysis",
)
@link("repo://docs/trajectory-harness.md")
def test_new_measurer_exports_and_rerenders_without_executing_plugins(tmp_path):
    current = artifact(trajectory("one"))
    write_run_artifact(tmp_path, current)
    reporter = TrajectoryReportBuilder()
    reporter.write(tmp_path, current)
    summary = tmp_path / "measurement-views.json"
    before = summary.read_text()
    document = json.loads(before)
    assert document["global"]["values"][0]["name"] == "same_name_count"
    assert document["global"]["values"][0]["value"] == 2
    assert document["sessions"][0]["session"]["id"] == "shared"
    assert document["measurer_specs"][0]["title"] == "Adjacent tool calls"
    html = (tmp_path / "report.html").read_text()
    assert "Global measurements" in html
    assert "Session measurements" in html
    assert "same_name_count" in html
    index = json.loads((tmp_path / "session-views/index.json").read_text())
    assert index["measurement_views"] == "../measurement-views.json"
    with patch.object(
        AdjacentCallsMeasurer, "measure", side_effect=AssertionError("must not run")
    ):
        reporter.rerender(tmp_path)
    assert summary.read_text() == before


@pytest.mark.parametrize(
    "measurer,operation,key",
    [
        (ModelUsageMeasurer(), "chat", "model_duration_ms"),
        (ToolUsageMeasurer(), "execute_tool", "tool_duration_ms"),
    ],
)
def test_duration_coverage_preserves_missing_and_recorded_zero(
    measurer, operation, key
):
    calls = [
        Step(step_id=i, operation=operation, name="read", duration_ms=value)
        for i, value in enumerate((5, 0, 9))
    ]
    extra = deepcopy(calls[2].extra)
    extra["quality_harness"].pop("duration_ms")
    calls[2] = calls[2].model_copy(update={"extra": extra})
    result = measurer.measure(Trajectory(trajectory_id="duration", steps=tuple(calls)))
    assert result.measurements[key] == 5
    assert result.measurements["duration_reported_call_count"] == 2
    result = measurer.measure(Trajectory(trajectory_id="missing", steps=(calls[2],)))
    assert key not in result.measurements
    assert result.measurements["duration_reported_call_count"] == 0
