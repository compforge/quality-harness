"""Collect saved Prometheus queries independently of load and offline reporting."""

import json
import logging
import time
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from harness_common import ClientManager
from harness_toolbox.data_loader import DataLoader
from harness_toolbox.prometheus import PrometheusDataSource

from perf_harness.metric import ScalarSummary, series_id, split_series
from perf_harness.metric.reduce import time_series_summary
from perf_harness.model import ArmRun, ProbeErrors, PrometheusObservation, Run, Sample, Series
from perf_harness.observe.base import Probe
from perf_harness.observe.histogram import histogram_summary
from perf_harness.observe.prometheus import PrometheusQuery, finite_number, project_result
from perf_harness.observe.prometheus_query import PrometheusQueryProbe

logger = logging.getLogger(__name__)


def _project(arm: ArmRun, observation: PrometheusObservation, *, fraction: bool = False) -> None:
    """Apply one complete query atomically; malformed/partial results never become a zero."""
    if observation.data is None:
        raise ValueError("Prometheus query has no result")
    if observation.warnings:
        raise ValueError("Prometheus query returned warnings; observation may be incomplete")
    if observation.histogram is not None:
        summary = histogram_summary(observation.data, observation.histogram)
        window = next(w for w in arm.windows if w.id == observation.window_id)
        if summary is not None:
            window.probe_metrics[series_id(observation.metric, observation.labels)] = summary
        return
    query = PrometheusQuery(
        observation.metric, observation.expression, labels=observation.result_labels
    )
    if observation.step_s is None:
        values = project_result(query, observation.data)
        if fraction and any(not 0 <= value <= 1 for value in values.values()):
            raise ValueError("ratio observation must be between zero and one")
        if not values:
            raise ValueError("Prometheus query returned no samples")
        window = next(w for w in arm.windows if w.id == observation.window_id)
        for key, value in values.items():
            metric, labels = split_series(key)
            window.probe_metrics[series_id(metric, {**observation.labels, **labels})] = (
                ScalarSummary(value)
            )
        return

    if observation.data["resultType"] != "matrix" or arm.started_at_s is None:
        raise ValueError("Prometheus history requires a matrix and a recorded Unix origin")
    collected: dict[str, list[Sample]] = {}
    seen = set()
    for row in observation.data["result"]:
        if "histograms" in row:
            raise ValueError("Native histogram results need explicit PromQL projection")
        labels = {key: value for key, value in row["metric"].items() if key != "__name__"}
        if set(labels) != set(observation.result_labels):
            raise ValueError("Prometheus range query returned undeclared or missing labels")
        sid = series_id(observation.metric, {**observation.labels, **labels})
        if sid in seen:
            raise ValueError("Prometheus range query returned duplicate series")
        seen.add(sid)
        samples = []
        previous = None
        for timestamp, value in row["values"]:
            timestamp = finite_number(timestamp)
            if not observation.start_s <= timestamp <= observation.end_s:
                raise ValueError("Prometheus sample lies outside the requested window")
            if previous is not None and timestamp <= previous:
                raise ValueError("Prometheus sample timestamps must increase")
            samples.append(Sample(timestamp - arm.started_at_s, finite_number(value)))
            previous = timestamp
        if samples:
            collected[sid] = samples
    if not collected:
        raise ValueError("Prometheus query returned no samples")
    descriptor = arm.metrics[observation.metric]
    for sid, samples in collected.items():
        arm.series[sid] = Series(observation.metric.rsplit(".", 1)[-1], descriptor.unit, samples)
        for window in arm.windows:
            selected = [s for s in samples if window.start_s <= s.t < window.end_s]
            summary = time_series_summary(selected, descriptor.value_kind)
            if summary is not None:
                window.probe_metrics[sid] = summary


def _clear(arm: ArmRun) -> None:
    """Replace only this collection's metrics; preserve load and other observations."""
    queries = arm.prometheus_observations
    for mapping in [arm.series, *(w.probe_metrics for w in arm.windows)]:
        for key in list(mapping):
            metric, labels = split_series(key)
            if any(
                q.metric == metric and all(labels.get(k) == v for k, v in q.labels.items())
                for q in queries
            ):
                del mapping[key]
    for query in queries:
        arm.probe_errors.pop(query.probe, None)


def sources_for(probes: list[Probe]) -> dict[str, PrometheusDataSource]:
    sources = {}
    for probe in probes:
        if isinstance(probe, PrometheusQueryProbe):
            source = probe.data_source
            if source.url in sources and sources[source.url].client_key != source.client_key:
                raise ValueError("Prometheus probes sharing a URL must share access options")
            sources[source.url] = source
    return sources


def plan_history(probes: list[Probe], arm: ArmRun) -> None:
    """Freeze adapter-owned queries once the actual execution windows are known."""
    for probe in probes:
        if isinstance(probe, PrometheusQueryProbe):
            arm.prometheus_observations.extend(probe.plan(arm))


