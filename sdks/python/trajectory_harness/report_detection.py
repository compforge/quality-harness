"""Detector catalog and Finding evidence report projections."""

from __future__ import annotations

from typing import Sequence

from harness_common.report_kit import Section, Table
from trajectory_harness.metrics import TrajectoryAnalysisRun
from trajectory_harness.model import (
    require_trajectory_id,
    trajectory_recording_id,
    trajectory_source,
)


def detector_catalog_section(runs: Sequence[TrajectoryAnalysisRun]) -> Section:
    specs = {}
    for run in runs:
        for spec in run.detector_specs:
            specs[spec.detector_id] = spec
    return Section(
        heading="Detector catalog",
        blocks=[
            Table(
                columns=[
                    "Detector",
                    "Category",
                    "Rule type",
                    "Kind",
                    "Owner",
                    "Description",
                ],
                rows=[
                    [
                        spec.detector_id,
                        spec.category,
                        spec.rule_type,
                        spec.kind,
                        spec.owner or "—",
                        spec.description,
                    ]
                    for spec in sorted(
                        specs.values(), key=lambda item: item.detector_id
                    )
                ],
            )
        ],
    )


def detection_evidence_section(runs: Sequence[TrajectoryAnalysisRun]) -> Section:
    rows = []
    for run in sorted(runs, key=lambda item: item.dataset_id):
        for item in run.detections:
            for result in item.results:
                findings = result.findings or (None,)
                for finding in findings:
                    rows.append(
                        [
                            run.dataset_id,
                            require_trajectory_id(item.trajectory),
                            trajectory_recording_id(item.trajectory) or "—",
                            trajectory_source(item.trajectory) or "—",
                            item.target or "—",
                            item.category,
                            result.detector_id,
                            result.status,
                            finding.code if finding else "—",
                            finding.severity if finding else "—",
                            finding.summary if finding else "—",
                            ", ".join(finding.step_ids) if finding else "—",
                            "; ".join(finding.hypotheses) if finding else "—",
                            result.explanation or "—",
                        ]
                    )
    return Section(
        heading="Detection evidence",
        blocks=[
            Table(
                columns=[
                    "Dataset",
                    "Trajectory",
                    "Recording",
                    "Source",
                    "Target",
                    "Category",
                    "Detector",
                    "Status",
                    "Finding",
                    "Severity",
                    "Summary",
                    "Steps",
                    "Hypotheses",
                    "Explanation",
                ],
                rows=rows,
            )
        ]
        if rows
        else [],
    )
