"""Shared native facts converted through the existing ATIF construction helpers."""

import json
from datetime import datetime
from pathlib import Path

from trajectory_harness import (
    Failure,
    ModelUsageMeasurer,
    ToolUsageMeasurer,
    TrajectoryAnalysisRunner,
    TrajectoryDataset,
    measurement_views,
)
from trajectory_harness.model import make_atif_step, make_atif_trajectory


def test_go_python_session_measurement_conformance():
    fixture = json.loads(
        (
            Path(__file__).resolve().parents[4]
            / "conformance/trajectory/session-measurements.json"
        ).read_text()
    )
    trajectories = []
    for session in fixture["sessions"]:
        steps = []
        for index, node in enumerate(session["nodes"], 1):
            model = node["kind"] == "model_call"
            usage = node.get("response", {}).get("usage", {})
            attributes = {}
            if "input_tokens" in usage:
                attributes["input_tokens"] = usage["input_tokens"] + usage.get(
                    "cache_write_input_tokens", 0
                )
            if "output_tokens" in usage:
                attributes["output_tokens"] = usage["output_tokens"]
            if "cache_read_input_tokens" in usage:
                attributes["cached_input_tokens"] = usage["cache_read_input_tokens"]
            duration = None
            if node.get("started_at") and node.get("finished_at"):
                duration = (
                    datetime.fromisoformat(node["finished_at"])
                    - datetime.fromisoformat(node["started_at"])
                ).total_seconds() * 1000
            step = make_atif_step(
                step_id=index,
                operation="chat" if model else "execute_tool",
                duration_ms=duration or 0,
                attributes=attributes,
                failure=Failure(
                    kind="llm" if model else "tool", phase="", error_type="failed"
                )
                if node.get("status") == "failed"
                else None,
            )
            if duration is None:
                step.extra["quality_harness"].pop("duration_ms")
            steps.append(step)
        identity = session["session"]["id"]
        trajectories.append(
            make_atif_trajectory(trajectory_id=identity, steps=tuple(steps)).model_copy(
                update={"session_id": identity}
            )
        )
    dataset = TrajectoryDataset("conformance", "1", tuple(trajectories))
    run = TrajectoryAnalysisRunner(
        measurers=(ModelUsageMeasurer(), ToolUsageMeasurer())
    ).run(dataset, run_id="conformance")
    views = measurement_views(run, trajectories=trajectories)

    def check(view, expected):
        rows = {(row["measurer_id"], row["name"]): row for row in view.values}
        for measurer, values in expected.items():
            for name, value in values.items():
                assert rows[measurer, name]["value"] == value

    check(views.global_view, fixture["expected"]["global"])
    for view in views.sessions:
        check(view, fixture["expected"]["sessions"].get(view.session.id, {}))
