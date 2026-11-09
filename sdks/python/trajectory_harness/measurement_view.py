"""Pure Session and Global projections of one persisted measurement Worksheet."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from typing import Any, Sequence
from urllib.parse import quote

from agent_ledger.common import SessionRef
from agent_ledger.observation import SourceRef
from atif import Trajectory
from spec_case import link, spec

from trajectory_harness.measure import MeasurementResult
from trajectory_harness.metrics import TrajectoryAnalysisRun, aggregate_measurements

MEASUREMENT_VIEW_FILE = "measurement-views.json"


@dataclass(frozen=True, slots=True)
class MeasurementView:
    """Display-ready values with their catalog, coverage, and optional source evidence."""

    values: tuple[dict[str, Any], ...]
    session: SessionRef | None = None

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {"values": list(self.values)}
        if self.session is not None:
            value["session"] = self.session.model_dump(mode="json")
        return value


@dataclass(frozen=True, slots=True)
class MeasurementViews:
    """A result projection; Global contains no per-Session distribution."""

    run: TrajectoryAnalysisRun
    global_view: MeasurementView
    sessions: tuple[MeasurementView, ...]
    unassigned_trajectories: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": 1,
            "run_id": self.run.run_id,
            "dataset_id": self.run.dataset_id,
            "dataset_version": self.run.dataset_version,
            "created_at": self.run.created_at.isoformat(),
            "measurer_specs": [item.to_dict() for item in self.run.measurer_specs],
            "global": self.global_view.to_dict(),
            "sessions": [item.to_dict() for item in self.sessions],
            "unassigned_trajectories": list(self.unassigned_trajectories),
        }


@spec(
    "Project only recorded top-level results; shared Sessions and embedded children never multiply Global measurements"
)
@link("repo://docs/trajectory-harness.md")
def measurement_views(
    run: TrajectoryAnalysisRun, *, trajectories: Sequence[Trajectory] = ()
) -> MeasurementViews:
    """Render a fixed Run without executing a Source, Measurer, Detector or Verifier."""
    selected = set(run.trajectory_ids)
    if len(selected) != len(run.trajectory_ids):
        raise ValueError("duplicate trajectory identity in measurement Run")
    by_id = {
        item.trajectory_id: item
        for item in trajectories
        if item.trajectory_id in selected
    }
    results: dict[tuple[str, str], MeasurementResult] = {}
    for envelope in run.measurements:
        identity = envelope.trajectory.trajectory_id
        if identity not in selected:
            raise ValueError(f"measurement trajectory {identity!r} is outside the Run")
        if (
            identity in by_id
            and by_id[identity].session_id != envelope.trajectory.session_id
        ):
            raise ValueError(
                f"conflicting Session identity for trajectory {identity!r}"
            )
        by_id.setdefault(identity, envelope.trajectory)
        for result in envelope.results:
            key = (identity, result.measurer_id)
            if key in results:
                raise ValueError(f"duplicate measurement result for {key!r}")
            results[key] = result
    sessions: dict[str, set[str]] = {}
    unassigned = []
    for identity in run.trajectory_ids:
        trajectory = by_id.get(identity)
        if trajectory is None or not trajectory.session_id:
            unassigned.append(identity)
        else:
            sessions.setdefault(trajectory.session_id, set()).add(identity)
    return MeasurementViews(
        run=run,
        global_view=_view(run, selected, results),
        sessions=tuple(
            _view(run, identities, results, SessionRef(source="atif", id=session_id))
            for session_id, identities in sorted(sessions.items())
        ),
        unassigned_trajectories=tuple(unassigned),
    )


def _view(
    run: TrajectoryAnalysisRun,
    identities: set[str],
    results: dict[tuple[str, str], MeasurementResult],
    session: SessionRef | None = None,
) -> MeasurementView:
    scoped = replace(
        run,
        trajectory_ids=tuple(
            identity for identity in run.trajectory_ids if identity in identities
        ),
        measurements=tuple(
            item
            for item in run.measurements
            if item.trajectory.trajectory_id in identities
        ),
    )
    aggregates = {
        (
            dict(metric.dimensions)["measurer_id"],
            dict(metric.dimensions).get("measurement"),
            metric.aggregation,
        ): metric.value
        for metric in aggregate_measurements(scoped, group_by_target=False)
        if metric.name == "measurement.value"
    }
    values = []
    for catalog in run.measurer_specs:
        for definition in catalog.measurements:
            counts: Counter[str] = Counter()
            evidence: list[dict[str, Any]] = []
            explanations: list[str] = []
            for identity in sorted(identities):
                result = results.get((identity, catalog.measurer_id))
                status = "missing" if result is None else result.status
                if (
                    result is not None
                    and status == "measured"
                    and definition.name not in result.measurements
                ):
                    status = "missing"
                counts[status] += 1
                if result is not None:
                    if result.explanation and result.explanation not in explanations:
                        explanations.append(result.explanation)
                    if session is not None and status == "measured":
                        evidence.extend(
                            SourceRef(
                                source="atif-step",
                                id=step,
                                uri=f"atif:{quote(identity, safe='')}",
                            ).model_dump(mode="json")
                            for step in result.step_ids
                        )
            aggregation = definition.summary_aggregation or (
                definition.aggregations[0] if definition.aggregations else None
            )
            value = aggregates.get((catalog.measurer_id, definition.name, aggregation))
            if counts["measured"]:
                status = (
                    "partial" if counts["missing"] or counts["error"] else "measured"
                )
            elif counts["error"]:
                status = "error"
            elif identities and counts["not_applicable"] == len(identities):
                status = "not_applicable"
            else:
                status = "missing"
            if counts["measured"] and value is None:
                status = "not_aggregated"
            row: dict[str, Any] = {
                "measurer_id": catalog.measurer_id,
                "measurer_title": catalog.title,
                "category": catalog.category,
                "name": definition.name,
                "description": definition.description,
                "unit": definition.unit,
                "direction": definition.direction,
                "aggregation": aggregation,
                "value": value,
                "status": status,
                "coverage": {
                    key: counts[key]
                    for key in ("measured", "missing", "error", "not_applicable")
                },
            }
            if session is not None:
                row["evidence"] = evidence
                row["explanations"] = explanations
            values.append(row)
    return MeasurementView(values=tuple(values), session=session)
