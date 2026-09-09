"""Explicit effect-cost comparison projection for trajectory reports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

from harness_common.report_kit import Section, Table
from trajectory_harness.metrics import (
    Metric,
    MetricAggregation,
    TrajectoryAnalysisRun,
)

Objective = Literal["maximize", "minimize"]


@dataclass(frozen=True, slots=True)
class MetricSelector:
    """Select one Metric per run for an explicitly owned comparison objective."""

    name: str
    aggregation: MetricAggregation
    objective: Objective
    dimensions: tuple[tuple[str, str], ...] = ()

    def matches(self, metric: Metric) -> bool:
        actual = dict(metric.dimensions)
        return (
            metric.name == self.name
            and metric.aggregation == self.aggregation
            and all(actual.get(key) == value for key, value in self.dimensions)
        )


@dataclass(frozen=True, slots=True)
class ParetoSpec:
    """Domain-selected effect and cost axes; the Harness never guesses them."""

    effect: MetricSelector
    cost: MetricSelector


def pareto_section(
    run_metrics: Sequence[tuple[TrajectoryAnalysisRun, tuple[Metric, ...]]],
    spec: ParetoSpec,
) -> Section:
    rows = []
    issues = []
    points = []
    for run, metrics in run_metrics:
        effects = [metric for metric in metrics if spec.effect.matches(metric)]
        costs = [metric for metric in metrics if spec.cost.matches(metric)]
        if len(effects) != 1 or len(costs) != 1:
            issues.append(
                [
                    run.run_id,
                    run.dataset_id,
                    str(len(effects)),
                    str(len(costs)),
                ]
            )
            continue
        points.append((run, effects[0], costs[0]))

    for index, (run, effect, cost) in enumerate(points):
        dominated = any(
            _dominates(
                other_effect.value,
                other_cost.value,
                effect.value,
                cost.value,
                spec,
            )
            for other_index, (_, other_effect, other_cost) in enumerate(points)
            if other_index != index
        )
        target_dimensions = tuple(
            item for item in spec.effect.dimensions if item[0] == "target"
        )
        completion = _summary_metric(
            run_metrics,
            run,
            "execution.completion",
            "rate",
            target_dimensions,
        )
        duration_p95 = _summary_metric(
            run_metrics,
            run,
            "execution.duration_ms",
            "p95",
            target_dimensions,
        )
        rows.append(
            [
                run.run_id,
                run.dataset_id,
                run.dataset_version or "—",
                _number(effect.value),
                _number(cost.value),
                _number(completion.value) if completion else "—",
                _number(duration_p95.value) if duration_p95 else "—",
                "yes" if dominated else "no",
            ]
        )

    blocks = []
    if rows:
        blocks.append(
            Table(
                columns=[
                    "Run",
                    "Dataset",
                    "Version",
                    "Effect",
                    "Cost",
                    "Completion",
                    "Duration p95",
                    "Dominated",
                ],
                rows=rows,
            )
        )
    if issues:
        blocks.append(
            Table(
                columns=["Run", "Dataset", "Effect matches", "Cost matches"],
                rows=issues,
            )
        )
    return Section(heading="Effect-cost Pareto comparison", blocks=blocks)


def _dominates(
    candidate_effect: float,
    candidate_cost: float,
    effect: float,
    cost: float,
    spec: ParetoSpec,
) -> bool:
    effect_better = _at_least_as_good(candidate_effect, effect, spec.effect.objective)
    cost_better = _at_least_as_good(candidate_cost, cost, spec.cost.objective)
    strictly_better = candidate_effect != effect or candidate_cost != cost
    return effect_better and cost_better and strictly_better


def _at_least_as_good(candidate: float, current: float, objective: Objective) -> bool:
    return candidate >= current if objective == "maximize" else candidate <= current


def _summary_metric(
    run_metrics: Sequence[tuple[TrajectoryAnalysisRun, tuple[Metric, ...]]],
    selected_run: TrajectoryAnalysisRun,
    name: str,
    aggregation: MetricAggregation,
    dimensions: tuple[tuple[str, str], ...],
) -> Metric | None:
    matches = [
        metric
        for run, metrics in run_metrics
        if (
            run.run_id,
            run.dataset_id,
            run.dataset_version,
        )
        == (
            selected_run.run_id,
            selected_run.dataset_id,
            selected_run.dataset_version,
        )
        for metric in metrics
        if (
            metric.name == name
            and metric.aggregation == aggregation
            and all(
                dict(metric.dimensions).get(key) == value for key, value in dimensions
            )
        )
    ]
    return matches[0] if len(matches) == 1 else None


def _number(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return f"{value:.4f}".rstrip("0").rstrip(".")
