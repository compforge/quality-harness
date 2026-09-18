"""Run persistence — the MODEL layer on disk, separate from the human report.

A run dir (``runs/<experiment>/<run_id>/``) holds three artifact layers:

  raw    ``requests.jsonl``（每请求一行的事实，含 warmup/drop）+ ``timeseries.csv``
         （probe 每 tick 采样）— append-only facts, never re-derived.
  model  ``run.json`` — the FULL serialized ``Run``（schema 版本化）: per arm_run the
         resources / load（含停止策略）/ stop / SLO 明细、metric registry（family →
         unit/kind/source/description）、每个 Window 的请求/资源 summary。
         内存模型知道的一切，离线同样可寻址。
  views  ``report.md/html`` + ``summary/by_facet.csv``（``report.py``）— 给人看的
         渲染，从模型导出，不是事实来源。

``write_run_data`` lays down raw + model. ``load_run`` reconstructs the ``Run``
from a run dir — so ``MetricStore(load_run(d).arm_runs)`` serves the SAME
``<family>{labels}.<stat>`` reads offline that the live process served, and SLO /
analysis can re-evaluate without re-firing. ``load_run`` restores all raw request
records and independent evaluations for re-slicing and recomputing percentiles.
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, fields
from pathlib import Path

from harness_common import (
    Artifact,
    Component,
    Environment,
    Forge,
    HttpOperation,
    KubernetesWorkload,
    Operation,
    OperationRun,
    Reducer,
    Repository,
    Service,
    Workload,
)

from perf_harness.drive.load import LoadPlan, Stage, Warmup
from perf_harness.metric import (
    CounterSummary,
    DistributionSummary,
    GaugeSummary,
    MetricFamily,
    MetricSummary,
    ScalarSummary,
)
from perf_harness.model import (
    Arm,
    ArmRun,
    ArmStop,
    Outcome,
    PhaseError,
    ProbeErrors,
    ProbeWindowObservation,
    ReportColumn,
    RequestStats,
    ResourceProfile,
    Run,
    Sample,
    Series,
    SloAssertion,
    SloCheck,
    StopSnapshot,
    Window,
    WindowSelector,
)
from perf_harness.records import RequestEvaluation, RequestRecord

#: bump when run.json's shape changes incompatibly — offline readers check this first
RUN_SCHEMA = 6


def _record(model, data: dict):
    """Read known fields while accepting additive fields from another schema-6 writer."""
    return model(**{f.name: data[f.name] for f in fields(model) if f.name in data})


# ---------------------------------------------------------------------------
# serializers (model → plain dict) and their inverses — kept adjacent per noun so
# the two directions can't drift apart silently
# ---------------------------------------------------------------------------


def _resources_json(c: ResourceProfile) -> dict:
    return {
        "cpu": c.cpu,
        "memory": c.memory,
        "workers": c.workers,
        "replicas": c.replicas,
        "extra": dict(c.extra),
        "label": c.label(),  # display convenience; derived, ignored on load
    }


def _resources_from(d: dict) -> ResourceProfile:
    return ResourceProfile(
        cpu=d.get("cpu"),
        memory=d.get("memory"),
        workers=d.get("workers"),
        replicas=int(d.get("replicas", 1)),
        extra=dict(d.get("extra") or {}),
    )


def _load_json(ld: LoadPlan) -> dict:
    data = asdict(ld)
    data["request_rate"] = "inf" if ld.saturated else ld.request_rate
    data["stages"] = [
        {**asdict(stage), "request_rate": "inf" if ld.saturated else stage.request_rate}
        for stage in ld.stages
    ]
    return data


def _load_from(d: dict) -> LoadPlan:
    return LoadPlan(
        **{
            **d,
            "request_rate": float(d["request_rate"]),
            "warmup": Warmup(**d.get("warmup", {})),
            "stages": tuple(
                Stage(**{**s, "request_rate": float(s["request_rate"])})
                for s in d.get("stages", [])
            ),
        }
    )


def _summary_json(s: MetricSummary) -> dict:
    """Typed summary → tagged dict; the ``kind`` tag picks the inverse on load."""
    caveats = sorted(s.caveats)
    if isinstance(s, CounterSummary):
        return {"kind": "counter", "total": s.total, "rate": s.rate, "increase": s.increase,
                "caveats": caveats}  # fmt: skip
    if isinstance(s, GaugeSummary):
        return {"kind": "gauge", "last": s.last, "mean": s.mean, "peak": s.peak,
                "caveats": caveats}  # fmt: skip
    if isinstance(s, DistributionSummary):
        return {"kind": "distribution", "n": s.n, "mean": s.mean, "p50": s.p50, "p95": s.p95,
                "p99": s.p99, "caveats": caveats}  # fmt: skip
    return {"kind": "scalar", "value": s.value, "caveats": caveats}


def _summary_from(d: dict) -> MetricSummary:
    caveats = frozenset(d.get("caveats") or [])
    kind = d["kind"]
    if kind == "counter":
        return CounterSummary(
            total=d["total"], rate=d.get("rate"), increase=d.get("increase"), caveats=caveats
        )
    if kind == "gauge":
        return GaugeSummary(last=d["last"], mean=d.get("mean"), peak=d.get("peak"), caveats=caveats)
    if kind == "distribution":
        return DistributionSummary(
            n=d["n"], mean=d["mean"], p50=d["p50"], p95=d["p95"], p99=d["p99"], caveats=caveats
        )
    return ScalarSummary(value=d["value"], caveats=caveats)


def _stats_json(s: RequestStats) -> dict:
    return {
        **asdict(s),
        "caveats": sorted(s.caveats),
        "metrics": {k: _summary_json(v) for k, v in s.metrics.items()},
    }


def _stats_from(d: dict) -> RequestStats:
    return RequestStats(
        **{
            **d,
            "caveats": frozenset(d.get("caveats", [])),
            "metrics": {k: _summary_from(v) for k, v in d.get("metrics", {}).items()},
        }
    )


def _family_json(f: MetricFamily) -> dict:
    return {
        "unit": f.unit,
        "side": f.side,
        "value_kind": f.value_kind,
        "source": f.source,
        "description": f.description,
        "labels": sorted(f.labels),
    }


def _family_from(name: str, d: dict) -> MetricFamily:
    return MetricFamily(
        name=name,
        unit=d.get("unit", ""),
        side=d["side"],
        value_kind=d["value_kind"],
        source=d.get("source", "client"),
        description=d.get("description", ""),
        labels=frozenset(d.get("labels") or []),
    )


def _stop_json(s: ArmStop) -> dict:
    out: dict = {
        "reason": s.reason,
        "inflight_at_stop": s.inflight_at_stop,
        "interrupted": s.interrupted,
        "force_cancelled": s.force_cancelled,
    }
    if s.snapshot:
        snap = s.snapshot
        out["snapshot"] = {
            "at_s": round(snap.at_s, 2),
            "completed": snap.completed,
            "errors": snap.errors,
            "error_rate": round(snap.error_rate, 4),
            "threshold": snap.threshold,
        }
    return out


def _stop_from(d: dict) -> ArmStop:
    snap = d.get("snapshot")
    return ArmStop(
        reason=d.get("reason", "deadline"),
        snapshot=StopSnapshot(
            at_s=snap["at_s"],
            completed=snap["completed"],
            errors=snap["errors"],
            error_rate=snap["error_rate"],
            threshold=snap["threshold"],
        )
        if snap
        else None,
        inflight_at_stop=d.get("inflight_at_stop", 0),
        interrupted=d.get("interrupted", 0),
        force_cancelled=d.get("force_cancelled", False),
    )


def _slo_json(c: SloCheck) -> dict:
    a = c.assertion
    return {
        "metric": a.metric,
        "op": a.op,
        # a `between` threshold is a (lo, hi) tuple — JSON carries it as a list
        "threshold": list(a.threshold) if isinstance(a.threshold, tuple) else a.threshold,
        "window": {
            "kind": a.window.kind,
            "name": a.window.name,
            "level": a.window.level,
        },
        "window_id": c.window_id,
        "observed": c.observed,
        "state": c.state,
    }


def _slo_from(d: dict) -> SloCheck:
    thr = d["threshold"]
    window = d.get("window") or {"kind": "measurement"}
    return SloCheck(
        assertion=SloAssertion(
            metric=d["metric"],
            op=d["op"],
            threshold=tuple(thr) if isinstance(thr, list) else thr,
            window=WindowSelector(
                kind=window.get("kind", "measurement"),
                name=window.get("name"),
                level=window.get("level"),
            ),
        ),
        observed=d.get("observed"),
        state=d["state"],
        window_id=d.get("window_id"),
    )


def _operation_json(call: OperationRun) -> dict:
    service = call.service
    return {
        "id": call.id,
        "service": {
            "name": service.name,
            "component": asdict(service.component),
            "environment": {"name": service.environment.name},
            "workloads": [asdict(w) for w in service.workloads],
        },
        "operation": asdict(call.operation),
        "outcome": asdict(call.outcome),
    }


def _operation_from(d: dict) -> OperationRun:
    target = d["service"]
    component = target["component"]
    repo = component["repository"]
    service = Service(
        name=target["name"],
        component=Component(
            name=component["name"],
            repository=Repository(forge=Forge(**repo["forge"]), path=repo["path"]),
        ),
        environment=Environment(name=target["environment"]["name"]),
        workloads=tuple(
            KubernetesWorkload(**w) if "location" in w else Workload(**w)
            for w in target.get("workloads", [])
        ),
    )
    operation = (
        HttpOperation(**d["operation"])
        if "method" in d["operation"]
        else Operation(**d["operation"])
    )
    return OperationRun(
        id=d["id"], service=service, operation=operation, outcome=_record(Outcome, d["outcome"])
    )


def _arm_run_json(r: ArmRun) -> dict:
    return {
        "id": r.label(),
        "service": r.service,
        "arm": {
            "id": r.arm.id,
            "resources": _resources_json(r.arm.resources),
            "load": _load_json(r.arm.load),
        },
        "stop": _stop_json(r.stop),
        "slo": [_slo_json(c) for c in r.slo],
        "registry": {name: _family_json(f) for name, f in r.metrics.items()},
        "windows": [
            {
                "id": window.id,
                "name": window.name,
                "kind": window.kind,
                "start_s": window.start_s,
                "end_s": window.end_s,
                "complete": window.complete,
                "target_level": window.target_level,
                "end_reason": window.end_reason,
                "limited_s": window.limited_s,
                "request": _stats_json(window.request) if window.request is not None else None,
                "by_case": {
                    case_id: _stats_json(stats) for case_id, stats in window.by_case.items()
                },
                "by_facet": {
                    key: {value: _stats_json(stats) for value, stats in values.items()}
                    for key, values in window.by_facet.items()
                },
                "probe_metrics": {
                    sid: _summary_json(summary) for sid, summary in window.probe_metrics.items()
                },
            }
            for window in r.windows
        ],
        "window_observations": [asdict(item) for item in r.window_observations],
        "probe_errors": {
            name: {"failures": e.failures, "ticks": e.ticks, "last": e.last}
            for name, e in r.probe_errors.items()
        },
        "phase_errors": [
            {"phase": e.phase, "error_type": e.error_type, "message": e.message}
            for e in r.phase_errors
        ],
    }


def _arm_run_from(d: dict, service: str) -> ArmRun:
    arm = d["arm"]
    return ArmRun(
        id=d["id"],
        window_observations=[
            ProbeWindowObservation(**item) for item in d.get("window_observations", [])
        ],
        service=d.get("service", service),
        arm=Arm(
            id=arm["id"],
            resources=_resources_from(arm["resources"]),
            load=_load_from(arm["load"]),
        ),
        windows=[
            Window(
                id=window["id"],
                name=window["name"],
                kind=window["kind"],
                start_s=float(window["start_s"]),
                end_s=float(window["end_s"]),
                complete=bool(window["complete"]),
                target_level=window.get("target_level"),
                end_reason=window.get("end_reason"),
                limited_s=float(window.get("limited_s", 0)),
                request=_stats_from(window["request"]) if window.get("request") else None,
                by_case={
                    case_id: _stats_from(stats)
                    for case_id, stats in (window.get("by_case") or {}).items()
                },
                by_facet={
                    key: {value: _stats_from(stats) for value, stats in values.items()}
                    for key, values in (window.get("by_facet") or {}).items()
                },
                probe_metrics={
                    sid: _summary_from(summary)
                    for sid, summary in (window.get("probe_metrics") or {}).items()
                },
            )
            for window in d.get("windows") or []
        ],
        series={},  # filled from timeseries.csv by load_run
        stop=_stop_from(d.get("stop") or {}),
        slo=[_slo_from(c) for c in d.get("slo") or []],
        metrics={name: _family_from(name, f) for name, f in (d.get("registry") or {}).items()},
        probe_errors={
            name: ProbeErrors(
                failures=e.get("failures", 0), ticks=e.get("ticks", 0), last=e.get("last", "")
            )
            for name, e in (d.get("probe_errors") or {}).items()
        },
        phase_errors=[
            PhaseError(
                phase=e["phase"],
                error_type=e.get("error_type", "Exception"),
                message=e.get("message", ""),
            )
            for e in d.get("phase_errors") or []
        ],
    )


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------


def write_timeseries_data(arm_runs: list[ArmRun], run_dir: str | Path) -> Path:
    """Persist the raw probe samples independently of report rendering."""
    path = Path(run_dir) / "timeseries.csv"
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["arm_run", "series", "t", "value"])
        for arm_run in arm_runs:
            for key, series in arm_run.series.items():
                for sample in series.samples:
                    # Preserve round-trippable floats in the raw layer. Display
                    # formatting belongs to report views, never persisted facts.
                    writer.writerow([arm_run.label(), key, repr(sample.t), repr(sample.value)])
    return path


def write_run_data(run: Run, run_dir: str | Path) -> dict[str, str]:
    """Write the raw + model layers under ``run_dir``.

    These diagnostic facts are committed before any optional report renderer runs,
    so an execution or rendering error still leaves a useful run artifact.
    """
    out = Path(run_dir)
    out.mkdir(parents=True, exist_ok=True)

    doc = {
        "schema": RUN_SCHEMA,
        "run_id": run.run_id,
        "experiment": run.experiment,
        "created_at": run.created_at,
        "service": run.service,
        "passed": run.passed,
        "report_columns": [asdict(column) for column in run.report_columns],
        "executions": [_arm_run_json(r) for r in run.arm_runs],
    }
    run_json = out / "run.json"
    run_json.write_text(json.dumps(doc, ensure_ascii=False, indent=2))

    requests = out / "requests.jsonl"
    with requests.open("w") as stream:
        for execution in run.arm_runs:
            calls = {call.id: call for call in execution.operation_runs}
            for record in execution.requests:
                row = {"arm_run_id": execution.id, "request": asdict(record)}
                if record.operation_run_id is not None:
                    row["operation_run"] = _operation_json(calls[record.operation_run_id])
                stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
    evaluations = out / "evaluations.json"
    evaluations.write_text(
        json.dumps(
            {
                e.id: {key: asdict(value) for key, value in e.evaluations.items()}
                for e in run.arm_runs
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    timeseries = write_timeseries_data(run.arm_runs, out)
    run.add_artifact("model", "run.json")
    run.add_artifact("requests", "requests.jsonl")
    run.add_artifact("evaluations", "evaluations.json")
    run.add_artifact("timeseries", "timeseries.csv")
    return {
        "run.json": str(run_json),
        "requests": str(requests),
        "evaluations": str(evaluations),
        "timeseries": str(timeseries),
    }


class PerfReducer(Reducer[Run]):
    """Persist perf raw/model facts without re-running the runner."""

    def reduce(self, run: Run, run_dir: Path) -> list[Artifact]:
        write_run_data(run, run_dir)
        names = {"model", "requests", "evaluations", "timeseries"}
        return [artifact for artifact in run.artifacts if artifact.name in names]


def load_run(run_dir: str | Path, *, with_series: bool = True) -> Run:
    """Reconstruct a ``Run`` from a run dir — the model layer back in memory, so
    ``MetricStore(load_run(d).arm_runs)`` serves the same ``<family>{labels}.<stat>``
    reads offline. ``with_series`` also reads ``timeseries.csv`` back into each
    arm_run's ``series`` (units resolved from the arm_run's registry). Request facts
    and independent evaluations are always loaded."""
    out = Path(run_dir)
    doc = json.loads((out / "run.json").read_text())
    schema = doc.get("schema")
    if schema != RUN_SCHEMA:
        raise ValueError(
            f"run.json schema {schema!r} not supported (expected {RUN_SCHEMA}); "
            f"re-run with a matching perf_harness or read the file directly"
        )
    service = doc.get("service", "")
    arm_runs = [_arm_run_from(d, service) for d in doc.get("executions") or []]
    ids = [execution.id for execution in arm_runs]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate ArmRun id in run.json; request ownership is ambiguous")

    ts = out / "timeseries.csv"
    if with_series and ts.exists():
        by_id = {r.label(): r for r in arm_runs}
        with ts.open() as f:
            for row in csv.DictReader(f):
                r = by_id.get(row["arm_run"])
                if r is None:
                    continue
                sid = row["series"]
                series = r.series.get(sid)
                if series is None:
                    # unit lives on the family (label-free) — derive family from the sid
                    fam = r.metrics.get(sid.split("{", 1)[0])
                    series = r.series[sid] = Series(metric=sid, unit=fam.unit if fam else "")
                series.samples.append(Sample(t=float(row["t"]), value=float(row["value"])))

    run = Run(
        run_id=doc["run_id"],
        experiment=doc["experiment"],
        created_at=doc.get("created_at", ""),
        executions=arm_runs,
        service=service,
        passed=bool(doc.get("passed", True)),
        report_columns=[
            ReportColumn(item["title"], item["metric"], WindowSelector(**item.get("window", {})))
            for item in doc.get("report_columns", [])
        ],
    )
    run.add_artifact("model", "run.json")
    by_id = {execution.id: execution for execution in arm_runs}
    with (out / "requests.jsonl").open() as stream:
        for line in stream:
            row = json.loads(line)
            execution = by_id[row["arm_run_id"]]
            execution.requests.append(_record(RequestRecord, row["request"]))
            if row.get("operation_run") is not None:
                execution.operation_runs.append(_operation_from(row["operation_run"]))
    evaluations = json.loads((out / "evaluations.json").read_text())
    for key, values in evaluations.items():
        by_id[key].evaluations = {
            identity: _record(RequestEvaluation, value) for identity, value in values.items()
        }
    run.add_artifact("requests", "requests.jsonl")
    run.add_artifact("evaluations", "evaluations.json")
    if ts.exists():
        run.add_artifact("timeseries", "timeseries.csv")
    return run
