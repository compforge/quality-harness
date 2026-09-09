"""Trajectory analysis runs and generic metric aggregation."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Literal

from atif import Trajectory

from trajectory_harness.detect import DetectorSpec, TrajectoryDetection
from trajectory_harness.verify import VerifierSpec, TrajectoryVerification
from trajectory_harness.measure import (
    MetricDirection,
    MeasurerSpec,
    TrajectoryMeasurement,
)
from trajectory_harness.model import (
    require_trajectory_id,
    step_failure,
    trajectory_execution,
    trajectory_from_dict,
    trajectory_to_dict,
)

MetricAggregation = Literal["count", "rate", "sum", "mean", "p50", "p95"]


@dataclass(frozen=True, slots=True)
class TrajectoryAnalysisRun:
    """One Worksheet produced by evaluating a fixed trajectory dataset version."""

    run_id: str
    created_at: datetime
    dataset_id: str
    dataset_version: str
    trajectory_ids: tuple[str, ...]
    trajectory_targets: tuple[tuple[str, str], ...] = ()
    detections: tuple[TrajectoryDetection, ...] = ()
    detector_specs: tuple[DetectorSpec, ...] = ()
    verifications: tuple[TrajectoryVerification, ...] = ()
    verifier_specs: tuple[VerifierSpec, ...] = ()
    measurements: tuple[TrajectoryMeasurement, ...] = ()
    measurer_specs: tuple[MeasurerSpec, ...] = ()
    annotation_count: int | None = None
    metrics: tuple[Metric, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def target_for(self, trajectory_id: str) -> str:
        return dict(self.trajectory_targets).get(trajectory_id, "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "created_at": self.created_at.isoformat(),
            "dataset_id": self.dataset_id,
            "dataset_version": self.dataset_version,
            "trajectories": [
                trajectory_to_dict(item) for item in _run_trajectories(self)
            ],
            "trajectory_ids": list(self.trajectory_ids),
            "trajectory_targets": dict(self.trajectory_targets),
            "detections": [item.to_dict() for item in self.detections],
            "detector_specs": [item.to_dict() for item in self.detector_specs],
            "verifications": [item.to_dict() for item in self.verifications],
            "verifier_specs": [item.to_dict() for item in self.verifier_specs],
            "measurements": [item.to_dict() for item in self.measurements],
            "measurer_specs": [item.to_dict() for item in self.measurer_specs],
            "annotation_count": self.annotation_count,
            "metrics": [item.to_dict() for item in self.metrics],
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> TrajectoryAnalysisRun:
        trajectories = {
            require_trajectory_id(trajectory): trajectory
            for trajectory in (
                trajectory_from_dict(item) for item in value.get("trajectories", ())
            )
        }

        def trajectory_for(item: dict[str, Any]) -> Trajectory:
            trajectory_id = str(item["trajectory_id"])
            try:
                return trajectories[trajectory_id]
            except KeyError as error:
                raise ValueError(
                    f"run item references unknown trajectory {trajectory_id!r}"
                ) from error

        targets = value.get("trajectory_targets") or {}
        return cls(
            run_id=str(value["run_id"]),
            created_at=datetime.fromisoformat(str(value["created_at"])),
            dataset_id=str(value["dataset_id"]),
            dataset_version=str(value.get("dataset_version") or ""),
            trajectory_ids=tuple(
                str(item) for item in value.get("trajectory_ids", trajectories)
            ),
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


@dataclass(frozen=True, slots=True)
class Metric:
    """One dataset-level value, suitable for comparison and trend reporting."""

    run_id: str
    dataset_id: str
    name: str
    value: float
    aggregation: MetricAggregation
    unit: str = ""
    direction: MetricDirection = "neutral"
    dataset_version: str = ""
    dimensions: tuple[tuple[str, str], ...] = ()

    @property
    def series_key(self) -> tuple:
        return (
            self.dataset_id,
            self.name,
            self.unit,
            self.aggregation,
            self.dimensions,
        )

    @property
    def identity_key(self) -> tuple:
        """Identity within one persisted run; unlike a trend series, includes version."""

        return (self.dataset_version, *self.series_key)

    @property
    def qualified_name(self) -> str:
        suffix = f".{self.aggregation}" if self.aggregation else ""
        dimensions = ",".join(f"{key}={value}" for key, value in self.dimensions)
        return f"{self.name}{suffix}" + (f"{{{dimensions}}}" if dimensions else "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "dataset_id": self.dataset_id,
            "dataset_version": self.dataset_version,
            "name": self.name,
            "value": self.value,
            "unit": self.unit,
            "aggregation": self.aggregation,
            "direction": self.direction,
            "dimensions": dict(self.dimensions),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Metric:
        dimensions = value.get("dimensions") or {}
        return cls(
            run_id=str(value["run_id"]),
            dataset_id=str(value["dataset_id"]),
            dataset_version=str(value.get("dataset_version") or ""),
            name=str(value["name"]),
            value=float(value["value"]),
            unit=str(value.get("unit") or ""),
            aggregation=value["aggregation"],
            direction=value.get("direction", "neutral"),
            dimensions=tuple((str(key), str(item)) for key, item in dimensions.items()),
        )


def aggregate_metrics(
    run: TrajectoryAnalysisRun,
    *,
    trajectories: Iterable[Trajectory] = (),
) -> tuple[Metric, ...]:
    """Aggregate a Worksheet, preserving target and category as dimensions."""

    metrics: list[Metric] = []
    targets: dict[str, list[Trajectory]] = defaultdict(list)
    by_id = {require_trajectory_id(item): item for item in trajectories}
    by_id.update({require_trajectory_id(item): item for item in _run_trajectories(run)})
    for trajectory in by_id.values():
        targets[run.target_for(require_trajectory_id(trajectory))].append(trajectory)
    for target, trajectories in sorted(targets.items()):
        metrics.extend(_execution_metrics(run, target, trajectories))
        metrics.extend(_failure_metrics(run, target, trajectories))
    metrics.extend(_detection_metrics(run))
    metrics.extend(_verification_metrics(run))
    metrics.extend(_measurement_metrics(run))
    return tuple(metrics)


def _execution_metrics(
    run: TrajectoryAnalysisRun,
    target: str,
    trajectories: list[Trajectory],
) -> list[Metric]:
    dimensions = _context_dimensions(target)
    metrics = [
        _metric(
            run,
            "trajectory",
            len(trajectories),
            "count",
            "count",
            dimensions=dimensions,
        )
    ]
    known = []
    for trajectory in trajectories:
        execution = trajectory_execution(trajectory)
        if execution and execution.outcome != "unknown":
            known.append(execution)
    if not known:
        return metrics
    outcomes = Counter(execution.outcome for execution in known)
    denominator = len(known)
    metrics.extend(
        (
            _metric(
                run,
                "execution.completion",
                outcomes["completed"] / denominator,
                "rate",
                "ratio",
                "higher_is_better",
                dimensions,
            ),
            _metric(
                run,
                "execution.timeout",
                outcomes["timeout"] / denominator,
                "rate",
                "ratio",
                "lower_is_better",
                dimensions,
            ),
            _metric(
                run,
                "execution.failure",
                (outcomes["failed"] + outcomes["timeout"]) / denominator,
                "rate",
                "ratio",
                "lower_is_better",
                dimensions,
            ),
        )
    )
    durations = [item.duration_ms for item in known if item.duration_ms is not None]
    metrics.extend(
        _distribution_metrics(
            run,
            "execution.duration_ms",
            durations,
            "ms",
            "lower_is_better",
            ("mean", "p50", "p95"),
            dimensions,
        )
    )
    return metrics


def _failure_metrics(
    run: TrajectoryAnalysisRun,
    target: str,
    trajectories: list[Trajectory],
) -> list[Metric]:
    total = len(trajectories)
    events: Counter[tuple[str, str, str, str]] = Counter()
    affected: dict[tuple[str, str, str, str], set[str]] = defaultdict(set)
    for trajectory in trajectories:
        for step in trajectory.steps:
            failure = step_failure(step)
            if failure:
                key = (
                    "step",
                    failure.kind,
                    failure.phase,
                    failure.error_type,
                )
                events[key] += 1
                affected[key].add(require_trajectory_id(trajectory))
        execution = trajectory_execution(trajectory)
        if execution and execution.failure:
            failure = execution.failure
            key = ("execution", failure.kind, failure.phase, failure.error_type)
            events[key] += 1
            affected[key].add(require_trajectory_id(trajectory))

    result = []
    for key, count in sorted(events.items()):
        impact, kind, phase, error_type = key
        dimensions = _context_dimensions(target) + (
            ("impact", impact),
            ("kind", kind),
            ("phase", phase),
            ("error_type", error_type),
        )
        result.append(
            _metric(
                run,
                "failure",
                count,
                "count",
                "count",
                "lower_is_better",
                dimensions,
            )
        )
        if total:
            result.append(
                _metric(
                    run,
                    "failure",
                    len(affected[key]) / total,
                    "rate",
                    "ratio",
                    "lower_is_better",
                    dimensions,
                )
            )
    return result


def _run_trajectories(run: TrajectoryAnalysisRun) -> tuple[Trajectory, ...]:
    by_id = {
        require_trajectory_id(item.trajectory): item.trajectory
        for item in (*run.detections, *run.verifications, *run.measurements)
    }
    return tuple(by_id.values())


def _detection_metrics(run: TrajectoryAnalysisRun) -> list[Metric]:
    result = []
    grouped = defaultdict(list)
    for item in run.detections:
        for detection in item.results:
            grouped[(item.target, item.category, detection.detector_id)].append(
                detection
            )
    for (target, category, detector_id), detections in sorted(grouped.items()):
        total = _target_total(run, target)
        analyzed = [item for item in detections if item.status == "analyzed"]
        errors = [item for item in detections if item.status == "error"]
        dimensions = _context_dimensions(target, category) + (
            ("detector_id", detector_id),
        )
        if total:
            result.extend(
                (
                    _metric(
                        run,
                        "detection.applicability",
                        len(analyzed) / total,
                        "rate",
                        "ratio",
                        "neutral",
                        dimensions,
                    ),
                    _metric(
                        run,
                        "detection.error",
                        len(errors) / total,
                        "rate",
                        "ratio",
                        "lower_is_better",
                        dimensions,
                    ),
                )
            )

        findings = [
            (detection, finding)
            for detection in analyzed
            for finding in detection.findings
        ]
        for code, severity in sorted(
            {(finding.code, finding.severity) for _, finding in findings}
        ):
            matching = [
                (detection, finding)
                for detection, finding in findings
                if (finding.code, finding.severity) == (code, severity)
            ]
            finding_dimensions = dimensions + (
                ("code", code),
                ("severity", severity),
            )
            result.append(
                _metric(
                    run,
                    "finding",
                    len(matching),
                    "count",
                    "count",
                    "neutral",
                    finding_dimensions,
                )
            )
            if total:
                affected = {
                    item.trajectory.trajectory_id
                    for item in run.detections
                    if item.target == target and item.category == category
                    for detection in item.results
                    if detection.detector_id == detector_id
                    and any(
                        finding.code == code and finding.severity == severity
                        for finding in detection.findings
                    )
                }
                result.append(
                    _metric(
                        run,
                        "finding",
                        len(affected) / total,
                        "rate",
                        "ratio",
                        "neutral",
                        finding_dimensions,
                    )
                )
    return result


def _verification_metrics(run: TrajectoryAnalysisRun) -> list[Metric]:
    result = []
    grouped = defaultdict(list)
    for item in run.verifications:
        for verification in item.results:
            grouped[(item.target, item.category, verification.verifier_id)].append(
                verification
            )
    for (target, category, verifier_id), verifications in sorted(grouped.items()):
        total = _target_total(run, target)
        verified = [item for item in verifications if item.status == "verified"]
        errors = [item for item in verifications if item.status == "error"]
        dimensions = _context_dimensions(target, category) + (
            ("verifier_id", verifier_id),
        )
        if total:
            result.extend(
                (
                    _metric(
                        run,
                        "verification.applicability",
                        len(verified) / total,
                        "rate",
                        "ratio",
                        "neutral",
                        dimensions,
                    ),
                    _metric(
                        run,
                        "verification.error",
                        len(errors) / total,
                        "rate",
                        "ratio",
                        "lower_is_better",
                        dimensions,
                    ),
                )
            )
        if verified:
            judged = [item for item in verified if item.verdict is not None]
            if judged:
                passing = sum(item.verdict == "pass" for item in judged)
                result.append(
                    _metric(
                        run,
                        "verification.pass",
                        passing / len(judged),
                        "rate",
                        "ratio",
                        "higher_is_better",
                        dimensions,
                    )
                )
            scores = [item.score for item in verified if item.score is not None]
            result.extend(
                _distribution_metrics(
                    run,
                    "verification.score",
                    scores,
                    "score",
                    "higher_is_better",
                    ("mean",),
                    dimensions,
                )
            )

    return result


def _measurement_metrics(run: TrajectoryAnalysisRun) -> list[Metric]:
    result = []
    specs = {spec.measurer_id: spec for spec in run.measurer_specs}
    grouped = defaultdict(list)
    for item in run.measurements:
        for measurement in item.results:
            grouped[(item.target, item.category, measurement.measurer_id)].append(
                measurement
            )
    for (target, category, measurer_id), measurements in sorted(grouped.items()):
        spec = specs.get(measurer_id)
        if spec is None:
            continue
        total = _target_total(run, target)
        measured = [item for item in measurements if item.status == "measured"]
        errors = [item for item in measurements if item.status == "error"]
        dimensions = _context_dimensions(target, category) + (
            ("measurer_id", measurer_id),
        )
        if total:
            result.extend(
                (
                    _metric(
                        run,
                        "measurement.applicability",
                        len(measured) / total,
                        "rate",
                        "ratio",
                        "neutral",
                        dimensions,
                    ),
                    _metric(
                        run,
                        "measurement.error",
                        len(errors) / total,
                        "rate",
                        "ratio",
                        "lower_is_better",
                        dimensions,
                    ),
                )
            )

        for measurement_spec in spec.measurements:
            values = [
                float(item.measurements[measurement_spec.name])
                for item in measured
                if measurement_spec.name in item.measurements
            ]
            result.extend(
                _distribution_metrics(
                    run,
                    "measurement.value",
                    values,
                    measurement_spec.unit,
                    measurement_spec.direction,
                    measurement_spec.aggregations,
                    dimensions + (("measurement", measurement_spec.name),),
                )
            )
    return result


def _distribution_metrics(
    run: TrajectoryAnalysisRun,
    name: str,
    values: Iterable[float],
    unit: str,
    direction: MetricDirection,
    aggregations: tuple[MetricAggregation, ...],
    dimensions: tuple[tuple[str, str], ...] = (),
) -> list[Metric]:
    collected = list(values)
    if not collected:
        return []
    result = []
    for aggregation in aggregations:
        if aggregation == "sum":
            value = sum(collected)
        elif aggregation == "mean":
            value = sum(collected) / len(collected)
        elif aggregation == "p50":
            value = _percentile(collected, 0.50)
        elif aggregation == "p95":
            value = _percentile(collected, 0.95)
        else:
            continue
        result.append(
            _metric(
                run,
                name,
                value,
                aggregation,
                unit,
                direction,
                dimensions,
            )
        )
    return result


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(percentile * len(ordered)) - 1)]


def _metric(
    run: TrajectoryAnalysisRun,
    name: str,
    value: float,
    aggregation: MetricAggregation,
    unit: str = "",
    direction: MetricDirection = "neutral",
    dimensions: tuple[tuple[str, str], ...] = (),
) -> Metric:
    return Metric(
        run_id=run.run_id,
        dataset_id=run.dataset_id,
        dataset_version=run.dataset_version,
        name=name,
        value=round(float(value), 6),
        unit=unit,
        aggregation=aggregation,
        direction=direction,
        dimensions=dimensions,
    )


def _context_dimensions(target: str, category: str = "") -> tuple[tuple[str, str], ...]:
    dimensions = []
    if target:
        dimensions.append(("target", target))
    if category:
        dimensions.append(("category", category))
    return tuple(dimensions)


def _target_total(run: TrajectoryAnalysisRun, target: str) -> int:
    if run.trajectory_targets:
        return sum(item_target == target for _, item_target in run.trajectory_targets)
    return len(run.trajectory_ids) if not target else 0
