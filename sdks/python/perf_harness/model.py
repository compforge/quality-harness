"""perf_harness value objects — the ubiquitous language, as plain data.

The whole harness is four lines::

    一个 Experiment 比较一组 Arm;
    一个 Arm 是命名的 ResourceProfile(资源档) + LoadProfile(负载档);
    一个 Trial 是 Arm 的真实执行, 由 Workload 发 Case、Probe 周期采样并按 Window 归约;
    report / SLO / analyze 都是对这张表的查询。

This module holds the nouns that are *just data* (no behaviour): the
time-series primitives (`Sample`/`Series`), one request's `Outcome` + its
`Verdict`, the domain `Environment` / `Service`, the `ResourceProfile` (资源档),
and the per-Trial/-Run aggregates (`RequestStats`/`TrialRecord`/`Run`). The load
*shape* vocabulary (`LoadProfile`/`Schedule`/`Pacing`) lives in `load.py`;
behaviour (firing load, sampling probes, provisioning) lives in sibling modules.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from harness_common import (
    Component,
    Execution,
    ExperimentRun,
    Forge,
    KubernetesEnvironment,
    Repository,
)
from harness_common import Deployment as BaseDeployment
from harness_common import Outcome as BaseOutcome
from harness_common import Service as BaseService

from perf_harness.metric import Caveat, MetricFamily, MetricSummary

if TYPE_CHECKING:
    # annotation-only: importing drive at runtime would cycle (drive.workload
    # constructs this module's Outcome/Verdict)
    from perf_harness.drive.load import LoadProfile


def make_run_id() -> str:
    """A sortable run id: ``YYYYMMDD-HHMMSS`` (local time). One run = one config run."""
    return time.strftime("%Y%m%d-%H%M%S")


@dataclass(frozen=True)
class Sample:
    """One reading of one Metric at time ``t`` (monotonic seconds into the Trial)."""

    t: float
    value: float


@dataclass
class Series:
    """A named Metric's readings over a Trial — one time-varying signal."""

    metric: str
    unit: str
    samples: list[Sample] = field(default_factory=list)


@dataclass
class Outcome(BaseOutcome):
    """Client-side result of one Workload.fire() — the request-side truth.

    Two-stage contract: ``fire`` records the *raw observation* (status, timing,
    SSE frame/byte counts, and protocol-specific signals in ``meta``); the
    *verdict* (``ok`` / ``error_kind``) is then decided by ``Workload.judge`` —
    NOT by ``fire`` — and written back by the Engine. So ``fire`` may leave
    ``ok``/``error_kind`` at their defaults; the authoritative values come from
    judge. Keeping judge a pure function of this raw Outcome means verdicts can
    be recomputed offline from stored Outcomes without re-firing.

    Latency percentiles, throughput and the error taxonomy are aggregated from
    these (not from a server histogram), so they are always available even when
    the Service exposes no /metrics.
    """

    status: int | None
    duration_ms: float
    ok: bool = False  # verdict — set by Workload.judge() via the Engine, not by fire()
    error_kind: str | None = None  # verdict bucket — set by judge(): "ReadTimeout" / "503" / …
    events: int = 0  # SSE frames consumed (0 for non-SSE)
    nbytes: int = 0
    metrics: dict[str, float] = field(
        default_factory=dict
    )  # per_request metric values fire() measured: ttft_ms / first_<event>_ms … (→ MetricStat)
    dropped: bool = False  # never-sent (open-loop max_inflight shed); NOT a latency sample
    meta: dict = field(
        default_factory=dict
    )  # raw signals fire() records for judge(): exc / saw_done / error_frames / ttft_ms …
    facets: dict[str, str] = field(
        default_factory=dict
    )  # fired Case's dims; report pivots by these
    case_id: str = ""  # canonical Case.id stamped by the scheduler for cross-run joins


@dataclass(frozen=True)
class Verdict:
    """The judgement on one Outcome — ``Workload.judge``'s return.

    ``error_kind`` is the report's error bucket; ``None`` iff ``ok``.
    """

    ok: bool
    error_kind: str | None = None