async def collect_metrics(run: Run, sources: dict[str, PrometheusDataSource]) -> None:
    """Query frozen windows. Neither retry nor a metrics failure sends load requests.

    @spec Missing, non-finite and partial data remain explicit failures; request outcomes
          and lifecycle status are unchanged by metric collection.
    """
    fractions = {
        metric.metric
        for binding in run.observation_bindings
        for name, metric in binding.metrics.items()
        if name == "error_rate"
    }
    required = {q.source_url for arm in run.arm_runs for q in arm.prometheus_observations}
    if required - sources.keys():
        raise ValueError("Prometheus configuration does not match saved query sources")
    async with ClientManager() as clients, DataLoader() as scope:
        for arm in run.arm_runs:
            _clear(arm)
            for query in arm.prometheus_observations:
                query.data, query.error = None, None
                query.warnings, query.infos = (), ()
                query.collected_at_s = time.time()
                logger.info(
                    "perf metric query started: run=%s arm=%s probe=%s metric=%s",
                    run.run_id,
                    arm.id,
                    query.probe,
                    query.metric,
                )
                try:
                    client = await clients.get(sources[query.source_url])
                    if query.step_s is None:
                        results = await client.read(
                            [query.expression], timestamp=query.end_s, scope=scope
                        )
                    else:
                        results = await client.read_range(
                            [query.expression],
                            start=query.start_s,
                            end=query.end_s,
                            step=query.step_s,
                            scope=scope,
                        )
                    result = results[query.expression]
                    query.data, query.warnings, query.infos = (
                        result.data,
                        result.warnings,
                        result.infos,
                    )
                    _project(
                        arm,
                        query,
                        fraction=series_id(query.metric, query.labels) + ".value" in fractions,
                    )
                    logger.info(
                        "perf metric query completed: run=%s arm=%s probe=%s "
                        "metric=%s elapsed_s=%.3f",
                        run.run_id,
                        arm.id,
                        query.probe,
                        query.metric,
                        time.time() - query.collected_at_s,
                    )
                except Exception as exc:
                    query.error = f"{type(exc).__name__}: {exc}"
                    previous = arm.probe_errors.get(query.probe, ProbeErrors(0, 0, ""))
                    arm.probe_errors[query.probe] = ProbeErrors(
                        previous.failures + 1, 0, query.error
                    )
                    logger.warning(
                        "perf metric query failed: run=%s arm=%s probe=%s metric=%s error_type=%s",
                        run.run_id,
                        arm.id,
                        query.probe,
                        query.metric,
                        type(exc).__name__,
                    )
            for name, error in list(arm.probe_errors.items()):
                total = sum(q.probe == name for q in arm.prometheus_observations)
                if total:
                    arm.probe_errors[name] = ProbeErrors(error.failures, total, error.last)


def archive_collection(run: Run, directory: Path) -> Path:
    """Append an immutable attempt before replacing a run's latest metric projection."""
    folder = directory / "metric-collections"
    folder.mkdir(exist_ok=True)
    path = folder / f"{time.time_ns()}-{uuid4().hex[:8]}.json"
    path.write_text(
        json.dumps(
            {
                "run_id": run.run_id,
                "executions": {
                    arm.id: [asdict(q) for q in arm.prometheus_observations] for arm in run.arm_runs
                },
            },
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
    )
    return path


async def recollect_metrics(run_dir: str, config_path: str | None = None) -> Run:
    """Use only source configuration and saved queries, without importing project runners."""
    import yaml

    from perf_harness.observe.configuration import parse_prometheus
    from perf_harness.runio import load_run, write_metric_data

    directory = Path(run_dir)
    run = load_run(directory)
    if not any(arm.prometheus_observations for arm in run.arm_runs):
        raise ValueError("Run has no saved Prometheus queries; no load will be generated")
    raw = yaml.safe_load(Path(config_path or directory / "config.yaml").read_text())
    sources = {}
    configurations = (
        raw.get("data_sources", {}).values() if "observation" in raw else [raw.get("prometheus")]
    )
    for configuration in configurations:
        if configuration is None:
            continue
        options = dict(configuration)
        kind = options.pop("type", "prometheus")
        if kind != "prometheus":
            raise ValueError(f"unsupported data source type: {kind}")
        source, _ = parse_prometheus(options)
        assert source is not None
        if source.url in sources and sources[source.url].client_key != source.client_key:
            raise ValueError("Prometheus sources sharing a URL must share access options")
        sources[source.url] = source
    if not sources:
        raise ValueError("Metric collection requires a Prometheus data source")
    # Preserve the original failed attempt as well as each subsequent one.
    archive_collection(run, directory)
    await collect_metrics(run, sources)
    archive_collection(run, directory)
    write_metric_data(run, directory)
    return run
