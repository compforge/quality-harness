"""Engine — pure orchestration: resolve Arms and execute one ArmRun per Arm.

One ArmRun: deploy the ResourceProfile (via the Experiment's Deployer, if any) →
open a client → drive the load (``drive.scheduler``) while the observer samples
every Probe (``observe.observe_loop``) → collapse outcomes + series into a
ArmRun (``_aggregate``, minting via ``metric.reduce``). The grid is swept
resources-outer so each profile is provisioned once and all load levels run
under it. Execution detail lives in the packages; this module only wires phases.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import Counter
from dataclasses import asdict, dataclass, field, replace

from harness_common import ClientManager, Deployer
from harness_common import Experiment as BaseExperiment
from harness_common.overlay import Overlay
from harness_toolbox.http import HTTPClientProvider
from spec_case.model import Case

from perf_harness.drive.load import LoadPlan
from perf_harness.drive.runner import ArmContext, Runner
from perf_harness.drive.scheduler import DriveState, drive
from perf_harness.judge import Judge, default_judge
from perf_harness.metric import (
    MetricFamily,
    ScalarSummary,
    series_id,
    split_series,
)
from perf_harness.metric.reduce import reduce_requests, unit_of
from perf_harness.metric.store import PER_REQUEST_DESCRIPTORS, REQUEST_DESCRIPTORS
from perf_harness.model import (
    Arm,
    ArmRun,
    Deployment,
    Phase,
    PhaseError,
    ProbeErrors,
    ReportColumn,
    ResourceProfile,
    Run,
    Sample,
    Series,
    Service,
    SloAssertion,
    Window,
    make_run_id,
)
from perf_harness.observe import (
    ClientStats,
    Probe,
    ProbeContext,
    ProbeStore,
    observe_loop,
)
from perf_harness.slo import evaluate_slo


def _phase_error(phase: Phase, exc: Exception) -> PhaseError:
    return PhaseError(phase=phase, error_type=type(exc).__name__, message=str(exc))


@dataclass
class _ArmExecution:
    """One ArmRun's lifecycle state, carried through every phase to finalization.

    ``ArmContext`` is the immutable runner-facing input. This private state
    machine preserves ordered diagnostics while the lifecycle continues to its
    mandatory cleanup and one final ArmRun exit.
    """

    context: ArmContext
    drive: DriveState = field(default_factory=DriveState)
    phase: Phase = "setup"
    phase_errors: list[PhaseError] = field(default_factory=list)
    fatal_error: BaseException | None = None

    def enter(self, phase: Phase) -> None:
        self.phase = phase

    def record(self, exc: Exception, *, phase: Phase | None = None) -> None:
        self.phase_errors.append(_phase_error(phase or self.phase, exc))


@dataclass(kw_only=True)
class Experiment(BaseExperiment):
    """A named, reproducible perf study — one config = one Experiment.

    Its arms are the resources × load sweep. ``cases`` is the pool the Engine picks
    from each fire (the *what*); the pick is weighted by ``mix`` (the experiment
    runner mix), NOT by the case — the same case is reusable across experiments at
    different weights. Empty pool → one anonymous Case. ``facet_order`` gives ordered
    facets their report sort order.
    """

    service: Service
    runner: Runner
    resources: list[ResourceProfile]
    loads: list[LoadPlan]
    judge: Judge = default_judge
    deployer: Deployer[Deployment] | None = None
    probes: list[Probe] = field(default_factory=list)
    cases: list[Case] = field(default_factory=list)
    # experiment runner mix: load weight per case.id (unwritten → 1.0; unknown id → error).
    # An Overlay, not a Case field — weight is "how this run uses the case", not its identity.
    mix: Overlay = field(default_factory=Overlay)
    facet_order: dict[str, list[str]] = field(default_factory=dict)
    slo: list[SloAssertion] = field(default_factory=list)  # per-run gate (aggregate verdict)
    abort_on_fail: bool = False  # stop the sweep at the first SLO-failing arm_run
    strict_slo: bool = False  # treat a skipped SLO (no data) as a run failure, not a pass
    name: str = "perf"  # experiment name → runs/<name>/<run_id>/
    observe_interval_s: float = 5.0
    cooldown_s: float = 0.0  # keep probes running after deactivation for scale-down curves
    teardown: bool = False
    report_columns: list[ReportColumn] = field(default_factory=list)

    def resolved_arms(self) -> list[Arm]:
        """Expand the configured resource × load axes into named comparison Arms."""
        expanded = [
            (f"{resources.label()}|{load.label()}", resources, load)
            for resources in self.resources
            for load in self.loads
        ]
        counts = Counter(base for base, _, _ in expanded)
        arms: list[Arm] = []
        for base, resources, load in expanded:
            arm_id = base
            if counts[base] > 1:
                payload = json.dumps(
                    {"resources": asdict(resources), "load": asdict(load)},
                    sort_keys=True,
                    separators=(",", ":"),
                )
                arm_id = f"{base}@{hashlib.sha256(payload.encode()).hexdigest()[:8]}"
            arms.append(Arm(id=arm_id, resources=resources, load=load))
        ids = [arm.id for arm in arms]
        if len(set(ids)) != len(ids):
            raise ValueError(f"duplicate arm id: {ids}")
        return arms


class Engine:
    """Runs one Experiment → a Run (run_id + one ArmRun per Arm)."""

    def __init__(self, experiment: Experiment, *, run_id: str | None = None) -> None:
        self.experiment = experiment
        self.run_id = run_id or make_run_id()  # threaded to Runner.fire + the run dir
        # empty pool → one anonymous Case; weights come from the experiment mix (Overlay
        # keyed by case.id, default 1.0), not from the case — see Experiment.mix.
        self._cases = experiment.cases or [Case(id="default", input={})]
        _mix = experiment.mix.resolve((c.id for c in self._cases), default_of=lambda _: 1.0)
        self._weights = [max(_mix[c.id], 0.0) for c in self._cases]

    async def run(self) -> Run:
        exp = self.experiment
        started = time.strftime("%Y-%m-%dT%H:%M:%S")
        arm_runs: list[ArmRun] = []
        passed = True
        try:
            applied: ResourceProfile | None = None
            for arm in exp.resolved_arms():
                if exp.deployer is not None and arm.resources != applied:
                    await exp.deployer.deploy(
                        Deployment(service=exp.service, resources=arm.resources)
                    )
                    applied = arm.resources
                arm_run = await self._run_arm_run(exp.service, arm)
                # A breaker-ended arm_run only observed a partial load window. Even
                # if every evaluated SLO happens to pass, it cannot prove this
                # load level was sustained.
                failed = (
                    arm_run.stop.early or bool(arm_run.phase_errors) or arm_run.stop.interrupted > 0
                )
                if exp.slo and not arm_run.phase_errors:
                    arm_run.slo = evaluate_slo(arm_run, exp.slo)
                    # the run gate is lenient on skip by default: a skipped check
                    # (slice absent) is surfaced but doesn't flip the exit code;
                    # strict_slo treats an unverifiable SLO as a failure.
                    failed = (
                        failed
                        or any(c.failed for c in arm_run.slo)
                        or any(
                            c.skipped and (exp.strict_slo or c.assertion.window.kind == "cooldown")
                            for c in arm_run.slo
                        )
                    )
                passed = passed and not failed
                arm_runs.append(arm_run)
                # A phase error says the testbed/execution chain itself is no
                # longer trustworthy. Continuing the sweep can compound leaked
                # state, so it stops independently of the SLO-oriented policy.
                if arm_run.phase_errors or (exp.abort_on_fail and failed):
                    break
        finally:
            if exp.teardown and exp.deployer is not None:
                await exp.deployer.teardown()
        return Run(
            run_id=self.run_id,
            experiment=exp.name,
            created_at=started,
            executions=arm_runs,
            service=exp.service.name,
            passed=passed,
            report_columns=list(exp.report_columns),
        )

    async def _run_arm_run(self, service: Service, arm: Arm) -> ArmRun:
        exp = self.experiment
        profile, load = arm.resources, arm.load
        stats = ClientStats()
        cap = load.peak_inflight
        result = ArmRun(
            id=f"{self.run_id}:{arm.id}", service=service.name, arm=arm, windows=[], series={}
        )
        store: ProbeStore = {}
        probe_errors: dict[str, ProbeErrors] = {}
        cooldown_start_s: float | None = None
        cooldown_end_s: float | None = None
        # trust_env=False: the load generator connects DIRECTLY to the Service's
        # base_url — never via an ambient HTTP(S)_PROXY/ALL_PROXY from the shell (a
        # stray proxy env would silently reroute or, if malformed, crash client
        # creation). A real proxy, if ever needed, should be explicit Service access config.
        async with ClientManager() as clients:
            client = await clients.get(HTTPClientProvider(cap, 120, pool="load"))
            obs_client = await clients.get(HTTPClientProvider(8, 10, pool="observe"))
            execution = _ArmExecution(
                context=ArmContext(
                    service=service,
                    client=client,
                    run_id=self.run_id,
                    resources=profile,
                    load=load,
                )
            )
            ctx: ProbeContext | None = None
            observer: asyncio.Task | None = None
            stop = asyncio.Event()
            start_observation = asyncio.Event()
            try:
                await exp.runner.setup(execution.context)
                execution.enter("warmup")
                ctx = ProbeContext(
                    service=service,
                    client=client,
                    t0=time.monotonic(),
                    stats=stats,
                    observer_client=obs_client,
                    clients=clients,
                    observation_budget_s=load.duration_s + load.cooldown_timeout_s + exp.cooldown_s,
                )
                ready = asyncio.Event()
                observer = asyncio.create_task(
                    observe_loop(
                        exp.probes,
                        ctx,
                        store,
                        stop,
                        exp.observe_interval_s,
                        ready,
                        start_observation,
                    )
                )
                initial = asyncio.create_task(ready.wait())
                try:
                    await asyncio.wait((initial, observer), return_when=asyncio.FIRST_COMPLETED)
                    if observer.done():
                        await observer
                finally:
                    initial.cancel()
                    await asyncio.gather(initial, return_exceptions=True)
                # Baseline collection must not consume the requested load duration.
                now = time.monotonic()
                offset = now - ctx.t0
                for key, samples in store.items():
                    store[key] = [Sample(sample.t - offset, sample.value) for sample in samples]
                ctx.t0 = now
                start_observation.set()
                await drive(
                    exp.runner,
                    exp.judge,
                    execution.context,
                    ctx,
                    self._cases,
                    self._weights,
                    result,
                    execution.drive,
                )
                execution.enter("cooldown")
                await exp.runner.deactivate(execution.context)
                if exp.cooldown_s:
                    execution.enter("cooldown")
                    cooldown_start_s = time.monotonic() - ctx.t0
                    await asyncio.sleep(exp.cooldown_s)
            except Exception as exc:
                if execution.phase in ("warmup", "hold"):
                    execution.enter(execution.drive.phase)
                execution.record(exc)
            except BaseException as exc:
                # Cancellation / process-level interrupts remain control flow rather
                # than persisted test results, but cleanup must still not hide them.
                execution.fatal_error = exc
            finally:
                if observer is not None:
                    stop.set()
                    start_observation.set()
                    if execution.fatal_error is not None:
                        observer.cancel()
                    try:
                        probe_errors = await observer
                    except Exception as exc:
                        execution.record(exc)
                    except BaseException as exc:
                        if execution.fatal_error is None:
                            execution.fatal_error = exc
                        else:
                            execution.fatal_error.add_note(
                                f"observer finalization also failed: {exc!r}"
                            )
                    else:
                        if cooldown_start_s is not None and ctx is not None:
                            # The observer takes one final tick after ``stop`` wakes it.
                            # Close the Window only after that tick has been recorded.
                            cooldown_end_s = time.monotonic() - ctx.t0
                try:
                    result = self._aggregate(
                        arm,
                        result,
                        store,
                        probe_errors,
                        measurement_end_s=execution.drive.measurement_end_s or 0.0,
                        drive_state=execution.drive,
                        cooldown_start_s=cooldown_start_s,
                        cooldown_end_s=cooldown_end_s,
                        observation_end_s=time.monotonic() - ctx.t0 if ctx else None,
                    )
                    if ctx is not None and execution.fatal_error is None:
                        await self._finish_probes(ctx, result)
                except Exception as exc:
                    execution.record(exc, phase="cooldown")
                try:
                    await exp.runner.cleanup(execution.context)
                except BaseException as cleanup_error:
                    if execution.fatal_error is not None:
                        execution.fatal_error.add_note(f"cleanup also failed: {cleanup_error!r}")
                    elif isinstance(cleanup_error, Exception):
                        execution.record(cleanup_error, phase="cleanup")
                    else:
                        execution.fatal_error = cleanup_error
                if execution.fatal_error is not None:
                    raise execution.fatal_error

        result.stop = execution.drive.stop
        result.phase_errors = execution.phase_errors
        return result

    async def _finish_probes(self, ctx: ProbeContext, result: ArmRun) -> None:
        windows = {window.id: window for window in result.windows}
        for probe in self.experiment.probes:
            try:
                observations = await probe.finish(ctx, result.windows)
            except Exception as exc:
                previous = result.probe_errors.get(probe.name, ProbeErrors(0, 0, ""))
                result.probe_errors[probe.name] = ProbeErrors(
                    previous.failures + 1, previous.ticks + 1, str(exc)
                )
                continue
            result.window_observations.extend(observations)
            for observation in observations:
                if observation.error:
                    previous = result.probe_errors.get(probe.name, ProbeErrors(0, 0, ""))
                    result.probe_errors[probe.name] = ProbeErrors(
                        previous.failures + 1, previous.ticks + 1, observation.error
                    )
                    continue
                for key, value in observation.values.items():
                    bare, extra = split_series(key)
                    sid = series_id(f"{probe.family}.{bare}", {**probe.labels, **extra})
                    windows[observation.window_id].probe_metrics[sid] = ScalarSummary(value)

    def _aggregate(
        self,
        arm: Arm,
        result: ArmRun,
        store: ProbeStore,
        probe_errors: dict[str, ProbeErrors] | None = None,
        *,
        measurement_end_s: float | None = None,
        drive_state: DriveState | None = None,
        cooldown_start_s: float | None = None,
        cooldown_end_s: float | None = None,
        observation_end_s: float | None = None,
    ) -> ArmRun:
        exp = self.experiment
        load = arm.load
        warmup = drive_state.measurement_start_s if drive_state else 0.0
        measured_end = measurement_end_s if measurement_end_s is not None else load.duration_s

        windows = [
            Window(
                id="measurement",
                name="measurement",
                kind="measurement",
                start_s=warmup,
                end_s=max(warmup, measured_end),
                complete=measured_end >= load.duration_s,
            )
        ]
        clock = 0.0
        for index, stage in enumerate(() if drive_state else load.planned_stages):
            stage_start, stage_end = clock, clock + stage.duration_s
            start, end = max(stage_start, warmup), min(stage_end, measured_end)
            if end > start:
                windows.append(
                    Window(
                        id=f"stage-{index}",
                        name=stage.label,
                        kind=stage.kind,
                        start_s=start,
                        end_s=end,
                        complete=measured_end >= stage_end,
                        target_level=stage.level,
                    )
                )
            clock = stage_end
        if drive_state:
            windows[0].complete = drive_state.stop.reason == "deadline" and any(
                w.kind == "hold" and w.complete for w in drive_state.windows
            )
            windows[0].limited_s = sum(w.limited_s for w in drive_state.windows if w.kind == "hold")
            windows.extend(drive_state.windows)
        if drive_state and cooldown_end_s is not None and drive_state.windows:
            drive_state.windows[-1].end_s = cooldown_end_s
        elif cooldown_start_s is not None and cooldown_end_s is not None:
            windows.append(
                Window(
                    id="cooldown",
                    name="cooldown",
                    kind="cooldown",
                    start_s=cooldown_start_s,
                    end_s=cooldown_end_s,
                    complete=True,
                )
            )

        drain_end = max([measured_end, *(r.finished_at or measured_end for r in result.requests)])
        if not drive_state and drain_end > measured_end:
            windows.append(
                Window(
                    id="cooldown",
                    name="cooldown",
                    kind="cooldown",
                    start_s=measured_end,
                    end_s=drain_end + 1e-12,
                    complete=True,
                )
            )

        if observation_end_s is not None and any(p.needs_observation_window for p in exp.probes):
            baseline = min(
                (sample.t for samples in store.values() for sample in samples), default=0.0
            )
            windows.append(
                Window(
                    id="observation",
                    name="observation",
                    kind="observation",
                    start_s=min(0.0, baseline),
                    end_s=observation_end_s,
                    complete=(not drive_state.stop.early and not drive_state.stop.interrupted)
                    if drive_state
                    else True,
                )
            )
        result.windows = windows
        for window in windows:
            window.request = reduce_requests(result, window.start_s, window.end_s)
            case_ids = {r.case_id for r in result.requests}
            window.by_case = {
                case_id: reduce_requests(result, window.start_s, window.end_s, case_id=case_id)
                for case_id in case_ids
            }
            for key in {k for r in result.requests for k in r.facets}:
                window.by_facet[key] = {
                    value: reduce_requests(result, window.start_s, window.end_s, facet=(key, value))
                    for value in {r.facets[key] for r in result.requests if key in r.facets}
                }

        measurement = windows[0]
        assert measurement.request is not None

        # unified metric registry: every report/SLO-visible metric → its descriptor.
        # (1) builtin request.* (duration_ms / error_rate / throughput_rps /
        # drop_rate). (2) per-request distributions: a Runner may declare
        # unit/source (describe()); undeclared keys (e.g. dynamic first_<event>_ms)
        # are inferred — always a distribution, source=client.
        registry: dict[str, MetricFamily] = {d.name: d for d in REQUEST_DESCRIPTORS}
        # per-request descriptor precedence: Runner-declared > framework (first_byte_ms) > inferred
        known = {d.name: d for d in PER_REQUEST_DESCRIPTORS}
        known.update({m.name: m for m in exp.runner.describe()})
        for key in measurement.request.metrics:
            registry[key] = known.get(
                key,
                MetricFamily(
                    name=key,
                    unit=unit_of(key),
                    side="request",
                    value_kind="distribution",
                    source="client",
                ),
            )

        # resource-side: each probe collapses its (steady) series → typed summaries
        # (gauge/counter), keyed <probe>.<metric>; FAMILY descriptors come from
        # describe() (deduped by family name — service is a label on the series, not it).
        series_out: dict[str, Series] = {}
        errors = probe_errors or {}
        for probe in exp.probes:
            registry.update({d.name: d for d in probe.describe()})  # by family (dedup)
            up_samples = store.get((probe.name, "up"), [])
            # group this probe's sampled series by extra-label-set: a common probe's bare
            # keys all land in the one () group; a fan-out probe's labeled keys
            # (cpu_m{pod="…"}) land in one group per pod. Each group then summarizes
            # exactly like a single probe did before — pod is just another label.
            groups: dict[tuple[tuple[str, str], ...], dict[str, list[Sample]]] = {}
            for (pname, key), samples in store.items():
                if pname != probe.name:
                    continue
                bare, extra = split_series(key)
                groups.setdefault(tuple(sorted(extra.items())), {})[bare] = samples
            for extra in sorted(groups):
                by_bare = groups[extra]
                labels = {**probe.labels, **dict(extra)}  # base {service} + e.g. {pod}
                for metric, spec in probe.families.items():
                    full = by_bare.get(metric, [])
                    sid = series_id(f"{probe.family}.{metric}", labels)
                    if full:
                        series_out[sid] = Series(metric, spec.unit, list(full))
                for window in windows:
                    window_health = [
                        sample for sample in up_samples if window.start_s <= sample.t < window.end_s
                    ]
                    # Cooldown gates the final recovered state. A failed scrape, or
                    # a labeled series that vanished before the last healthy tick,
                    # cannot prove recovery; omit that summary so MetricStore returns
                    # Missing instead of reusing a stale pre-scale-down value.
                    if window.kind == "cooldown" and (
                        not window_health or any(sample.value <= 0 for sample in window_health)
                    ):
                        continue
                    own: dict[str, list[Sample]] = {
                        metric: [
                            sample
                            for sample in by_bare.get(metric, [])
                            if window.start_s <= sample.t < window.end_s
                        ]
                        for metric in probe.families
                    }
                    for bare, summary in probe.summarize(own).items():
                        samples = own.get(bare)
                        if (
                            window.kind == "cooldown"
                            and samples is not None
                            and (not samples or samples[-1].t < window_health[-1].t)
                        ):
                            continue
                        if probe.name in errors:
                            # The probe missed ticks — its series have holes; the value
                            # stands but a flat-looking trend may be a sampling artifact.
                            summary = replace(summary, caveats=summary.caveats | {"probe_error"})
                        window.probe_metrics[series_id(f"{probe.family}.{bare}", labels)] = summary
            # the synthesized health series (`up`, see _observe): full series for §4's
            # outage view, gauge summary (mean = availability ratio, SLO-addressable as
            # `<family>.up{service=…}.mean`). No probe_error caveat — up IS that signal.
            if up_samples:
                up_fam = f"{probe.family}.up"
                sid = series_id(up_fam, probe.labels)
                series_out[sid] = Series("up", "", list(up_samples))
                for window in windows:
                    measured_up = [
                        sample for sample in up_samples if window.start_s <= sample.t < window.end_s
                    ]
                    for _, summary in probe.summarize({"up": measured_up}).items():
                        window.probe_metrics[sid] = summary
                registry[up_fam] = MetricFamily(
                    name=up_fam,
                    unit="",
                    side="resource",
                    value_kind="gauge",
                    source=probe.source,
                    description="probe health (1 ok / 0 failed) — Prometheus `up` 的对应物；"
                    "mean 即观测可用率",
                    labels=frozenset(probe.labels),
                )

        result.windows = windows
        result.series = series_out
        result.metrics = registry
        result.probe_errors = dict(errors)
        return result
