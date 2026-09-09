"""Validity lens — how far THIS run's conclusions can be trusted.

Audits the run itself, not the service: early stops / interrupted in-flight,
whether the error breaker was even reachable at the observed request rate (a
breaker that can't accumulate ``min_n`` within the window is decoration), facet
slices with no contrast (a declared dimension where only one value ever fired),
and drop rates that taint the latency story.
"""

from __future__ import annotations

from perf_harness.analysis.base import Observation
from perf_harness.model import Run

#: time-to-min_n above this share of the trial window → breaker-reachability flag
BREAKER_WINDOW_SHARE = 0.5
#: drop_rate above this taints latency/throughput (mirrors report's saturation flag)
DROP_FLAG = 0.01


def analyze(run: Run, store=None) -> list[Observation]:  # noqa: ARG001 — uniform lens signature
    out: list[Observation] = []
    for r in run.trials:
        tid = r.label()
        s = r.stop
        for error in r.phase_errors:
            curve_note = (
                "measurement 已完成，保留性能曲线点"
                if r.measurement.complete
                else "measurement 未完成，不进入性能曲线"
            )
            out.append(
                Observation(
                    "validity",
                    "flag",
                    f"[{tid}] Trial 执行异常（{error.phase}）："
                    f"{error.error_type}: {error.message} — {curve_note}",
                    {
                        "trial": tid,
                        "phase": error.phase,
                        "error_type": error.error_type,
                        "message": error.message,
                        "measurement_complete": r.measurement.complete,
                    },
                )
            )
        if s.early and not r.phase_errors:
            snap = s.snapshot
            detail = (
                f"err {snap.error_rate * 100:.1f}% ({snap.errors}/{snap.sent}) @{snap.at_s:.0f}s"
                if snap
                else "—"
            )
            out.append(
                Observation(
                    "validity",
                    "flag",
                    f"[{tid}] 提前停止（{s.reason}）：{detail} — 该档数字是部分窗口，吞吐被低估",
                    {"trial": tid, "reason": s.reason, "interrupted": s.interrupted},
                )
            )
        if s.interrupted:
            out.append(
                Observation(
                    "validity",
                    "flag",
                    f"[{tid}] {s.interrupted} 个在途请求被强制 cancel（drain 窗口不够）",
                    {"trial": tid, "interrupted": s.interrupted},
                )
            )

        # breaker reachability: at the observed rps, how long until min_n is even
        # judged? Longer than half the window → the safety net mostly isn't armed.
        ld = r.arm.load
        if ld.abort_on_error_rate is not None and r.measurement.request.throughput_rps > 0:
            t_arm = ld.breaker_min_n / r.measurement.request.throughput_rps
            window = ld.schedule.total_s
            if window and t_arm / window > BREAKER_WINDOW_SHARE:
                out.append(
                    Observation(
                        "validity",
                        "flag",
                        f"[{tid}] 熔断起判需 ~{t_arm:.0f}s 攒满 min_n={ld.breaker_min_n}"
                        f"（占窗口 {t_arm / window * 100:.0f}%）— 低 rps 下安全网大半时间未武装",
                        {"trial": tid, "t_arm_s": round(t_arm, 1), "window_s": window},
                    )
                )

        # observation health: a probe that failed ticks leaves holes — its series'
        # flat-looking trends may be sampling artifacts, never read them as calm data
        for pname, pe in sorted(r.probe_errors.items()):
            out.append(
                Observation(
                    "validity",
                    "flag",
                    f"[{tid}] 观测断档：probe {pname} 失败 {pe.failures}/{pe.ticks} ticks"
                    f"（最后错误：{pe.last}）— 其指标趋势不可信",
                    {
                        "trial": tid,
                        "probe": pname,
                        "failures": pe.failures,
                        "ticks": pe.ticks,
                    },  # fmt: skip
                )
            )

        for key, vals in sorted(r.measurement.by_facet.items()):
            if len(vals) == 1:
                only = next(iter(vals))
                out.append(
                    Observation(
                        "validity",
                        "flag",
                        f"[{tid}] 维度 {key} 只观察到 '{only}'——该维度切片无对比意义"
                        f"（其余取值 0 样本）",
                        {"trial": tid, "facet": key, "only_value": only},
                    )
                )

        if r.measurement.request.drop_rate > DROP_FLAG:
            out.append(
                Observation(
                    "validity",
                    "flag",
                    f"[{tid}] drop率 {r.measurement.request.drop_rate * 100:.1f}%——压力机欠投放，"
                    f"延迟/吞吐低估真实负载",
                    {"trial": tid, "drop_rate": round(r.measurement.request.drop_rate, 4)},
                )
            )
    return out
