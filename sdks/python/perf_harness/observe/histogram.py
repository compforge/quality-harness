"""Classic bucket quantiles retaining overflow instead of clamping to a finite bucket."""

import math

from perf_harness.metric import ScalarSummary
from perf_harness.model import HistogramQuantile
from perf_harness.observe.prometheus import finite_number


def histogram_summary(data: dict, projection: HistogramQuantile) -> ScalarSummary | None:
    """Only explicit bucket evidence may produce +Inf; arbitrary scalar queries cannot.

    @spec A quantile in the final +Inf bucket remains +Inf through JSON and rendering.
    """
    if data.get("resultType") != "vector":
        raise ValueError("histogram query must return a vector grouped only by le")
    if not data.get("result"):
        raise ValueError("Prometheus query returned no samples")
    buckets: dict[float, float] = {}
    for row in data["result"]:
        labels = {k: v for k, v in row["metric"].items() if k != "__name__"}
        if set(labels) != {"le"}:
            raise ValueError("histogram query must aggregate by le only")
        boundary = float(labels["le"])
        count = finite_number(row["value"][1])
        if math.isnan(boundary) or boundary < 0 or count < 0 or boundary in buckets:
            raise ValueError("histogram has invalid or duplicate buckets")
        buckets[boundary] = count
    bounds = sorted(buckets)
    if len(bounds) < 2 or bounds[-1] != math.inf:
        raise ValueError("histogram requires finite buckets and a +Inf bucket")
    counts = [buckets[b] for b in bounds]
    if any(a > b for a, b in zip(counts, counts[1:], strict=False)):
        raise ValueError("histogram bucket counts are not monotonic")
    total = counts[-1]
    if total == 0:
        return None
    rank = projection.quantile * total
    lower, before = 0.0, 0.0
    value = math.inf
    for upper, count in zip(bounds, counts, strict=True):
        if count >= rank:
            if math.isfinite(upper):
                value = (
                    lower + (upper - lower) * (rank - before) / (count - before)
                ) * projection.scale
            break
        lower, before = upper, count
    return ScalarSummary(
        value,
        histogram={
            "quantile": projection.quantile,
            "sample_count": total,
            "max_finite_bucket": bounds[-2] * projection.scale,
            "overflow": math.isinf(value),
        },
    )
