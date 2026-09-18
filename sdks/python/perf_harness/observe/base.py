"""Probe — the observation extension point (the other plug-in axis).

A Probe samples one Source on a fixed interval and contributes one or more named
Metrics; each Metric becomes a time-series within the ArmRun, and the Probe also
says how to collapse its own series into the summary row (``summarize``).

"Source" is just *which handle on the ProbeContext a Probe reads*: the http
client (scrape any /metrics), the client-side ClientStats (load generator's own
view), or the Service's K8s coordinates (see ``k8s.py``). Decoupling the
Source from the Service is what lets one run mix client-side + server-side +
downstream probes and so attribute a slowdown rather than guess.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import httpx
from harness_common.client import ClientManager
from harness_toolbox.data_loader import DataLoader

from perf_harness.metric import (
    MetricFamily,
    MetricSummary,
    MetricValueKind,
)
from perf_harness.metric.reduce import time_series_summary
from perf_harness.model import ProbeErrors, ProbeWindowObservation, Sample, Service, Window


class ClientStats:
    """Load-generator-side counters, updated by the Engine around each fire."""

    def __init__(self) -> None:
        self.inflight = 0
        self.sent = 0
        self.dropped = 0

    def start(self) -> None:
        self.inflight += 1
        self.sent += 1

    def done(self) -> None:
        self.inflight -= 1


@dataclass
class ProbeContext:
    """The handles a Probe may read from — each field is a candidate Source.

    ``client`` is the *load* client (also handed to ``Runner.fire``).
    ``observer_client`` is a separate client with its own small pool that
    custom HTTP-source probes use to avoid queuing behind load traffic. Falls
    back to ``client`` when unset. DataSource clients (including Prometheus)
    have their own pools, managed by ``clients`` for this ArmRun.
    """

    service: Service
    client: httpx.AsyncClient
    t0: float
    stats: ClientStats = field(default_factory=ClientStats)
    observer_client: httpx.AsyncClient | None = None
    # Owned by the ArmRun through final window queries; direct callers must dispose.
    clients: ClientManager = field(default_factory=ClientManager)
    reads: DataLoader | None = None
    # Unix time at the start of this tick, before other probes consume its budget.
    observation_budget_s: float | None = None
    sample_time_s: float | None = None
    wall_origin_s: float = field(default_factory=lambda: time.time() - time.monotonic())

    @property
    def probe_client(self) -> httpx.AsyncClient:
        """The client HTTP-source probes should read (isolated from load)."""
        return self.observer_client or self.client


@dataclass(frozen=True)
class FamilySpec:
    """One bare metric a Probe emits — unit + value_kind + human meaning, declared
    ONCE in the probe's ``families`` table (the mdatagen idea: metric metadata is a
    single declarative table that recording, registry and docs all read)."""

    unit: str
    value_kind: MetricValueKind = "gauge"
    description: str = ""
    labels: tuple[str, ...] = ()


class Probe(ABC):
    """Samples a Source over time and collapses its series for the summary row.

    A Probe is the ``resource`` side of perf's one metric model: it speaks the
    same unified ``Metric`` vocabulary the request side does (see ``describe``), so
    a cpu gauge and a per-request ttft are both "metrics", differing only in side.
    """

    #: stable, UNIQUE store id (one per observed target, e.g. "top.chat"); the
    #: metric *family* is the class-level name ("top") and ``service`` is a label.
    name: str = "probe"
    #: where this probe reads — groups its metrics for bottleneck attribution
    source: str = ""
    #: which service this probe observes (a downstream entry / the Service); the
    #: family stays un-prefixed and ``service`` becomes a label on every metric.
    _service: str | None = None

    @property
    def family(self) -> str:
        """The metric family (un-prefixed) — the class-level ``name``. Service-bound
        instances suffix ``self.name`` (for a unique store id) but keep this family."""
        return type(self).name

    @property
    def labels(self) -> dict[str, str]:
        """Series labels — ``{service: …}`` when bound to a service, else none."""
        return {"service": self._service} if self._service else {}

    #: THE declaration table: bare metric name → its FamilySpec (unit + value_kind +
    #: description). One source of truth for ``describe`` (the registry descriptor),
    #: ``summarize`` (which reducers to emit) and the Engine (series units) — a
    #: single table, so the vocabularies can't drift apart.
    families: dict[str, FamilySpec] = {}
    needs_observation_window: bool = False

    def describe(self) -> list[MetricFamily]:
        """This probe's contributions as ``resource``-side metric FAMILIES (no labels).

        The seam that makes a Probe part of the one metric model: its series are
        ``resource`` metrics, addressed ``<name>{labels}.<stat>`` in the report/SLO
        exactly like the request-side metrics a Runner records on the Outcome. The
        family is label-free (``top.cpu_m``); ``service`` is a label on the concrete
        series (built in the Engine from ``self.labels``), so two service-bound probes
        of the same family dedup to one family entry instead of duplicating metadata.
        """
        return [
            MetricFamily(
                name=f"{self.family}.{m}",  # family (top.cpu_m); service is a series label
                unit=spec.unit,
                side="resource",
                value_kind=spec.value_kind,
                source=self.source,
                description=spec.description,
                labels=frozenset((*self.labels, *spec.labels)),
            )
            for m, spec in self.families.items()
        ]

    @abstractmethod
    async def sample(self, ctx: ProbeContext) -> dict[str, float]:
        """One instantaneous reading. Omit a key when it is momentarily unavailable.

        A key is normally the bare metric name (one series at the probe's base
        ``labels``). A probe observing multiple instances may key a value with extra
        labels in ``series_id`` form (``cpu_m{pod="…"}``) — one series per instance,
        merged onto the base labels. Same metric model either way: a label is part of
        the series identity, and the Engine/report group by it like any other label."""

    async def finish(
        self, ctx: ProbeContext, windows: list[Window]
    ) -> list[ProbeWindowObservation]:
        """Read final window facts before clients close; default probes need no final query."""
        return []

    def summarize(self, series: dict[str, list[Sample]]) -> dict[str, MetricSummary]:
        """Collapse this probe's (steady-state) series → one typed MetricSummary per
        (bare) metric, keyed by bare name (the Engine prefixes ``<probe>.``).

        ``value_kind`` (from ``families``) picks the reducer: gauge → mean/peak/last,
        counter → total/rate/increase (rate = Δ/Δt over the steady window). One
        impl covers every built-in probe; override only for an exotic reduction.
        """
        out: dict[str, MetricSummary] = {}
        for metric, samples in series.items():
            spec = self.families.get(metric)
            summary = time_series_summary(samples, spec.value_kind if spec is not None else "gauge")
            if summary is not None:
                out[metric] = summary
        return out


class ClientProbe(Probe):
    """Client-side Source: in-flight depth and total sent, no external call."""

    name = "client"
    source = "client"
    families = {
        "inflight": FamilySpec("count", "gauge", "requests in flight from the load generator"),
        "sent": FamilySpec("count", "counter", "total requests the load generator has sent"),
    }

    async def sample(self, ctx: ProbeContext) -> dict[str, float]:
        return {"inflight": float(ctx.stats.inflight), "sent": float(ctx.stats.sent)}


# Probe sample store: (probe.name, sample key) → time series. The sample key is what
# ``Probe.sample`` returned it under — a bare metric (``cpu_m``) or, for a fan-out
# probe, a labeled ``series_id`` (``cpu_m{pod="…"}``). Tupling with the unique
# probe.name (instead of concatenating) keeps "top" / "top.chat" keys collision-free.
ProbeStore = dict[tuple[str, str], list[Sample]]


async def observe_loop(
    probes: list[Probe],
    ctx: ProbeContext,
    store: ProbeStore,
    stop: asyncio.Event,
    interval: float,
    ready: asyncio.Event | None = None,
    start: asyncio.Event | None = None,
) -> dict[str, ProbeErrors]:
    """Sample every probe each ``interval`` until stopped. A failing probe never stops
    observation, but the failure is RECORDED, not swallowed — returns the per-probe
    error census (failures / total ticks / last error) so ``_aggregate`` can flag the
    affected summaries and the arm_run. A broken /metrics must not render as calm data."""
    failures: dict[str, list[str]] = {}
    ticks = 0
    while True:
        ctx.sample_time_s = time.time()
        t = time.monotonic() - ctx.t0
        ticks += 1
        async with DataLoader() as reads:
            ctx.reads = reads
            try:
                for probe in probes:
                    try:
                        reading = await probe.sample(ctx)
                    except Exception as exc:  # noqa: BLE001 — one bad probe must not stop observation
                        failures.setdefault(probe.name, []).append(repr(exc))
                        reading = None
                    # synthesize the probe's health as a SERIES (the Prometheus `up` analogue):
                    # the arm_run census says THAT observation broke, this says WHEN — §4 can
                    # chart the outage window instead of a fake-calm gap. 1 ok / 0 failed.
                    store.setdefault((probe.name, "up"), []).append(
                        Sample(t, 0.0 if reading is None else 1.0)
                    )
                    for key, val in (reading or {}).items():
                        store.setdefault((probe.name, key), []).append(Sample(t, val))
            finally:
                ctx.reads = None
                ctx.sample_time_s = None
        if ready is not None:
            ready.set()
        if start is not None:
            await start.wait()
            start = None
            continue
        if stop.is_set():
            break
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)
    return {
        name: ProbeErrors(failures=len(errs), ticks=ticks, last=errs[-1])
        for name, errs in failures.items()
    }
