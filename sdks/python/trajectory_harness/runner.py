"""Detect, verify, and measure one fixed trajectory dataset."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from trajectory_harness.dataset import TrajectoryDataset
from trajectory_harness.detect import Detector, DetectorSpec, detect
from trajectory_harness.verify import Verifier, VerifierSpec, verify
from trajectory_harness.measure import Measurer, MeasurerSpec, measure
from trajectory_harness.metrics import TrajectoryAnalysisRun, aggregate_metrics
from atif import Trajectory

from trajectory_harness.model import AnalysisCategory


class TrajectoryAnalysisRunner:
    """Dataset-to-run stage, independent of source collection and report rendering."""

    def __init__(
        self,
        *,
        detectors: Sequence[Detector] = (),
        verifiers: Sequence[Verifier] = (),
        measurers: Sequence[Measurer] = (),
    ) -> None:
        self.detectors = tuple(detectors)
        self.verifiers = tuple(verifiers)
        self.measurers = tuple(measurers)

    def target_for(self, trajectory: Trajectory, dataset: TrajectoryDataset) -> str:
        """Return the domain target represented by one Worksheet row."""

        return ""

    def detectors_for(
        self, target: str, dataset: TrajectoryDataset
    ) -> Sequence[Detector]:
        return self.detectors

    def verifiers_for(
        self, target: str, dataset: TrajectoryDataset
    ) -> Sequence[Verifier]:
        return self.verifiers

    def measurers_for(
        self, target: str, dataset: TrajectoryDataset
    ) -> Sequence[Measurer]:
        return self.measurers

    def metadata_for(self, dataset: TrajectoryDataset) -> Mapping[str, Any]:
        return {}

    def run(
        self,
        dataset: TrajectoryDataset,
        *,
        run_id: str,
        created_at: datetime | None = None,
    ) -> TrajectoryAnalysisRun:
        timestamp = created_at or datetime.now(timezone.utc)
        targets = tuple(
            (
                trajectory.trajectory_id,
                self.target_for(trajectory, dataset),
            )
            for trajectory in dataset.trajectories
        )
        detections = []
        verifications = []
        measurements = []
        selected_detectors = []
        selected_verifiers = []
        selected_measurers = []
        for trajectory, (_, target) in zip(dataset.trajectories, targets):
            detectors = tuple(self.detectors_for(target, dataset))
            verifiers = tuple(self.verifiers_for(target, dataset))
            measurers = tuple(self.measurers_for(target, dataset))
            selected_detectors.extend(detectors)
            selected_verifiers.extend(verifiers)
            selected_measurers.extend(measurers)
            trajectory_measurements = []
            for category, items in _measurers_by_category(measurers):
                trajectory_measurements.append(
                    measure(
                        trajectory,
                        items,
                        target=target,
                        category=category,
                    )
                )
            measurements.extend(trajectory_measurements)
            measurement_input = tuple(
                result for item in trajectory_measurements for result in item.results
            )
            for category, items in _detectors_by_category(detectors):
                detections.append(
                    detect(
                        trajectory,
                        items,
                        measurements=measurement_input,
                        target=target,
                        category=category,
                    )
                )
            for category, items in _verifiers_by_category(verifiers):
                verifications.append(
                    verify(
                        trajectory,
                        items,
                        measurements=measurement_input,
                        target=target,
                        category=category,
                    )
                )
        run = TrajectoryAnalysisRun(
            run_id=run_id,
            created_at=timestamp,
            dataset_id=dataset.dataset_id,
            dataset_version=dataset.version,
            trajectory_ids=tuple(item.trajectory_id for item in dataset.trajectories),
            trajectory_targets=targets,
            detections=tuple(detections),
            detector_specs=_detector_specs(selected_detectors),
            verifications=tuple(verifications),
            verifier_specs=_verifier_specs(selected_verifiers),
            measurements=tuple(measurements),
            measurer_specs=_measurer_specs(selected_measurers),
            annotation_count=_annotation_count(dataset, dataset.trajectories),
            metadata=dict(self.metadata_for(dataset)),
        )
        return replace(
            run,
            metrics=aggregate_metrics(run, trajectories=dataset.trajectories),
        )


def _annotation_count(
    dataset: TrajectoryDataset, trajectories: Sequence[Trajectory]
) -> int:
    if not dataset.annotations:
        return 0
    trajectory_ids = {item.trajectory_id for item in trajectories}
    return sum(
        bool(trajectory_ids.intersection(annotation.trajectory_ids))
        for annotation in dataset.annotations
    )


def _verifier_specs(verifiers: Sequence[Verifier]) -> tuple[VerifierSpec, ...]:
    return tuple({item.spec.verifier_id: item.spec for item in verifiers}.values())


def _detector_specs(detectors: Sequence[Detector]) -> tuple[DetectorSpec, ...]:
    return tuple({item.spec.detector_id: item.spec for item in detectors}.values())


def _measurer_specs(measurers: Sequence[Measurer]) -> tuple[MeasurerSpec, ...]:
    return tuple({item.spec.measurer_id: item.spec for item in measurers}.values())


def _detectors_by_category(
    detectors: Sequence[Detector],
) -> tuple[tuple[AnalysisCategory, tuple[Detector, ...]], ...]:
    categories: dict[AnalysisCategory, list[Detector]] = {}
    for detector in detectors:
        categories.setdefault(detector.spec.category, []).append(detector)
    return tuple(
        (category, tuple(items)) for category, items in sorted(categories.items())
    )


def _verifiers_by_category(
    verifiers: Sequence[Verifier],
) -> tuple[tuple[AnalysisCategory, tuple[Verifier, ...]], ...]:
    categories: dict[AnalysisCategory, list[Verifier]] = {}
    for verifier in verifiers:
        categories.setdefault(verifier.spec.category, []).append(verifier)
    return tuple(
        (category, tuple(items)) for category, items in sorted(categories.items())
    )


def _measurers_by_category(
    measurers: Sequence[Measurer],
) -> tuple[tuple[AnalysisCategory, tuple[Measurer, ...]], ...]:
    categories: dict[AnalysisCategory, list[Measurer]] = {}
    for measurer in measurers:
        categories.setdefault(measurer.spec.category, []).append(measurer)
    return tuple(
        (category, tuple(items)) for category, items in sorted(categories.items())
    )
