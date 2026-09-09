"""Independent dataset/run artifacts for offline analysis and report rendering."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from trajectory_harness.build import DatasetBuildResult, DatasetBuildSummary
from trajectory_harness.dataset import TrajectoryDataset
from trajectory_harness.detect import DetectorSpec, TrajectoryDetection
from trajectory_harness.verify import VerifierSpec, TrajectoryVerification
from trajectory_harness.measure import MeasurerSpec, TrajectoryMeasurement
from trajectory_harness.metrics import Metric, TrajectoryAnalysisRun
from atif import Trajectory

DATASET_SCHEMA = 4
RUN_SCHEMA = 5
DATASET_FILE = "dataset.json"
RUN_FILE = "run.json"


@dataclass(frozen=True, slots=True)
class TrajectoryRunArtifact:
    """The fixed dataset plus one analysis run over that exact version."""

    build: DatasetBuildResult
    run: TrajectoryAnalysisRun

    def __post_init__(self) -> None:
        dataset = self.build.dataset
        identity = (dataset.dataset_id, dataset.version)
        if (self.run.dataset_id, self.run.dataset_version) != identity:
            raise ValueError("trajectory run does not match its dataset artifact")
        known_trajectory_ids = set(dataset.trajectory_by_id)
        if not set(self.run.trajectory_ids) <= known_trajectory_ids:
            raise ValueError("analysis run references an unknown trajectory")
        target_trajectory_ids = {item[0] for item in self.run.trajectory_targets}
        if len(self.run.trajectory_targets) != len(
            self.run.trajectory_ids
        ) or target_trajectory_ids != set(self.run.trajectory_ids):
            raise ValueError("every trajectory must have exactly one analysis target")
        result_trajectory_ids = {
            item.trajectory.trajectory_id
            for item in (
                *self.run.detections,
                *self.run.verifications,
                *self.run.measurements,
            )
        }
        if not result_trajectory_ids <= set(self.run.trajectory_ids):
            raise ValueError("run result references a trajectory outside its Worksheet")
        for metric in self.run.metrics:
            if (metric.dataset_id, metric.dataset_version) != identity:
                raise ValueError("metric does not match its trajectory run")
            if metric.run_id != self.run.run_id:
                raise ValueError("metric does not match its enclosing run")

    @property
    def dataset(self) -> TrajectoryDataset:
        return self.build.dataset


def write_run_artifact(
    run_dir: str | Path, artifact: TrajectoryRunArtifact
) -> tuple[Path, Path]:
    """Persist one dataset and one current run; history is deliberately external."""

    directory = Path(run_dir)
    directory.mkdir(parents=True, exist_ok=True)
    dataset_path = write_dataset_artifact(directory, artifact.build)
    run_path = _write_json(directory / RUN_FILE, _run_to_dict(artifact.run))
    return dataset_path, run_path


def write_dataset_artifact(directory: str | Path, build: DatasetBuildResult) -> Path:
    """Persist a dataset independently so later runs need no recording source."""

    target_dir = Path(directory)
    target_dir.mkdir(parents=True, exist_ok=True)
    return _write_json(
        target_dir / DATASET_FILE,
        {
            "schema": DATASET_SCHEMA,
            "dataset": build.dataset.to_dict(),
            "build": build.summary.to_dict(),
        },
    )


def load_dataset_artifact(directory: str | Path) -> DatasetBuildResult:
    """Load a fixed dataset independently of any prior analysis run."""

    dataset_value = json.loads(
        (Path(directory) / DATASET_FILE).read_text(encoding="utf-8")
    )
    if dataset_value.get("schema") != DATASET_SCHEMA:
        raise ValueError(
            "unsupported trajectory dataset schema "
            f"{dataset_value.get('schema')!r}; expected {DATASET_SCHEMA}"
        )
    dataset = TrajectoryDataset.from_dict(dataset_value["dataset"])
    return DatasetBuildResult(
        dataset=dataset,
        summary=DatasetBuildSummary.from_dict(dataset_value.get("build") or {}),
    )


def load_run_artifact(run_dir: str | Path) -> TrajectoryRunArtifact:
    """Load one run without touching a Source, Loader, Verifier, or Measurer."""

    directory = Path(run_dir)
    build = load_dataset_artifact(directory)
    dataset = build.dataset
    run_value = json.loads((directory / RUN_FILE).read_text(encoding="utf-8"))
    run = _run_from_dict(run_value, dataset.trajectory_by_id)
    return TrajectoryRunArtifact(build=build, run=run)


def _run_to_dict(run: TrajectoryAnalysisRun) -> dict[str, Any]:
    return {
        "schema": RUN_SCHEMA,
        "run_id": run.run_id,
        "created_at": run.created_at.isoformat(),
        "dataset_id": run.dataset_id,
        "dataset_version": run.dataset_version,
        "trajectory_ids": list(run.trajectory_ids),
        "trajectory_targets": dict(run.trajectory_targets),
        "detections": [item.to_dict() for item in run.detections],
        "detector_specs": [item.to_dict() for item in run.detector_specs],
        "verifications": [item.to_dict() for item in run.verifications],
        "verifier_specs": [item.to_dict() for item in run.verifier_specs],
        "measurements": [item.to_dict() for item in run.measurements],
        "measurer_specs": [item.to_dict() for item in run.measurer_specs],
        "annotation_count": run.annotation_count,
        "metrics": [item.to_dict() for item in run.metrics],
        "metadata": dict(run.metadata),
    }


def _run_from_dict(
    value: dict[str, Any], trajectories: dict[str, Trajectory]
) -> TrajectoryAnalysisRun:
    if value.get("schema") != RUN_SCHEMA:
        raise ValueError(
            f"unsupported trajectory run schema {value.get('schema')!r}; "
            f"expected {RUN_SCHEMA}"
        )
    run_id = str(value["run_id"])
    created_at = datetime.fromisoformat(str(value["created_at"]))
    targets = value.get("trajectory_targets") or {}

    def trajectory_for(item: dict[str, Any]) -> Trajectory:
        trajectory_id = str(item["trajectory_id"])
        try:
            return trajectories[trajectory_id]
        except KeyError as error:
            raise ValueError(
                f"run item references unknown trajectory {trajectory_id!r}"
            ) from error

    return TrajectoryAnalysisRun(
        run_id=run_id,
        created_at=created_at,
        dataset_id=str(value["dataset_id"]),
        dataset_version=str(value.get("dataset_version") or ""),
        trajectory_ids=tuple(str(item) for item in value.get("trajectory_ids", ())),
        trajectory_targets=tuple(
            (str(key), str(item)) for key, item in targets.items()
        ),
        detections=tuple(
            TrajectoryDetection.from_dict(item, trajectory=trajectory_for(item))
            for item in value.get("detections", ())
        ),
        detector_specs=tuple(
            DetectorSpec.from_dict(item) for item in value.get("detector_specs", ())
        ),
        verifications=tuple(
            TrajectoryVerification.from_dict(item, trajectory=trajectory_for(item))
            for item in value.get("verifications", ())
        ),
        verifier_specs=tuple(
            VerifierSpec.from_dict(item) for item in value.get("verifier_specs", ())
        ),
        measurements=tuple(
            TrajectoryMeasurement.from_dict(item, trajectory=trajectory_for(item))
            for item in value.get("measurements", ())
        ),
        measurer_specs=tuple(
            MeasurerSpec.from_dict(item) for item in value.get("measurer_specs", ())
        ),
        annotation_count=(
            int(value["annotation_count"])
            if value.get("annotation_count") is not None
            else None
        ),
        metrics=tuple(Metric.from_dict(item) for item in value.get("metrics", ())),
        metadata=dict(value.get("metadata") or {}),
    )


def _write_json(path: Path, value: dict[str, Any]) -> Path:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    temporary.replace(path)
    return path
