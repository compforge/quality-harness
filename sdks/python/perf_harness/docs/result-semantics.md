# 结果、Window 与 SLO 语义

## 理念 / 概念

perf 的实验层级是：

```text
Experiment → Arm → TrialRecord → Window → metric slice
```

- **Arm** 是参与比较的命名配置，由一个 `ResourceProfile` 和一个 `LoadProfile` 组成；
  `arm_id` 是 run 内对齐键。
- **TrialRecord** 是某个 Arm 的一次真实执行记录，不叫 Result，因为它同时保存 raw facts、
  stop、SLO 与可重算的聚合。
- **Stage** 是 `Schedule` 中计划的负载控制段。
- **Window** 是实际观测边界。request 与 resource 都用同一个半开区间 `[start_s, end_s)` 聚合；
  `window_id` 在 Trial 内唯一，`name` 只负责展示，因此 spike 中两个同名 baseline hold 不会合并。

metric label 只表达实体维度，如 `{difficulty="complex"}`、`{service="worker"}`；时间维度只由
Window 表达。这样资源指标和请求指标能在同一时间边界上比较，也不会把 `stage` 混入业务 facet。

## 流程

1. `Experiment.resolved_arms()` 把资源轴 × 负载轴展开为 Arm。
2. scheduler 按 Schedule 发压。每个 Outcome 记录**发射时刻**；长请求即使在下一 Stage 完成，
   仍属于发射时所在 Window。
3. Engine 在 warmup、Stage、提前停止和 cooldown 的实际边界上建立 Window：
   `measurement` 与各 `ramp` / `hold` 可重叠，cooldown 独立。
4. reducer 对每个 Window 同时归约 Outcome 和 Probe series，写入 `TrialRecord.windows`。
5. `MetricStore.query(trial, ref, window)` 统一读取；run.json schema 4 保存 Arm、Window 与归约结果，
   `load_run()` 可离线重放报告和 SLO。

## 关键设计

### SLO：label 选实体，WindowSelector 选时间

```yaml
slo:
  - { metric: error_rate, window: {kind: hold}, lt: 0.01 }
  - { metric: p99_ms, window: {kind: hold, level: 40}, lte: 800 }
  - { metric: 'p99_ms{difficulty="complex"}', lt: 1200 }
  - { metric: 'prometheus.active{service="worker"}.last',
      window: {kind: cooldown}, lte: 0 }
```

省略 `window` 等价于 `{kind: measurement}`。selector 支持 `kind`，并可用 `name` / `level`
继续收窄；一个 selector 命中多个 Window 时，一条 assertion 展开成多个 `SloCheck`，每条 check
记录自己的 `window_id`。

读不到 metric 或 slice 时状态是 `skipped`，不是 pass。默认运行门对普通 skip 宽松，
`strict_slo` 将其视为失败；cooldown skip 始终失败，因为回收门禁没有观测值不能算恢复成功。

### Capacity：只认 complete hold Window

SLO-aware capacity 按资源档选择最高的、`complete=true` 且该 Window 上所有 check 均通过的
hold。提前停止时，当前 partial hold 不计容量；在它之前已经完整结束的较低 hold 仍是有效证据。
measurement 的跨 Stage 平均不能替代 hold 结论。

### 请求归窗按发射时间

Outcome 在请求完成后才可写全，但归属时间在 dispatch 前捕获。若按完成时间归窗，`sleep 30s`
会从低档 hold 漂到高档 hold，扭曲两档的吞吐、延迟和容量判断。drop 没有执行过程，直接使用
arrival time。

### 停止与三层判定

- per-request：`Workload.judge(Outcome) → Verdict`；
- per-Window：`RequestStats` / resource summaries；
- per-run：SLO check 的 pass / fail / skipped。

`TrialStop` 记录 deadline、breaker 快照和在途请求 census。drop（未发）与 cancel（未完成）
都不是延迟事实；提前停止会使 run 失败，但不会抹掉已完成 Window 的历史证据。

### 阶段异常也是执行事实

`setup / measurement / deactivate / cooldown / cleanup` 抛出的普通异常写入
`TrialRecord.phase_errors`，而不是伪造成一次失败请求、Probe failure 或 SLO fail。这样即使
setup 阶段尚未发出任何请求，Harness 仍能持久化空的 partial measurement Window、run/report 和
状态为 `error` 的 `verdict.json`；调用方可以从 phase、异常类型和消息判断是环境准备失败还是发压阶段
失败。cleanup 会照常尝试，其异常作为同一 Trial 的后续 phase error 保留，不覆盖主异常。
Phase error 始终让 run 失败并停止后续 sweep，但不抹掉已完成的当前 measurement：只有
`measurement.complete=false` 的异常 Trial 才不进入容量、资源和延迟曲线；deactivate、cooldown 或
cleanup 阶段失败时，已完成的 measurement 仍是有效事实。
