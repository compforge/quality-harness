"""Generation-provenance projection for trajectory reports."""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Sequence

from harness_common.report_kit import Section, Table
from trajectory_harness.metrics import TrajectoryAnalysisRun
from trajectory_harness.model import require_trajectory_id, trajectory_generation


def generation_provenance_section(
    runs: Sequence[TrajectoryAnalysisRun],
) -> Section:
    rows = []
    for run in runs:
        grouped = defaultdict(list)
        for trajectory in _run_trajectories(run).values():
            generation = trajectory_generation(trajectory)
            key = (
                json.dumps(generation, ensure_ascii=False, sort_keys=True)
                if generation
                else ""
            )
            grouped[key].append(trajectory)
        for generation_key, trajectories in grouped.items():
            del generation_key
            generation = trajectory_generation(trajectories[0])
            rows.append(
                [
                    run.run_id,
                    run.dataset_id,
                    str(len(trajectories)),
                    generation.get("agent_revision", "unknown"),
                    generation.get("instruction_version", "unknown"),
                    generation.get("skill_version", "unknown"),
                    generation.get("tool_contract_version", "unknown"),
                    generation.get("model", "unknown"),
                    generation.get("loop_config", "unknown"),
                    generation.get("orchestration", "unknown"),
                    _extra_fields(generation),
                ]
            )
    return Section(
        heading="Generation provenance",
        blocks=[
            Table(
                columns=[
                    "Run",
                    "Dataset",
                    "Trajectories",
                    "Agent revision",
                    "Instructions",
                    "Skills",
                    "Tool contract",
                    "Model",
                    "Loop",
                    "Orchestration",
                    "Metadata",
                ],
                rows=rows,
            )
        ]
        if rows
        else [],
    )


def _run_trajectories(run: TrajectoryAnalysisRun) -> dict:
    return {
        require_trajectory_id(item.trajectory): item.trajectory
        for item in (*run.detections, *run.verifications, *run.measurements)
    }


def _extra_fields(generation: dict[str, str]) -> str:
    known = {
        "agent_revision",
        "instruction_version",
        "skill_version",
        "tool_contract_version",
        "model",
        "loop_config",
        "orchestration",
    }
    extra = {key: value for key, value in generation.items() if key not in known}
    return json.dumps(extra, ensure_ascii=False, sort_keys=True) if extra else "—"