# The unit of load `Case` now lives in `common.case` — the canonical, harness-neutral case
# reused by e2e / eval / perf (perf reads it directly; see config.py / engine.py). Its
# per-experiment load weight is NOT a Case field: weight is "how this run uses the case",
# not the case's identity — config entries carry an inline `weight:` that the loader
# lifts into the `Experiment.mix` Overlay (keyed by case.id).


# ---------------------------------------------------------------------------
# Environment / Service runtime view + ResourceProfile (资源档)
# ---------------------------------------------------------------------------


Environment = KubernetesEnvironment


@dataclass(frozen=True, slots=True)
class Service(BaseService):
    """Perf view of a Service, including reachability and observation coordinates."""

    name: str = ""
    component: Component = field(
        default_factory=lambda: Component(
            repository=Repository(forge=Forge(name=""), path=""),
            name="",
        )
    )
    environment: Environment = field(default_factory=lambda: Environment(name=""))
    base_url: str = ""
    headers: dict[str, str] = field(default_factory=dict, repr=False)
    namespace: str = ""
    k8s_selector: str = ""
    metrics_prefix: str = ""
    metrics_port: int = 8000
    container: str | None = None


@dataclass(frozen=True)
class ResourceProfile:
    """资源档: the resource budget the Service runs under for one Trial.

    Substrate-agnostic data — it does not care whether a HelmDeployer drove it,
    a DockerDeployer did, or a human set it and the harness merely *records* it
    (no deployer). ``extra`` carries arbitrary ``helm --set`` style overrides.
    """

    cpu: str | None = None  # "2" / "500m"
    memory: str | None = None  # "2Gi"
    workers: int | None = None  # uvicorn workers
    replicas: int = 1
    extra: dict[str, str] = field(default_factory=dict)

    def label(self) -> str:
        """Short key for report rows, e.g. ``w2/2Gi``."""
        parts = []
        if self.workers is not None:
            parts.append(f"w{self.workers}")
        if self.memory:
            parts.append(self.memory)
        if self.cpu:
            parts.append(f"cpu{self.cpu}")
        return "/".join(parts) or "default"


@dataclass(frozen=True, slots=True)
class Deployment(BaseDeployment):
    """A perf Deployment that applies one ResourceProfile to the Service."""

    resources: ResourceProfile


@dataclass(frozen=True)
class Arm:
    """One named configuration participating in an Experiment comparison."""

    id: str
    resources: ResourceProfile
    load: LoadProfile


# ---------------------------------------------------------------------------
# Per-Trial aggregate — one row of the summary table
# ---------------------------------------------------------------------------


@dataclass
class RequestStats:
    """Request-side aggregate over one Window, or one facet slice within it.

    ``n`` / latency / throughput / error_rate cover only *sent* requests. Open-loop
    ``client_saturated`` drops (never-sent shed load) are NOT latency samples and
    are excluded from those — they live in ``n_dropped`` instead, with
    ``drop_rate`` = dropped / (sent + dropped). A non-trivial ``drop_rate`` means
    the generator measured below its intended offered load, so the latency
    percentiles understate reality (coordinated omission) and the Trial's latency
    is not trustworthy — the report flags it.
    """

    n: int
    n_ok: int
    throughput_rps: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    error_rate: float
    error_breakdown: dict[str, int]
    n_dropped: int = 0
    mean_ms: float = 0.0  # mean request latency (feeds the request.duration_ms distribution)
    metrics: dict[str, MetricSummary] = field(
        default_factory=dict
    )  # per_request metric distributions for this slice (ttft_ms / first_<event>_ms …)
    caveats: frozenset[Caveat] = field(default_factory=frozenset)
    """Slice-level trustworthiness, minted at REDUCE — ``co_biased`` (closed-loop
    tail), ``high_drop`` (open-loop saturation), ``few_samples``. Stamped onto this
    slice's ``request.duration_ms`` distribution so a CO-biased p99 can't be read as
    clean; the report renders them as flags instead of prose."""

    @property
    def drop_rate(self) -> float:
        total = self.n + self.n_dropped
        return self.n_dropped / total if total else 0.0


