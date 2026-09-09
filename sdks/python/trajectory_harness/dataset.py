"""Versioned trajectory dataset and annotation model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from atif import Trajectory

from trajectory_harness.model import (
    require_trajectory_id,
    trajectory_from_dict,
    trajectory_recording_id,
    trajectory_to_dict,
)


@dataclass(frozen=True, slots=True)
class TrajectoryAnnotation:
    """Pre-existing supervision joined to one or more trajectories.

    An annotation may reference multiple trajectories (for example, review stages).
    An empty ``trajectory_ids`` is allowed so a dataset can retain a known label whose
    recording produced no usable trajectory.
    """

    annotation_id: str
    recording_id: str
    trajectory_ids: tuple[str, ...] = ()
    annotation: dict[str, Any] = field(default_factory=dict)
    dimensions: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "annotation_id": self.annotation_id,
            "recording_id": self.recording_id,
            "trajectory_ids": list(self.trajectory_ids),
            "annotation": dict(self.annotation),
            "dimensions": dict(self.dimensions),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> TrajectoryAnnotation:
        return cls(
            annotation_id=str(value["annotation_id"]),
            recording_id=str(value["recording_id"]),
            trajectory_ids=tuple(str(item) for item in value.get("trajectory_ids", ())),
            annotation=dict(value.get("annotation") or {}),
            dimensions={
                str(key): str(item)
                for key, item in (value.get("dimensions") or {}).items()
            },
            metadata=dict(value.get("metadata") or {}),
        )


@dataclass(frozen=True, slots=True)
class TrajectoryDataset:
    """A fixed, versioned input to trajectory analysis."""

    dataset_id: str
    version: str
    trajectories: tuple[Trajectory, ...]
    annotations: tuple[TrajectoryAnnotation, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.dataset_id:
            raise ValueError("trajectory dataset needs a dataset_id")

        trajectories: dict[str, str] = {}
        for trajectory in self.trajectories:
            trajectory_id = require_trajectory_id(trajectory)
            if trajectory_id in trajectories:
                raise ValueError(f"duplicate trajectory_id {trajectory_id!r}")
            trajectories[trajectory_id] = trajectory_recording_id(trajectory)

        annotation_ids: set[str] = set()
        for annotation in self.annotations:
            if annotation.annotation_id in annotation_ids:
                raise ValueError(
                    f"duplicate annotation_id {annotation.annotation_id!r}"
                )
            annotation_ids.add(annotation.annotation_id)
            for trajectory_id in annotation.trajectory_ids:
                owner = trajectories.get(trajectory_id)
                if owner is None:
                    raise ValueError(
                        f"annotation {annotation.annotation_id!r} references unknown "
                        "trajectory "
                        f"{trajectory_id!r}"
                    )
                if owner != annotation.recording_id:
                    raise ValueError(
                        f"annotation {annotation.annotation_id!r} references trajectory "
                        f"{trajectory_id!r} from recording {owner!r}"
                    )

    @property
    def trajectory_by_id(self) -> dict[str, Trajectory]:
        return {require_trajectory_id(item): item for item in self.trajectories}

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "version": self.version,
            "trajectories": [trajectory_to_dict(item) for item in self.trajectories],
            "annotations": [item.to_dict() for item in self.annotations],
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> TrajectoryDataset:
        return cls(
            dataset_id=str(value["dataset_id"]),
            version=str(value.get("version") or ""),
            trajectories=tuple(
                trajectory_from_dict(item) for item in value.get("trajectories", ())
            ),
            annotations=tuple(
                TrajectoryAnnotation.from_dict(item)
                for item in value.get("annotations", ())
            ),
            metadata=dict(value.get("metadata") or {}),
        )
