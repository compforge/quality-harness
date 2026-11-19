"""Compile target-owned Common observations into Python Perf window measurements."""

from collections.abc import Mapping, Sequence
from dataclasses import replace

from harness_common import HttpOperation, Operation
from harness_toolbox.metricdb import MetricDBDataSource, bind_metricdb_observations

from perf_harness.model import RED_UNITS, Service, WindowSelector
from perf_harness.observe.definition import ObservationSpec
from perf_harness.observe.prometheus import WindowQuery
from perf_harness.observe.prometheus_query import PrometheusQueryProbe

_RED_CATEGORIES = {f"red:{role}": role for role in RED_UNITS}


def operation_metric_observation(
    service: Service,
    operation: Operation,
    data_source: MetricDBDataSource,
    windows: Sequence[WindowSelector],
    *,
    step_s: float = 15,
    parameters: Mapping[str, str] | None = None,
) -> ObservationSpec:
    """Plan a window summary for each declared MetricDB measurement, without I/O."""
    selected = bind_metricdb_observations(
        operation.observes or (), data_source.kind, parameters or {}
    )
    roles: dict[str, str] = {}
    summaries: list[WindowQuery] = []
    qps_semantics = "completed"
    response_modes = set()
    for definition in selected:
        if definition.response_mode == "inherit":
            response_modes.add(
                operation.response_mode if isinstance(operation, HttpOperation) else None
            )
        else:
            response_modes.add(definition.response_mode)
        for name, metric in definition.metrics.items():
            role = _RED_CATEGORIES.get(metric.category)
            if role is None:
                role = f"metric:{name}" if name in RED_UNITS or name.startswith("metric:") else name
            if role in roles:
                raise ValueError(f"Duplicate metric role {role} for {operation.id()}")
            series = f"{operation.id()}.{name}"
            roles[role] = series
            if role == "qps" and metric.semantics == "entered":
                qps_semantics = "entered"
            description = "; ".join(filter(None, (metric.description, metric.semantics)))
            summaries.extend(
                WindowQuery(
                    series,
                    metric.expression,
                    metric.unit,
                    description,
                    window=window,
                    histogram=metric.histogram,
                )
                for window in windows
            )
    if len(response_modes) != 1:
        raise ValueError(
            f"{operation.id()} requires MetricDB observations with a consistent response mode"
        )
    probe = PrometheusQueryProbe(
        service=service.name,
        operation=operation,
        data_source=data_source,
        step_s=step_s,
        summaries=summaries,
        metrics=roles,
        qps_semantics=qps_semantics,
    )
    binding = probe.observation_binding
    assert binding is not None
    probe.observation_binding = replace(binding, response_mode=response_modes.pop())
    return ObservationSpec(service, (probe,), operation)
