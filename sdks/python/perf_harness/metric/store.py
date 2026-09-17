"""MetricStore — the one addressable read face over a run's arm_runs.

A run is a response surface: ``metric = f(Arm, Window, slice)``. The store IS
that surface as an index — every report/SLO-visible number is addressable
``<family>{labels}.<stat>`` and resolved here, regardless of which side
(``request`` / ``resource``) it lives on or how it was produced.

The store is the **addressing layer over the slice storage** (the perf analogue of
TSDB vs PromQL), so the boundary is:
  - SLO + capacity read ONLY through the store (``query`` / ``rows``) — one source for
    every gated number, so a gate can't diverge from the report on how it's computed.
  - the report renders ``RequestStats`` request-slices *directly* for its hot stat
    columns (n / p50 / p99 …) — that's the slice's reduced storage, not a different
    computation — and uses the store (``pivot``) for label-routed resource reads (the
    per-service pivot). It does not round-trip the hot columns through ``query`` (pure
    indirection, no divergence to prevent).

``query`` returns ``Read`` = ``float | Missing`` — absence is a value, not a bare
``None``, so an SLO maps it to *skipped* (never silently to pass). Entity dimensions
such as service and facet remain metric labels; time is selected separately with a
``Window`` rather than hidden in a label.

The builtin descriptors + the request-slice projection live here too (they ARE the
metric layer); ``slo.py`` and ``config.py`` import them from here.
"""

from __future__ import annotations

from perf_harness.metric import (
    DistributionSummary,
    MetricFamily,
    MetricSummary,
    Missing,
    Read,
    ScalarSummary,
    parse_ref,
    resolve,
)
from perf_harness.model import ArmRun, RequestStats, Window

# builtin request-side metric families (aggregated from judged Outcomes) — always
# present, registered so an SLO may gate them and the report can describe them.
REQUEST_DESCRIPTORS: list[MetricFamily] = [
    MetricFamily(
        "request.duration_ms",
        "ms",
        "request",
        "distribution",
        "client",
        description="end-to-end request latency, client-observed",
    ),
    MetricFamily(
        "request.error_rate",
        "ratio",
        "request",
        "scalar",
        "client",
        description="judged failures ÷ completed dispatch-cohort requests (Judge)",
    ),
    MetricFamily(
        "request.throughput_rps",
        "rps",
        "request",
        "scalar",
        "client",
        description="completed requests ÷ steady window",
    ),
    MetricFamily(
        "request.drop_rate",
        "ratio",
        "request",
        "scalar",
        "client",
        description="finite-rate drops ÷ offered (not latency samples)",
    ),
]

EVENT_FIELDS = (
    "arrived",
    "dispatched",
    "completed",
    "succeeded",
    "n_interrupted",
    "inflight_peak",
    "inflight_end",
    "arrival_rps",
    "dispatch_rps",
    "success_rps",
)
REQUEST_DESCRIPTORS.extend(
    MetricFamily(
        f"request.{key}", "rps" if key.endswith("rps") else "count", "request", "scalar", "client"
    )
    for key in EVENT_FIELDS
)

# framework-recorded per-request metrics (stream_sse records first_byte_ms) — declared
# so an SLO may gate them. A consumer's OWN per-request metrics (first_<event>_ms …) are
# undeclared by default → they reach the report/CSV but cannot gate unless the Runner
# declares them via describe() (an SLO must fail-fast, not silently skip a typo).
PER_REQUEST_DESCRIPTORS: list[MetricFamily] = [
    MetricFamily(
        "first_byte_ms",
        "ms",
        "request",
        "distribution",
        "client",
        description="time to first response byte (not model TTFT)",
    ),
]

# undotted builtin RequestStats fields usable directly as SLO metrics (aliases)
SLO_METRICS = frozenset(
    {
        "n",
        "n_ok",
        "n_dropped",
        "throughput_rps",
        "p50_ms",
        "p95_ms",
        "p99_ms",
        "error_rate",
        "drop_rate",
    }
)