# ---------------------------------------------------------------------------
# Stop model — how a trial ended (every trial ends with one, "deadline" is normal)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StopSnapshot:
    """The watcher's view at the instant a breaker tripped — NOT the post-warmup
    measurement Window. Captured from the breaker's own (cumulative, incl-warmup) view so
    the report can answer 'why did it stop' from the trip itself, not by reverse-
    engineering the measurement aggregate. ``None`` on a clean ``deadline`` stop."""

    at_s: float  # seconds into the trial when it tripped
    sent: int  # requests sent at trip (the breaker's denominator)
    errors: int  # judged failures at trip (the breaker's numerator)
    error_rate: float  # errors / sent at trip
    threshold: float  # the configured abort_on_error_rate it crossed


@dataclass(frozen=True)
class TrialStop:
    """How one trial ended. EVERY trial has one — ``reason="deadline"`` is the normal
    end (planned ``steady_s`` reached), other reasons mean it stopped early. The
    enact census records what was in flight when the load wound down: a cancelled
    in-flight request is ``interrupted`` (NOT a latency sample / error — see
    ``RequestStats``), counted here only.

    Reasons today: ``deadline`` (normal) · ``error_rate`` (circuit breaker). External
    / resource / per-stage stops will add reasons without changing the shape."""

    reason: str = "deadline"
    snapshot: StopSnapshot | None = None  # the trip view; None for a deadline stop
    inflight_at_stop: int = 0  # requests in flight when the wind-down began
    interrupted: int = 0  # in-flight requests force-cancelled (drain window exceeded)
    force_cancelled: bool = False  # True iff in-flight REQUESTS had to be cut (interrupted > 0)

    @property
    def early(self) -> bool:
        """The trial stopped before its planned window (anything but a deadline end)."""
        return self.reason != "deadline"


@dataclass(frozen=True)
class ProbeErrors:
    """One probe's observation-failure census for a trial — observability health is
    DATA (a probe that silently fails paints a fake-flat trend). ``failures`` of
    ``ticks`` sampling rounds raised; ``last`` is the most recent error (repr).
    Observational only: it flags summaries (``probe_error`` caveat) and the report,
    never the run verdict."""

    failures: int
    ticks: int
    last: str


WindowKind = Literal["measurement", "ramp", "hold", "cooldown"]
Phase = Literal["setup", "measurement", "deactivate", "cooldown", "cleanup"]


@dataclass(frozen=True)
class PhaseError:
    """An ordinary exception raised while executing a Trial lifecycle phase.

    This is harness execution evidence, not a request ``Outcome``, Probe health,
    or an SLO result. Keeping it on the Trial lets a failed setup/cleanup still
    produce a complete run artifact without inventing request facts.
    """

    phase: Phase
    error_type: str
    message: str


@dataclass
class Window:
    """An observed time boundary within a Trial.

    Stage is the load plan; Window is the actual interval used to reduce request
    and resource facts. Its interval is half-open: ``[start_s, end_s)``. ``id`` is
    unique within a Trial even when display names
    repeat (for example a spike's two ``hold@base`` legs).
    """

    id: str
    name: str
    kind: WindowKind
    start_s: float
    end_s: float
    complete: bool
    target_level: float | None = None
    request: RequestStats | None = None
    by_case: dict[str, RequestStats] = field(default_factory=dict)
    by_facet: dict[str, dict[str, RequestStats]] = field(default_factory=dict)
    probe_metrics: dict[str, MetricSummary] = field(default_factory=dict)

    @property
    def duration_s(self) -> float:
        return max(self.end_s - self.start_s, 0.0)


