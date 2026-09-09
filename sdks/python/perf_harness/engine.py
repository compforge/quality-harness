"""Engine — pure orchestration: resolve Arms and execute one Trial per Arm.

One Trial: deploy the ResourceProfile (via the Experiment's Deployer, if any) →
open a client → drive the load (``drive.scheduler``) while the observer samples
every Probe (``observe.observe_loop``) → collapse outcomes + series into a
TrialRecord (``_aggregate``, minting via ``metric.reduce``). The grid is swept
resources-outer so each profile is provisioned once and all load levels run
under it. Execution detail lives in the packages; this module only wires phases.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field, replace

import httpx
from harness_common import Deployer, OperationRun
from harness_common import Experiment as BaseExperiment
from harness_common.overlay import Overlay
from spec_case.model import Case

from perf_harness.drive.load import LoadProfile
from perf_harness.drive.scheduler import drive_closed, drive_open
from perf_harness.drive.workload import TrialContext, Workload
from perf_harness.metric import (
    MetricFamily,
    series_id,
    split_series,
)
from perf_harness.metric.reduce import request_stats, unit_of
from perf_harness.metric.store import PER_REQUEST_DESCRIPTORS, REQUEST_DESCRIPTORS
from perf_harness.model import (
    Arm,
    Deployment,
    Outcome,
    Phase,
    PhaseError,
    ProbeErrors,
    ResourceProfile,
    Run,
    Sample,
    Series,
    Service,
    SloAssertion,
    TrialRecord,
    TrialStop,
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
class _TrialExecution:
    """One Trial's lifecycle state, carried through every phase to finalization.

    ``TrialContext`` is the immutable workload-facing input. This private state
    machine preserves ordered diagnostics while the lifecycle continues to its
    mandatory cleanup and one final TrialRecord exit.
    """

    context: TrialContext
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
    workload mix), NOT by the case — the same case is reusable across experiments at
    different weights. Empty pool → one anonymous Case. ``facet_order`` gives ordered
    facets their report sort order.
    """

    service: Service
    workload: Workload
    resources: list[ResourceProfile]
    loads: list[LoadProfile]
    deployer: Deployer[Deployment] | None = None
    probes: list[Probe] = field(default_factory=list)
    cases: list[Case] = field(default_factory=list)
    # experiment workload mix: load weight per case.id (unwritten → 1.0; unknown id → error).
    # An Overlay, not a Case field — weight is "how this run uses the case", not its identity.
    mix: Overlay = field(default_factory=Overlay)
    facet_order: dict[str, list[str]] = field(default_factory=dict)
    slo: list[SloAssertion] = field(default_factory=list)  # per-run gate (aggregate verdict)
    abort_on_fail: bool = False  # stop the sweep at the first SLO-failing trial
    strict_slo: bool = False  # treat a skipped SLO (no data) as a run failure, not a pass
    name: str = "perf"  # experiment name → runs/<name>/<run_id>/
    observe_interval_s: float = 5.0
    cooldown_s: float = 0.0  # keep probes running after deactivation for scale-down curves
    teardown: bool = False

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
    """Runs one Experiment → a Run (run_id + one TrialRecord per Arm)."""

    def __init__(self, experiment: Experiment, *, run_id: str | None = None) -> None:
        self.experiment = experiment
        self.run_id = run_id or make_run_id()  # threaded to Workload.fire + the run dir
        # empty pool → one anonymous Case; weights come from the experiment mix (Overlay
        # keyed by case.id, default 1.0), not from the case — see Experiment.mix.
        self._cases = experiment.cases or [Case(id="default", input={})]
        _mix = experiment.mix.resolve((c.id for c in self._cases), default_of=lambda _: 1.0)
        self._weights = [max(_mix[c.id], 0.0) for c in self._cases]

    async def run(self) -> Run:
        exp = self.experiment
        started = time.strftime("%Y-%m-%dT%H:%M:%S")
        trials: list[TrialRecord] = []
        passed = True
        try:
            applied: ResourceProfile | None = None
            for arm in exp.resolved_arms():
                if exp.deployer is not None and arm.resources != applied:
                    await exp.deployer.deploy(
                        Deployment(service=exp.service, resources=arm.resources)
                    )
                    applied = arm.resources
                trial = await self._run_trial(exp.service, arm)
                # A breaker-ended trial only observed a partial load window. Even
                # if every evaluated SLO happens to pass, it cannot prove this
                # load level was sustained.
                failed = trial.stop.early or bool(trial.phase_errors)
                if exp.slo and not trial.phase_errors:
                    trial.slo = evaluate_slo(trial, exp.slo)
                    # the run gate is lenient on skip by default: a skipped check
                    # (slice absent) is surfaced but doesn't flip the exit code;
                    # strict_slo treats an unverifiable SLO as a failure.
                    failed = (
                        failed
                        or any(c.failed for c in trial.slo)
                        or any(
                            c.skipped and (exp.strict_slo or c.assertion.window.kind == "cooldown")
                            for c in trial.slo
                        )
                    )
                passed = passed and not failed
                trials.append(trial)
                # A phase error says the testbed/execution chain itself is no
                # longer trustworthy. Continuing the sweep can compound leaked
                # state, so it stops independently of the SLO-oriented policy.
                if trial.phase_errors or (exp.abort_on_fail and failed):
                    break
        finally:
            if exp.teardown and exp.deployer is not None:
                await exp.deployer.teardown()
        return Run(
            run_id=self.run_id,
            experiment=exp.name,
            created_at=started,
            executions=trials,
            service=exp.service.name,
            trials=trials,
            passed=passed,
        )

    async def _run_trial(self, service: Service, arm: Arm) -> TrialRecord:
        exp = self.experiment
        profile, load = arm.resources, arm.load
        stats = ClientStats()
        cap = max(64, int(load.schedule.peak_level) * 2)
        limits = httpx.Limits(max_connections=cap, max_keepalive_connections=cap)
        # observer gets its own small pool so /metrics scraping never queues behind
        # load traffic on a saturated pool (P1: don't lose evidence at saturation)
        obs_limits = httpx.Limits(max_connections=8, max_keepalive_connections=8)
        timed: list[tuple[float, Outcome]] = []
        operation_runs: list[OperationRun[Outcome]] = []
        store: ProbeStore = {}
        probe_errors: dict[str, ProbeErrors] = {}
        measurement_end_s = 0.0
        cooldown_start_s: float | None = None
        cooldown_end_s: float | None = None
        # trust_env=False: the load generator connects DIRECTLY to the Service's
        # base_url — never via an ambient HTTP(S)_PROXY/ALL_PROXY from the shell (a
        # stray proxy env would silently reroute or, if malformed, crash client
        # creation). A real proxy, if ever needed, should be explicit Service access config.
        async with (
            httpx.AsyncClient(limits=limits, http2=False, trust_env=False) as client,
            httpx.AsyncClient(limits=obs_limits, http2=False, trust_env=False) as obs_client,
        ):
            execution = _TrialExecution(
                context=TrialContext(
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
            trial_stop = TrialStop(reason="aborted")
            try:
                await exp.workload.setup(execution.context)
                execution.enter("measurement")
                ctx = ProbeContext(
                    service=service,
                    client=client,
                    t0=time.monotonic(),
                    stats=stats,
                    observer_client=obs_client,
                )
                observer = asyncio.create_task(
                    observe_loop(exp.probes, ctx, store, stop, exp.observe_interval_s)
                )
                drive = drive_open if load.model == "open" else drive_closed
                trial_stop = await drive(
                    exp.workload,
                    execution.context,
                    ctx,
                    self._cases,
                    self._weights,
                    timed,
                    operation_runs,
                    arm.id,
                )
                # Post-load samples remain in the raw series for cooldown/scale-down
                # charts, but summaries must describe only the load measurement window.
                measurement_end_s = (
                    trial_stop.snapshot.at_s if trial_stop.snapshot is not None else load.duration_s
                )
                execution.enter("deactivate")
                await exp.workload.deactivate(execution.context)
                if exp.cooldown_s:
                    execution.enter("cooldown")
                    cooldown_start_s = time.monotonic() - ctx.t0
                    await asyncio.sleep(exp.cooldown_s)
            except Exception as exc:
                execution.record(exc)
                if ctx is not None and measurement_end_s == 0.0:
                    measurement_end_s = time.monotonic() - ctx.t0
            except BaseException as exc:
                # Cancellation / process-level interrupts remain control flow rather
                # than persisted test results, but cleanup must still not hide them.
                execution.fatal_error = exc
            finally:
                if observer is not None:
                    stop.set()
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
                    await exp.workload.cleanup(execution.context)
                except BaseException as cleanup_error:
                    if execution.fatal_error is not None:
                        execution.fatal_error.add_note(f"cleanup also failed: {cleanup_error!r}")
                    elif isinstance(cleanup_error, Exception):
                        execution.record(cleanup_error, phase="cleanup")
                    else:
                        execution.fatal_error = cleanup_error
                if execution.fatal_error is not None:
                    raise execution.fatal_error

        trial = self._aggregate(
            arm,
            timed,
            store,
            probe_errors,
            measurement_end_s=measurement_end_s,
            cooldown_start_s=cooldown_start_s,
            cooldown_end_s=cooldown_end_s,
        )
        trial.stop = trial_stop  # how the trial ended (deadline / breaker) + enact census
        trial.operation_runs = operation_runs
        trial.outcomes = timed  # raw layer (incl. warmup/drops) — runio persists these
        trial.phase_errors = execution.phase_errors
        return trial

    def _aggregate(
        self,
        arm: Arm,
        timed: list[tuple[float, Outcome]],
        store: ProbeStore,
        probe_errors: dict[str, ProbeErrors] | None = None,
        *,
        measurement_end_s: float | None = None,
        cooldown_start_s: float | None = None,
        cooldown_end_s: float | None = None,
    ) -> TrialRecord:
        exp = self.experiment
        load = arm.load
        warmup = load.warmup_s
        closed = load.model == "closed"  # → the slice's latency carries the co_biased caveat
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
        for index, stage in enumerate(load.schedule.stages):
            stage_start, stage_end = clock, clock + stage.over_s
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
                        target_level=stage.to_level,
                    )
                )
            clock = stage_end
        if cooldown_start_s is not None and cooldown_end_s is not None:
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

        for window in windows:
            if window.kind == "cooldown":
                continue
            selected = [o for t, o in timed if window.start_s <= t < window.end_s]
            window.request = request_stats(selected, max(window.duration_s, 1e-9), closed=closed)
            case_groups: dict[str, list[Outcome]] = defaultdict(list)
            for outcome in selected:
                if outcome.case_id:
                    case_groups[outcome.case_id].append(outcome)
            window.by_case = {
                case_id: request_stats(group, max(window.duration_s, 1e-9), closed=closed)
                for case_id, group in case_groups.items()
            }
            facet_keys = {key for outcome in selected for key in outcome.facets}
            for key in facet_keys:
                groups: dict[str, list[Outcome]] = defaultdict(list)
                for outcome in selected:
                    if key in outcome.facets:
                        groups[outcome.facets[key]].append(outcome)
                window.by_facet[key] = {
                    value: request_stats(group, max(window.duration_s, 1e-9), closed=closed)
                    for value, group in groups.items()
                }

        measurement = windows[0]
        assert measurement.request is not None

        # unified metric registry: every report/SLO-visible metric → its descriptor.
        # (1) builtin request.* (duration_ms / error_rate / throughput_rps /
        # drop_rate). (2) per-request distributions: a Workload may declare
        # unit/source (describe()); undeclared keys (e.g. dynamic first_<event>_ms)
        # are inferred — always a distribution, source=client.
        registry: dict[str, MetricFamily] = {d.name: d for d in REQUEST_DESCRIPTORS}
        # per-request descriptor precedence: Workload-declared > framework (ttft_ms) > inferred
        known = {d.name: d for d in PER_REQUEST_DESCRIPTORS}
        known.update({m.name: m for m in exp.workload.describe()})
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

        return TrialRecord(
            id=arm.id,
            service=exp.service.name,
            arm=arm,
            windows=windows,
            series=series_out,
            metrics=registry,
            probe_errors=dict(errors),
        )
