"""Lifecycle view keeps event rates separate from dispatch-cohort latency."""

import json

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
    "completed_by_code",
    "unjudged",
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
            if window.kind != "phase" or window.request is None:
                continue
            s = window.request
            rate = "inf" if load.saturated else f"{load.peak_level:g}"
            out.append(
                [
                    execution.arm.id,
                    window.phase or "",
                    rate,
                    str(load.peak_inflight),
                    str(s.arrived),
                    str(s.dispatched),
                    str(s.completed),
                    str(s.succeeded),
                    json.dumps(s.completed_by_code, ensure_ascii=False, sort_keys=True),
                    str(s.unjudged),
                    str(s.n_dropped),
                    str(s.interrupted),
                    f"{s.dispatch_rps:.2f}",
                    f"{s.throughput_rps:.2f}",
                    str(s.inflight_peak),
                    str(s.inflight_end),
                    f"{window.limited_s:.3f}",
                    window.end_reason or "",
                ]
            )
    return out


PHASE_COLUMNS = ["arm", "phase", "start_s", "end_s", "duration_s", "complete"]


def phase_rows(executions: list[ArmRun]) -> list[list[str]]:
    return [
        [
            run.arm.id,
            p.kind,
            f"{p.start_s:.3f}",
            f"{p.end_s:.3f}",
            f"{p.duration_s:.3f}",
            str(p.complete),
        ]
        for run in executions
        for p in run.phases
    ]


def section(executions: list[ArmRun]) -> Section:
    return Section(
        "执行阶段与流量统计",
        [
            Prose(
                "setup → warmup → hold → cooldown → cleanup 串行、不重叠。"
                "setup/cleanup 只准备与清理，不计入流量观测或吞吐分母；时间以开始加压为零点。"
            ),
            Table(PHASE_COLUMNS, phase_rows(executions)),
            Prose(EXPLANATION),
            Table(COLUMNS, rows(executions)),
        ],
    )


def markdown(executions: list[ArmRun]) -> list[str]:
    lines = [
        "## 执行阶段与流量统计",
        "",
        "setup/cleanup 只负责准备与清理，不计入流量观测。五阶段串行、不重叠。",
        "",
    ]
    for columns, values in [(PHASE_COLUMNS, phase_rows(executions)), (COLUMNS, rows(executions))]:
        lines += ["| " + " | ".join(columns) + " |", "|" + "|".join(["---"] * len(columns)) + "|"]
        lines += [
            "| " + " | ".join(cell.replace("|", "\\|") for cell in row) + " |" for row in values
        ]
        lines.append("")
    return lines + [EXPLANATION, ""]