def slice_summaries(sl: RequestStats) -> dict[str, MetricSummary]:
    """A request slice's unified metric summaries: its per_request distributions plus
    the builtin derived ``request.*`` (delegating to the slice's typed fields). The
    slice's caveats ride on its ``request.duration_ms`` distribution, so a CO-biased /
    saturated latency is never read as clean."""
    out: dict[str, MetricSummary] = dict(sl.metrics)
    if sl.n:
        out["request.duration_ms"] = DistributionSummary(
            n=sl.n, mean=sl.mean_ms, p50=sl.p50_ms, p95=sl.p95_ms, p99=sl.p99_ms, caveats=sl.caveats
        )
        out["request.error_rate"] = ScalarSummary(sl.error_rate)
    out["request.throughput_rps"] = ScalarSummary(sl.throughput_rps)
    out["request.drop_rate"] = ScalarSummary(sl.drop_rate)
    out.update({f"request.{key}": ScalarSummary(getattr(sl, key)) for key in EVENT_FIELDS})
    return out


class MetricStore:
    """Addressable read face over ArmRun Windows (see module docstring)."""

    def __init__(self, arm_runs: list[ArmRun]) -> None:
        self._arm_runs = arm_runs
        self._families: dict[str, MetricFamily] = {}
        for arm_run in arm_runs:
            self._families.update(arm_run.metrics)

    def rows(self) -> list[ArmRun]:
        return self._arm_runs

    def families(self) -> dict[str, MetricFamily]:
        return self._families

    def family(self, name: str) -> MetricFamily | None:
        return self._families.get(name)

    def query(self, arm_run: ArmRun, ref: str, window: Window | None = None) -> Read:
        """Resolve a metric ref inside one Window; measurement is the default."""
        window = window or arm_run.measurement
        name, labels, stat = parse_ref(ref)
        family = arm_run.metrics.get(name) or self._families.get(name)
        if family is not None and family.side == "resource":
            if stat is None:
                return Missing("no_data")
            value = resolve(window.probe_metrics, ref)
            if value is not None:
                return value
            return self._resource_missing(arm_run, name, labels)
        request = self._request_slice(window, labels)
        if request is None:
            return Missing("no_slice")
        if request.n == 0 and name in {"p50_ms", "p95_ms", "p99_ms", "mean_ms", "error_rate"}:
            return Missing("no_data")
        if stat is None:
            value = getattr(request, name, None)
            return float(value) if value is not None else Missing("no_data")
        summaries = slice_summaries(request)
        if not labels:
            summaries = {**window.probe_metrics, **summaries}
        value = resolve(summaries, f"{name}.{stat}")
        return value if value is not None else Missing("no_data")

    def summary(
        self, arm_run: ArmRun, sid: str, window: Window | None = None
    ) -> MetricSummary | None:
        window = window or arm_run.measurement
        name, labels, _ = parse_ref(sid)
        if "service" in labels:
            return window.probe_metrics.get(sid)
        request = self._request_slice(window, labels)
        return slice_summaries(request).get(name) if request is not None else None

    def pivot(
        self, arm_run: ArmRun, family: str, by: str, window: Window | None = None
    ) -> dict[str, MetricSummary]:
        """Group one Window's resource series by a label."""
        window = window or arm_run.measurement
        out: dict[str, MetricSummary] = {}
        for sid, summary in window.probe_metrics.items():
            name, labels, _ = parse_ref(sid)
            if name != family or by not in labels:
                continue
            extra = [value for key, value in sorted(labels.items()) if key != by]
            out["/".join([labels[by], *extra])] = summary
        return out

    @staticmethod
    def _request_slice(window: Window, labels: dict[str, str]) -> RequestStats | None:
        if not labels:
            return window.request
        ((key, value),) = labels.items()
        return window.by_facet.get(key, {}).get(value)

    @staticmethod
    def _resource_missing(arm_run: ArmRun, name: str, labels: dict[str, str]) -> Missing:
        service = labels.get("service")
        if service is not None:
            probe_id = f"{name.split('.', 1)[0]}.{service}"
            if probe_id in arm_run.probe_errors:
                return Missing("probe_error")
        return Missing("no_slice" if labels else "no_data")
