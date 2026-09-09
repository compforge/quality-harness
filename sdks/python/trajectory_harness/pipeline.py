"""One-click composition of the independent trajectory lifecycle stages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Sequence

from harness_common.run import run_dir_for
from trajectory_harness.build import TrajectoryDatasetBuilder
from trajectory_harness.report import TrajectoryReportBuilder
from trajectory_harness.runio import (
    TrajectoryRunArtifact,
    load_run_artifact,
    write_run_artifact,
)
from trajectory_harness.runner import TrajectoryAnalysisRunner
from trajectory_harness.source import RecordingQuery
from trajectory_harness.verdict import (
    TrajectoryVerdictPolicy,
    write_trajectory_verdict,
)


@dataclass(frozen=True, slots=True)
class TrajectoryHarnessResult:
    artifact: TrajectoryRunArtifact
    run_dir: Path
    dataset_path: Path
    run_path: Path
    report_path: Path
    verdict_path: Path


class TrajectoryHarness:
    """Convenience facade; lifecycle behavior remains owned by its three components."""

    def __init__(
        self,
        *,
        builder: TrajectoryDatasetBuilder,
        runner: TrajectoryAnalysisRunner,
        reporter: TrajectoryReportBuilder,
        verdict_policy: TrajectoryVerdictPolicy | None = None,
    ) -> None:
        self.builder = builder
        self.runner = runner
        self.reporter = reporter
        self.verdict_policy = verdict_policy

    def run(
        self,
        runs_dir: str | Path,
        *,
        scope: str,
        run_id: str,
        query: RecordingQuery | None = None,
        created_at: datetime | None = None,
        history_dirs: Sequence[str | Path] = (),
    ) -> TrajectoryHarnessResult:
        run_dir = run_dir_for(runs_dir, scope, run_id)
        build = self.builder.build(query)
        run = self.runner.run(build.dataset, run_id=run_id, created_at=created_at)
        artifact = TrajectoryRunArtifact(build=build, run=run)
        dataset_path, run_path = write_run_artifact(run_dir, artifact)
        history = tuple(load_run_artifact(path) for path in history_dirs)
        report_path = self.reporter.write(run_dir, artifact, history=history)
        verdict_path = write_trajectory_verdict(
            run_dir,
            scope,
            artifact,
            policy=self.verdict_policy,
        )
        return TrajectoryHarnessResult(
            artifact=artifact,
            run_dir=run_dir,
            dataset_path=dataset_path,
            run_path=run_path,
            report_path=report_path,
            verdict_path=verdict_path,
        )
