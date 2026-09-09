"""Resource lens — per service: usage vs its OWN request/limit, sensitivity to load.

Walks the time_sampled gauge families through the store's service pivot (per_pod
entities like ``chat/chat-abc`` fall out of the same pivot, zero special-casing):
headroom at the top level, usage-vs-level slope (which service is load-sensitive),
linear extrapolation to the request/limit lines, idle detection (a service the
workload never touched — a coverage gap, not a capacity result), and within-trial
memory growth (leak smell needs a longer soak, but a fast climb shows here).
"""

from __future__ import annotations

from perf_harness.analysis.base import Observation, by_resources, linfit, pct
from perf_harness.metric.store import MetricStore
from perf_harness.model import Run, TrialRecord

#: peak ≥ this share of the limit → "approaching its limit" flag
NEAR_LIMIT = 0.8
#: top-level peak below this share of request (and tiny absolutely) → idle flag
IDLE_SHARE = 0.05
#: within-trial growth above this share of the start value → growth flag
GROWTH_PCT = 10.0

# usage family → its request/limit bound families (the builtin `limits` probe's
# vocabulary; same units by construction so the numbers compare directly)
_BOUNDS = {
    "millicores": ("limits.cpu_request", "limits.cpu_limit"),
    "MiB": ("limits.mem_request", "limits.mem_limit"),
}


def analyze(run: Run, store: MetricStore) -> list[Observation]:
    out: list[Observation] = []
    for label, rs in by_resources(run.trials):
        top = rs[-1]  # headroom is judged at the hottest level of the sweep
        for family in _usage_families(top):
            unit = top.metrics[family].unit
            req_fam, lim_fam = _BOUNDS[unit]
            usage = _peaks(store, top, family)
            reqs = _peaks(store, top, req_fam)
            lims = _peaks(store, top, lim_fam)
            for svc, peak in sorted(usage.items()):
                req, lim = reqs.get(svc), lims.get(svc)
                out.extend(_headroom(label, family, unit, svc, peak, req, lim))
                out.extend(_slope(label, rs, store, family, unit, svc, req, lim))
            out.extend(_growth(label, top, family, unit))
    return out


def _usage_families(r: TrialRecord) -> list[str]:
    """Resource-side gauges in a bounded unit, excluding the bounds themselves."""
    return sorted(
        name
        for name, fam in r.metrics.items()
        if fam.side == "resource"
        and fam.value_kind == "gauge"
        and fam.unit in _BOUNDS
        and not name.startswith("limits.")
    )


def _peaks(store: MetricStore, r: TrialRecord, family: str) -> dict[str, float]:
    """service/entity → peak for one family (per_pod entities pivot out the same way)."""
    out = {}
    for svc, summary in store.pivot(r, family, "service").items():
        peak = getattr(summary, "peak", None)
        if peak is not None:
            out[svc] = peak
    return out


def _headroom(
    label: str, family: str, unit: str, svc: str,
    peak: float, req: float | None, lim: float | None,
) -> list[Observation]:  # fmt: skip
    ev = {
        "service": svc, "family": family, "unit": unit, "peak": round(peak, 1),
        "request": req, "limit": lim,
        "pct_of_request": pct(peak, req or 0),
        "pct_of_limit": pct(peak, lim or 0),
    }  # fmt: skip
    bounds = f"request {req:g}/limit {lim:g}" if req and lim else "（无 request/limit 数据）"
    out = [
        Observation(
            "resource",
            "fact",
            f"[{label}] {svc} {family} 峰值 {peak:.0f}{unit} vs {bounds}",
            ev,
        )  # fmt: skip
    ]
    if lim and peak / lim >= NEAR_LIMIT:
        out.append(
            Observation(
                "resource",
                "flag",
                f"[{label}] {svc} {family} 峰值已达 limit 的 {peak / lim * 100:.0f}%"
                f"（{peak:.0f}/{lim:g}{unit}）",
                ev,
            )  # fmt: skip
        )
    return out


def _slope(
    label: str, rs: list[TrialRecord], store: MetricStore,
    family: str, unit: str, svc: str, req: float | None, lim: float | None,
) -> list[Observation]:  # fmt: skip
    points = []
    for r in rs:
        peak = _peaks(store, r, family).get(svc)
        if peak is not None:
            points.append((r.arm.load.schedule.peak_level, peak))
    fit = linfit(points)
    if fit is None:
        return []
    slope, intercept = fit
    top_level, top_peak = points[-1]
    # idle: the sweep barely moves this service — a workload-coverage gap, not headroom
    if req and top_peak / req < IDLE_SHARE and abs(slope) < req * 0.005:
        return [
            Observation(
                "resource",
                "flag",
                f"[{label}] {svc} {family} 几乎未被压到（峰值 {top_peak:.0f}{unit}，"
                f"<request 的 {IDLE_SHARE * 100:.0f}%）— 该服务对此负载形态无覆盖",
                {
                    "service": svc,
                    "family": family,
                    "peak": round(top_peak, 1),
                    "request": req,
                    "slope_per_level": round(slope, 2),
                },  # fmt: skip
            )
        ]
    if slope <= 0:
        return []
    ev: dict[str, object] = {
        "service": svc, "family": family,
        "slope_per_level": round(slope, 2), "points": points,
    }  # fmt: skip
    parts = [f"斜率 {slope:.1f}{unit}/level"]
    for bound, name in ((req, "request"), (lim, "limit")):
        if bound and top_peak < bound:
            at = (bound - intercept) / slope
            ev[f"level_at_{name}"] = round(at, 1)
            parts.append(f"线性外推 ~{at:.0f} 并发触 {name}({bound:g}{unit})")
    return [
        Observation(
            "resource",
            "fact",
            f"[{label}] {svc} {family} 对负载敏感：{'，'.join(parts)}（线性外推仅供方向）",
            ev,
        )
    ]


def _growth(label: str, top: TrialRecord, family: str, unit: str) -> list[Observation]:
    """Within-trial first→last growth at the top level (fast-climb smell; a slow leak
    still needs a soak run — this window is short by design)."""
    out: list[Observation] = []
    if unit != "MiB":  # growth-watch is a memory question; cpu fluctuates by nature
        return out
    for sid, series in sorted(top.series.items()):
        if not sid.startswith(family) or len(series.samples) < 2:
            continue
        first, last = series.samples[0].value, series.samples[-1].value
        growth = last - first
        if first > 0 and growth / first * 100 >= GROWTH_PCT:
            out.append(
                Observation(
                    "resource",
                    "flag",
                    f"[{label}] {sid} 单 trial 内增长 {growth:.0f}{unit}"
                    f"（{growth / first * 100:.0f}%）— 需更长 soak 排除泄漏",
                    {"series": sid, "first": round(first, 1), "last": round(last, 1)},
                )
            )
    return out
