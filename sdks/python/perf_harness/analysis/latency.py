"""Latency lens — how the latency shape degrades with load, and how far to trust it.

Three questions per sweep: (1) does the tail diverge faster than the median
(queueing signature)? (2) is the first byte stable while the total grows (the
bottleneck is after the first byte — generation/orchestration, not accept/route)?
(3) are the percentiles statistically meaningful at all (sample count, p95==p99
exhaustion, and the caveats reduce already minted — co_biased/high_drop)?
"""

from __future__ import annotations

from perf_harness.analysis.base import Observation, by_resources
from perf_harness.metric.store import MetricStore
from perf_harness.model import Run, TrialRecord

#: below this many sent requests a p99 is an observed-max, not a percentile
MIN_N_FOR_TAIL = 100
#: ttft p50 spread (max-min over mean) below this → "first byte stable" fact
TTFT_STABLE_SPREAD = 0.10
#: caveat → what it means for the reader (mirrors reduce.py's minting reasons)
_CAVEAT_TEXT = {
    "co_biased": "闭环 CO-bias——慢响应期间少采样，尾延迟偏乐观，勿当严格 SLO 证据",
    "high_drop": "开环 drop 比例高——压力机欠投放，延迟分位低估真实负载",
    "few_samples": "样本过少——分位数波动大",
}


def analyze(run: Run, store: MetricStore) -> list[Observation]:
    out: list[Observation] = []
    for label, rs in by_resources(run.trials):
        out.extend(_degradation(label, rs))
        out.extend(_ttft(label, rs, store))
        out.extend(_adequacy(label, rs))
    return out


def _degradation(label: str, rs: list[TrialRecord]) -> list[Observation]:
    rows = [
        {
            "level": r.arm.load.schedule.peak_level,
            "p50_ms": round(r.measurement.request.p50_ms),
            "p95_ms": round(r.measurement.request.p95_ms),
            "p99_ms": round(r.measurement.request.p99_ms),
            "tail_ratio": round(r.measurement.request.p99_ms / r.measurement.request.p50_ms, 2)
            if r.measurement.request.p50_ms
            else None,
        }
        for r in rs
    ]
    out = [
        Observation(
            "latency",
            "fact",
            f"[{label}] 延迟随压力：level→(p50,p99) "
            f"{[(r['level'], r['p50_ms'], r['p99_ms']) for r in rows]}",
            {"rows": rows},
        )
    ]
    first, last = rows[0], rows[-1]
    if len(rows) >= 2 and first["p50_ms"] and first["p99_ms"]:
        p50_g = last["p50_ms"] / first["p50_ms"] - 1
        p99_g = last["p99_ms"] / first["p99_ms"] - 1
        if p99_g > max(2 * p50_g, 0.1):
            out.append(
                Observation(
                    "latency",
                    "flag",
                    f"[{label}] 尾部发散：p99 增长 {p99_g * 100:.0f}% > 2×p50 增长"
                    f"（{p50_g * 100:.0f}%）— 排队签名",
                    {
                        "p50_growth_pct": round(p50_g * 100, 1),
                        "p99_growth_pct": round(p99_g * 100, 1),
                    },  # fmt: skip
                )
            )
    return out


def _ttft(label: str, rs: list[TrialRecord], store: MetricStore) -> list[Observation]:
    vals = []
    for r in rs:
        v = store.query(r, "ttft_ms.p50")
        if isinstance(v, float):
            vals.append((r.arm.load.schedule.peak_level, v))
    if len(vals) < 2:
        return []
    mean = sum(v for _, v in vals) / len(vals)
    spread = (max(v for _, v in vals) - min(v for _, v in vals)) / mean if mean else 0
    top = rs[-1]
    share = (
        vals[-1][1] / top.measurement.request.p50_ms * 100
        if top.measurement.request.p50_ms
        else None
    )
    ev = {
        "ttft_p50_by_level": [(lv, round(v, 1)) for lv, v in vals],
        "spread_pct": round(spread * 100, 1),
        "share_of_total_pct": round(share, 1) if share else None,
    }
    if spread <= TTFT_STABLE_SPREAD:
        return [
            Observation(
                "latency",
                "fact",
                f"[{label}] 首字节稳定：ttft p50 ~{mean:.0f}ms 各档基本不动"
                f"（占总延迟 {share:.1f}%）— 劣化都在首字节之后（生成/编排段）",
                ev,
            )
        ]
    return [
        Observation(
            "latency",
            "flag",
            f"[{label}] ttft 随压力漂移 {spread * 100:.0f}%——接入/首包链路也在劣化",
            ev,
        )
    ]


def _adequacy(label: str, rs: list[TrialRecord]) -> list[Observation]:
    out: list[Observation] = []
    for r in rs:
        o = r.measurement.request
        lv = r.arm.load.schedule.peak_level
        if o.n and o.n < MIN_N_FOR_TAIL:
            out.append(
                Observation(
                    "latency",
                    "flag",
                    f"[{label}] level {lv:g}: n={o.n} < {MIN_N_FOR_TAIL}，"
                    f"p99 是观测极值而非分位数",
                    {"level": lv, "n": o.n},
                )
            )
        if o.n and o.p95_ms == o.p99_ms and o.p95_ms:
            out.append(
                Observation(
                    "latency",
                    "flag",
                    f"[{label}] level {lv:g}: p95==p99（{o.p95_ms:.0f}ms）— 尾部样本枯竭",
                    {"level": lv, "n": o.n, "p95_ms": o.p95_ms},
                )
            )
        for cav in sorted(o.caveats):
            out.append(
                Observation(
                    "latency",
                    "flag",
                    f"[{label}] level {lv:g}: {_CAVEAT_TEXT.get(cav, cav)}",
                    {"level": lv, "caveat": cav},
                )
            )
    return out
