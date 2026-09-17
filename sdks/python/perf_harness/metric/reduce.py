"""REDUCE — collapse raw observations into typed summaries (the minting point).

The Engine only *collects* raw observations (verdict'd Outcomes + Probe series);
turning them into the typed ``MetricSummary`` currency happens here, in one place,
so percentile/caveat logic never leaks into the orchestration. Two minters: request
slices (a bag of Outcomes → ``RequestStats`` + per_request distributions) and, on the
Probe side, ``Probe.summarize`` (a Series → gauge/counter). This module is request-side.

Caveats are minted here and ride on the summary, so a number's trustworthiness
travels with it (a CO-biased p99 can't later be read as clean):
  - ``co_biased``   : closed-loop tail under-samples slow responses (arm_run-level).
  - ``high_drop``   : open-loop saturation shed real load (slice-level).
  - ``few_samples`` : too few observations for a stable percentile (per-distribution).
"""

from __future__ import annotations

import statistics
from collections import Counter

from perf_harness.metric import (
    Caveat,
    CounterSummary,
    DistributionSummary,
    GaugeSummary,
    MetricSummary,
    MetricValueKind,
)
from perf_harness.model import ArmRun, RequestStats, Sample

# Below this many observations a distribution's percentiles are too noisy to trust.
FEW_SAMPLES = 30
# Drop rate at/above which the generator shed real load → percentiles understate reality.
HIGH_DROP = 0.01


def time_series_summary(samples: list[Sample], value_kind: MetricValueKind) -> MetricSummary | None:
    """Collapse one time-sampled gauge/counter window into its typed summary."""
    if not samples:
        return None
    vals = [s.value for s in samples]
    if value_kind == "counter":
        increase = rate = None
        caveats: frozenset[Caveat] = frozenset()
        if len(samples) >= 2:
            dt = samples[-1].t - samples[0].t
            # Positive-delta accumulation matches Prometheus increase semantics:
            # a restart must not turn a cooldown-window rate negative.
            deltas = [b - a for a, b in zip(vals, vals[1:], strict=False)]
            increase = sum(d for d in deltas if d > 0)
            rate = increase / dt if dt > 0 else None
            if any(d < 0 for d in deltas):
                caveats = frozenset({"counter_reset"})
        return CounterSummary(total=vals[-1], rate=rate, increase=increase, caveats=caveats)
    if value_kind == "gauge":
        return GaugeSummary(last=vals[-1], mean=statistics.fmean(vals), peak=max(vals))
    return None


def reduce_requests(
    execution: ArmRun,
    start: float,
    end: float,
    *,
    case_id: str | None = None,
    facet: tuple[str, str] | None = None,
) -> RequestStats:
    records = [
        r
        for r in execution.requests
        if (case_id is None or r.case_id == case_id)
        and (facet is None or r.facets.get(facet[0]) == facet[1])
    ]
    by_id = {o.id: o.outcome for o in execution.operation_runs}
    cohort = [r for r in records if r.dispatched_at is not None and start <= r.dispatched_at < end]
    finished = [r for r in cohort if r.state == "finished"]
    outcomes = [by_id[r.operation_run_id] for r in finished]
    evaluations = [execution.evaluations.get(r.operation_run_id) for r in finished]
    durs = sorted(o.duration_ms for o in outcomes)
    n, n_ok = len(finished), sum(e is not None and e.ok for e in evaluations)
    arrived = [r for r in records if start <= r.scheduled_at < end]
    completions = [
        r
        for r in records
        if r.state == "finished" and r.finished_at is not None and start <= r.finished_at < end
    ]
    succeeded = sum(
        execution.evaluations.get(r.operation_run_id) is not None
        and execution.evaluations[r.operation_run_id].ok
        for r in completions
    )
    dropped = sum(r.state == "dropped" for r in arrived)
    interrupted = sum(r.state == "interrupted" for r in cohort)
    caveats = set()
    if n and (
        execution.arm.load.saturated
        or any(w.limited_s > 0 and w.start_s < end and w.end_s > start for w in execution.windows)
    ):
        caveats.add("co_biased")
    if dropped:
        caveats.add("high_drop")
    if interrupted or any(e is None for e in evaluations):
        caveats.add("incomplete")
    if n < 30:
        caveats.add("few_samples")
    metrics = {}
    values = {
        key: sorted(o.metrics[key] for o in outcomes if key in o.metrics)
        for key in {k for o in outcomes for k in o.metrics}
    }
    values["scheduler_lag_ms"] = sorted((r.arrived_at - r.scheduled_at) * 1000 for r in arrived)
    for key, vs in values.items():
        if vs:
            metrics[key] = DistributionSummary(
                n=len(vs),
                mean=sum(vs) / len(vs),
                p50=pct(vs, 0.5),
                p95=pct(vs, 0.95),
                p99=pct(vs, 0.99),
                caveats=frozenset(caveats),
            )
    # Inflight is event-based, including requests dispatched before this window.
    events = []
    current = sum(
        r.dispatched_at is not None
        and r.dispatched_at < start
        and (r.finished_at is None or r.finished_at >= start)
        for r in records
    )
    peak = current
    for r in records:
        if r.dispatched_at is not None and start <= r.dispatched_at < end:
            events.append((r.dispatched_at, 1))
        if (
            r.dispatched_at is not None
            and r.finished_at is not None
            and start <= r.finished_at < end
        ):
            events.append((r.finished_at, -1))
    for _, delta in sorted(events):
        current += delta
        peak = max(peak, current)
    dt = max(end - start, 1e-9)
    breakdown = Counter(
        (e.error_kind or "unknown") if e else "unjudged"
        for e in evaluations
        if e is None or not e.ok
    )
    return RequestStats(
        n=n,
        n_ok=n_ok,
        throughput_rps=len(completions) / dt,
        p50_ms=pct(durs, 0.5),
        p95_ms=pct(durs, 0.95),
        p99_ms=pct(durs, 0.99),
        mean_ms=sum(durs) / n if n else 0,
        error_rate=(n - n_ok) / n if n else 0,
        error_breakdown=dict(breakdown),
        n_dropped=dropped,
        n_interrupted=interrupted,
        arrived=len(arrived),
        dispatched=len(cohort),
        completed=len(completions),
        succeeded=succeeded,
        arrival_rps=len(arrived) / dt,
        dispatch_rps=len(cohort) / dt,
        success_rps=succeeded / dt,
        inflight_peak=peak,
        inflight_end=current,
        metrics=metrics,
        caveats=frozenset(caveats),
    )


def pct(values: list[float], q: float) -> float:
    import math

    return values[max(0, math.ceil(q * len(values)) - 1)] if values else 0.0


def unit_of(metric_name: str) -> str:
    """Best-effort unit from a per_request metric key suffix (perf convention:
    units are encoded in the name, e.g. ``ttft_ms`` / ``prompt_tokens``)."""
    for suffix, unit in (("_ms", "ms"), ("_bytes", "bytes"), ("_tokens", "tokens")):
        if metric_name.endswith(suffix):
            return unit
    return ""