@dataclass
class TrialRecord(Execution[Outcome]):
    """The recorded execution of one Arm, reduced into addressable Windows."""

    service: str
    arm: Arm
    windows: list[Window]
    series: dict[str, Series]
    stop: TrialStop = field(default_factory=TrialStop)
    """How this trial ended (every trial has one; default ``reason="deadline"`` =
    normal). When it stopped early (e.g. the error-rate breaker), the trial's numbers
    are *partial* — throughput especially understates (denominator is the planned
    window) — so the report flags it from ``stop.reason``/``stop.snapshot`` and reads
    'it broke at this load', not a clean capacity point."""
    slo: list[SloCheck] = field(default_factory=list)  # per-run SLO gate, evaluated on this trial
    metrics: dict[str, MetricFamily] = field(default_factory=dict)
    """Unified metric registry: every report-visible metric FAMILY name → its
    family descriptor (no labels — metadata declared once), spanning all kinds:
    ``per_request`` (slice ``RequestStats.metrics``), ``time_sampled``
    (``probe_metrics``) and ``derived`` (builtin ``request.*``). report/SLO read
    value_kind/source/unit from here and address any series as ``<name>{labels}.<stat>``
    regardless of which pipeline produced it. The MetricStore wraps these trials to
    serve those reads."""
    outcomes: list[tuple[float, Outcome]] = field(default_factory=list, repr=False)
    """Raw request-side facts: every ``(t, Outcome)`` the drivers recorded, incl.
    warmup and drops — the request analogue of ``series`` (the time_sampled raw).
    Summaries above are derived from these at reduce time; they are kept so the
    persistence layer (``runio``) can write the raw layer (``outcomes.jsonl``) and
    offline analysis can re-slice / recompute without re-firing."""
    probe_errors: dict[str, ProbeErrors] = field(default_factory=dict)
    """Probes that FAILED at least one sampling tick this trial (key = unique probe
    name, e.g. ``metrics.chat``). Their summaries carry the ``probe_error`` caveat,
    absent reads resolve to ``Missing("probe_error")`` (≠ "slice没数据"), and the
    validity lens flags them — so a broken /metrics never renders as a calm line."""
    phase_errors: list[PhaseError] = field(default_factory=list)
    """Exceptions from Trial lifecycle hooks or orchestration, in occurrence order.

    A non-empty list makes the Run an execution error. It remains separate from
    request outcomes, Probe observation failures, and SLO evaluation.
    """

    def label(self) -> str:
        """Stable Trial id within a Run; equal to the Arm alignment key."""
        return self.arm.id

    @property
    def measurement(self) -> Window:
        return next(window for window in self.windows if window.kind == "measurement")


# ---------------------------------------------------------------------------
# SLO — the per-run gate (aggregate verdict, distinct from per-request judge)
# ---------------------------------------------------------------------------

SloOp = Literal["lt", "lte", "gt", "gte", "between"]


@dataclass(frozen=True)
class WindowSelector:
    """Select Trial Windows by observed semantics, not by metric labels."""

    kind: WindowKind = "measurement"
    name: str | None = None
    level: float | None = None

    def matches(self, window: Window) -> bool:
        return (
            window.kind == self.kind
            and (self.name is None or window.name == self.name)
            and (self.level is None or window.target_level == self.level)
        )


@dataclass(frozen=True)
class SloAssertion:
    """One declarative SLO: resolve ``metric`` on a trial and compare via ``op`` to
    ``threshold``. Metric labels select entities (facet/service); ``window`` selects
    time. One selector may match multiple Windows, yielding one check per Window.
    """

    metric: str
    op: SloOp
    threshold: float | tuple[float, float]
    window: WindowSelector = field(default_factory=WindowSelector)


SloState = Literal["pass", "fail", "skipped"]


@dataclass(frozen=True)
class SloCheck:
    """Result of one SloAssertion on one trial — a THREE-state verdict.

    ``skipped`` (``observed is None``) means the metric's slice had no value on this
    trial: a declared facet value no request carried, or a probe that returned no
    data. The check could NOT be evaluated — and a skip is **not** a pass. It never
    lets a trial count as confirmed capacity, and under ``strict_slo`` it fails the
    run; by default it leaves the run's exit code alone but is surfaced as skipped,
    never silently read as green (Prometheus alerts may no-fire on empty; a CI gate
    must not)."""

    assertion: SloAssertion
    observed: float | None
    state: SloState
    window_id: str | None = None

    @property
    def passed(self) -> bool:
        return self.state == "pass"

    @property
    def skipped(self) -> bool:
        return self.state == "skipped"

    @property
    def failed(self) -> bool:
        return self.state == "fail"


@dataclass
class Run(ExperimentRun):
    """One execution of an Experiment — its identity plus the per-Trial results.

    ``Engine.run()`` returns this; ``write_run`` lays it out under
    ``runs/<experiment>/<run_id>/`` and serialises it to ``run.json``. ``trials``
    are the cells of the Constraint × Load sweep (the experiment's arms).
    ``passed`` is the operational run gate: every trial completed its planned
    window and all gated SLOs passed. The CLI maps it to the process exit code for
    CI.
    """

    service: str
    trials: list[TrialRecord]
    passed: bool = True
