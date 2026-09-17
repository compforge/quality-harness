"""Lifecycle view keeps event rates separate from dispatch-cohort latency."""

from harness_common.report_kit import Prose, Section, Table

from perf_harness.model import ArmRun

COLUMNS = [
    "arm",
    "window",
    "target_rps",
    "cap",
    "arrived",
    "dispatched",
    "completed",
    "succeeded",
    "dropped",
    "interrupted",
    "dispatch_rps",
    "completion_rps",
    "inflight_peak",
    "inflight_end",
    "limited_s",
    "end_reason",
]
EXPLANATION = (
    "arrived 记录源头实际接受的发起机会；dispatch/completion/success 按实际事件时刻计数。"
    "延迟与错误率属于该窗口 dispatch 的请求，包含排空后完成的结果。"
    "cooldown 只等待已有请求退出；满在途时暂停源头，不产生丢弃或积压。"
    "丢弃没有 OperationRun，中断不伪造成功或失败响应。"
    "target_rps/cap 为 LoadPlan 最大配置值；阶段曲线见 run.json。"
)


def rows(executions: list[ArmRun]) -> list[list[str]]:
    out = []
    for execution in executions:
        load = execution.arm.load
        for window in execution.windows:
            if window.kind not in {"warmup", "hold", "cooldown"} or window.request is None:
                continue
            s = window.request
            rate = "inf" if load.saturated else f"{load.peak_level:g}"
            out.append(
                [
                    execution.arm.id,
                    window.kind,
                    rate,
                    str(load.peak_inflight),
                    str(s.arrived),
                    str(s.dispatched),
                    str(s.completed),
                    str(s.succeeded),
                    str(s.n_dropped),
                    str(s.n_interrupted),
                    f"{s.dispatch_rps:.2f}",
                    f"{s.throughput_rps:.2f}",
                    str(s.inflight_peak),
                    str(s.inflight_end),
                    f"{window.limited_s:.3f}",
                    window.end_reason or "",
                ]
            )
    return out


def section(executions: list[ArmRun]) -> Section:
    return Section("请求生命周期", [Prose(EXPLANATION), Table(COLUMNS, rows(executions))])


def markdown(executions: list[ArmRun]) -> list[str]:
    return (
        [
            "## 请求生命周期",
            "",
            EXPLANATION,
            "",
            "| " + " | ".join(COLUMNS) + " |",
            "|" + "|".join(["---"] * len(COLUMNS)) + "|",
        ]
        + ["| " + " | ".join(row) + " |" for row in rows(executions)]
        + [""]
    )
