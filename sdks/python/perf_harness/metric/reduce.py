"""REDUCE — collapse raw observations into typed summaries (the minting point).

The Engine only *collects* raw observations (verdict'd Outcomes + Probe series);
turning them into the typed ``MetricSummary`` currency happens here, in one place,
so percentile/caveat logic never leaks into the orchestration. Two minters: request
slices (a bag of Outcomes → ``RequestStats`` + per_request distributions) and, on the
Probe side, ``Probe.summarize`` (a Series → gauge/counter). This module is request-side.

Caveats are minted here and ride on the summary, so a number's trustworthiness
travels with it (a CO-biased p99 can't later be read as clean):
  - ``co_biased``   : closed-loop tail under-samples slow responses (trial-level).
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
from perf_harness.model import Outcome, RequestStats, Sample

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


def request_stats(outcomes: list[Outcome], steady_s: float, *, closed: bool) -> RequestStats:
    """Collapse already-judged Outcomes into one Window's request-side stats.

    The input may be the whole Window or one facet slice. ``ok``/``error_kind``
    were set by ``judge`` upstream.

    Never-sent ``client_saturated`` drops are partitioned out FIRST: a drop has no
    real latency, so folding its 0ms into the percentiles would drag p50/p99 down
    (and inflate throughput) exactly when the SUT is slow — textbook coordinated
    omission. Drops are counted in ``n_dropped`` only; latency/throughput/errors are
    over sent requests. ``closed`` flags the trial's load model so the slice's
    latency carries the ``co_biased`` caveat."""
    sent = [o for o in outcomes if not o.dropped]
    n_dropped = sum(1 for o in outcomes if o.dropped)
    n = len(sent)
    n_ok = sum(1 for o in sent if o.ok)
    durs = sorted(o.duration_ms for o in sent)
    breakdown: Counter[str] = Counter()
    for o in sent:
        if not o.ok:
            breakdown[o.error_kind or "unknown"] += 1
    drop_rate = n_dropped / (n + n_dropped) if (n + n_dropped) else 0.0

    caveats: set[Caveat] = set()
    if closed and n:
        caveats.add("co_biased")  # closed-loop tail latency is optimistic
    if drop_rate >= HIGH_DROP:
        caveats.add("high_drop")  # shed load → latency/throughput understate reality
    if 0 < n < FEW_SAMPLES:
        caveats.add("few_samples")

    return RequestStats(
        n=n,
        n_ok=n_ok,
        throughput_rps=(n / steady_s) if steady_s > 0 else 0.0,
        p50_ms=pct(durs, 0.50),
        p95_ms=pct(durs, 0.95),
        p99_ms=pct(durs, 0.99),
        error_rate=((n - n_ok) / n) if n else 0.0,
        error_breakdown=dict(breakdown),
        n_dropped=n_dropped,
        mean_ms=(sum(durs) / len(durs)) if durs else 0.0,
        metrics=metric_stats(sent, closed=closed),
        caveats=frozenset(caveats),
    )


def metric_stats(sent: list[Outcome], *, closed: bool) -> dict[str, MetricSummary]:
    """Per_request metric distributions: each ``Outcome.metrics`` key (ttft_ms /
    first_<event>_ms …) → DistributionSummary over the requests that carried it. Only
    sent (non-dropped) outcomes contribute — a drop measured nothing. Each carries
    ``co_biased`` (closed-loop) and ``few_samples`` (thin) as warranted."""
    keys = {k for o in sent for k in o.metrics}
    out: dict[str, MetricSummary] = {}
    for k in keys:
        vals = sorted(o.metrics[k] for o in sent if k in o.metrics)
        if not vals:
            continue
        caveats: set[Caveat] = set()
        if closed:
            caveats.add("co_biased")
        if len(vals) < FEW_SAMPLES:
            caveats.add("few_samples")
        out[k] = DistributionSummary(
            n=len(vals),
            mean=sum(vals) / len(vals),
            p50=pct(vals, 0.50),
            p95=pct(vals, 0.95),
            p99=pct(vals, 0.99),
            caveats=frozenset(caveats),
        )
    return out


def pct(sorted_vals: list[float], q: float) -> float:
    """Nearest-rank percentile of an ascending list (0.0 when empty)."""
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, int(q * len(sorted_vals)))
    return sorted_vals[idx]


def unit_of(metric_name: str) -> str:
    """Best-effort unit from a per_request metric key suffix (perf convention:
    units are encoded in the name, e.g. ``ttft_ms`` / ``prompt_tokens``)."""
    for suffix, unit in (("_ms", "ms"), ("_bytes", "bytes"), ("_tokens", "tokens")):
        if metric_name.endswith(suffix):
            return unit
    return ""
