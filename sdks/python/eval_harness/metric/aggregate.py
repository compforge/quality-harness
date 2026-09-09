"""Pure aggregation over metric results — no I/O, no Worksheet.

Two channels aggregate differently:

- **quality** → ``weighted_overall``: weighted mean of the applicable quality
  metrics, **renormalised over those present**. Measurement results and
  ``score is None`` (abstained / not run) are excluded — never imputed as 0.
  Weights are relative; renormalisation is what makes a metric's weight stable
  regardless of which other metrics happen to apply.
- **measurement** → ``percentiles``: mean / p50 / p95 over raw values; these
  never enter the quality composite.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from eval_harness.model.sample import MetricResult


def weighted_overall(
    results: Mapping[str, MetricResult],
    weights: Mapping[str, float],
) -> float | None:
    """Weighted mean of applicable quality scores, renormalised over present ones.

    ``weights`` is the resolved per-metric weight (metric default already
    merged with any experiment override). A weight that references a metric not
    present, or <= 0, is ignored. Returns None if no quality metric contributes.
    """
    num = 0.0
    den = 0.0
    for name, r in results.items():
        if r.kind == "measure" or r.score is None:
            continue
        w = weights.get(name, 1.0)
        if w <= 0:
            continue
        num += r.score * w
        den += w
    if den == 0:
        return None
    return round(num / den, 3)


def _percentile(sorted_vals: list[float], q: float) -> float:
    """Linear-interpolation percentile (q in [0,1]); ``sorted_vals`` non-empty."""
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = q * (len(sorted_vals) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def percentiles(values: Iterable[float]) -> dict[str, float] | None:
    """mean / p50 / p95 over raw measurement values, or None if empty."""
    vals = sorted(float(v) for v in values if v is not None)
    if not vals:
        return None
    return {
        "mean": round(sum(vals) / len(vals), 3),
        "p50": round(_percentile(vals, 0.50), 3),
        "p95": round(_percentile(vals, 0.95), 3),
        "n": len(vals),
    }
