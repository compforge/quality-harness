"""Capacity lens — does throughput scale with load, and where does it stop?

Closed-loop sweeps: per-user throughput (rps ÷ level) is the scaling signal — flat
means linear, a drop means queueing started between those two levels (the knee).
Little's law (N = X·R) cross-checks the closed loop's self-consistency. Open-loop
sweeps: offered vs achieved rate + drops is the saturation signal.
"""

from __future__ import annotations

from perf_harness.analysis.base import Observation, by_resources
from perf_harness.metric import parse_ref
from perf_harness.metric.store import MetricStore
from perf_harness.model import Run, TrialRecord

#: per-user throughput dropping ≥ this fraction vs the previous level → the knee flag
KNEE_DROP = 0.15


def analyze(run: Run, store: MetricStore) -> list[Observation]:
    out: list[Observation] = []
    for label, rs in by_resources(run.trials):
        if len(rs) < 2:
            continue
        if rs[0].arm.load.model == "closed":
            out.extend(_closed_scaling(label, rs))
        else:
            out.extend(_open_saturation(label, rs))
        out.extend(_amplification(label, rs, store))
    return out


def _closed_scaling(label: str, rs: list[TrialRecord]) -> list[Observation]:
    out: list[Observation] = []
    rows = []
    for r in rs:
        n = r.arm.load.schedule.peak_level
        x = r.measurement.request.throughput_rps
        # Little's law N = X·R: how many users the measured (rps, p50) pair implies —
        # a closed loop is self-consistent when this tracks the configured level
        implied_n = x * r.measurement.request.p50_ms / 1000.0
        rows.append(
            {
                "level": n,
                "rps": round(x, 3),
                "per_user_rps": round(x / n, 4) if n else None,
                "little_implied_users": round(implied_n, 2),
            }
        )
    out.append(
        Observation(
            "capacity",
            "fact",
            f"[{label}] closed 扩展性：level→rps {[(r['level'], r['rps']) for r in rows]}",
            {"rows": rows},
        )
    )
    for prev, cur in zip(rows, rows[1:], strict=False):
        if not (prev["per_user_rps"] and cur["per_user_rps"]):
            continue
        drop = 1 - cur["per_user_rps"] / prev["per_user_rps"]
        if drop >= KNEE_DROP:
            out.append(
                Observation(
                    "capacity",
                    "flag",
                    f"[{label}] 扩展拐点：并发 {prev['level']:g}→{cur['level']:g} "
                    f"per-user 吞吐下降 {drop * 100:.0f}%（排队开始）",
                    {
                        "from_level": prev["level"],
                        "to_level": cur["level"],
                        "per_user_drop_pct": round(drop * 100, 1),
                    },  # fmt: skip
                )
            )
    return out


def _open_saturation(label: str, rs: list[TrialRecord]) -> list[Observation]:
    rows = [
        {
            "offered": r.arm.load.schedule.peak_level,
            "achieved_rps": round(r.measurement.request.throughput_rps, 3),
            "drop_rate": round(r.measurement.request.drop_rate, 4),
        }
        for r in rs
    ]
    return [
        Observation(
            "capacity",
            "fact",
            f"[{label}] open 饱和度：offered→achieved "
            f"{[(r['offered'], r['achieved_rps']) for r in rows]}",
            {"rows": rows},
        )
    ]


def _amplification(label: str, rs: list[TrialRecord], store: MetricStore) -> list[Observation]:
    """Server-observed request rate ÷ client rps, per service exposing ``req_total``.
    ≫1 means one external request fans out / a fixed background flow dominates —
    either way ``req_total`` must not be read as business throughput."""
    out: list[Observation] = []
    services = sorted(
        {
            parse_ref(sid)[1].get("service", "")
            for r in rs
            for sid in r.measurement.probe_metrics
            if parse_ref(sid)[0] == "metrics.req_total"
        }
        - {""}
    )
    for svc in services:
        rows = []
        for r in rs:
            rate = store.query(r, f'metrics.req_total{{service="{svc}"}}.rate')
            if not isinstance(rate, float) or not r.measurement.request.throughput_rps:
                continue
            rows.append(
                {
                    "level": r.arm.load.schedule.peak_level,
                    "server_rate": round(rate, 2),
                    "amplification": round(rate / r.measurement.request.throughput_rps, 1),
                }
            )
        if rows:
            out.append(
                Observation(
                    "capacity",
                    "fact",
                    f"[{label}] {svc} 服务端请求放大：client rps 的 "
                    f"{[r['amplification'] for r in rows]} 倍（含背景流量，勿当业务吞吐）",
                    {"service": svc, "rows": rows},
                )
            )
    return out
