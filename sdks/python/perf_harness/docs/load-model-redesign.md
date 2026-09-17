# Perf 负载与执行模型

## 目标与归属

SSE 请求可以占用连接数分钟。到达率与同时在途数必须独立配置；4 request/s 持续一分钟表示
240 次计划到达，不表示始终只有 4 个请求。`max_concurrency` 限制从调用开始到完整响应结束的占用。

common 拥有 ExperimentRun、Execution、OperationRun、Outcome 与 Service/Workload 的通用语义；
toolbox 拥有 HTTP 客户端生命周期和 SSE 解析；perf 只拥有负载调度、请求记录、统计与容量判定。
原协议适配器更名为 Runner，避免与 common 的部署承载单元 Workload 混用。

```text
ExperimentRun.executions
  ArmRun（固定 Arm + 目标 Service/Workload + Case 选择）
    operation_runs → OperationRun → Outcome
    requests → RequestRecord → operation_run_id（仅实际调用有值）
    evaluations → operation_run_id → RequestEvaluation
```

Arm 是资源配置与 LoadPlan 的命名组合。一次 ArmRun 产生多少调用由加压器决定；简单 Case 的
一次触发对应一个 OperationRun。Outcome 只有一份，调度记录与判定通过 ID 引用，不复制事实。

## LoadPlan

```yaml
load:
  request_rate: 4
  max_concurrency: 32
  duration_s: 60
  drain_timeout_s: 180
```

- 有限 `request_rate`：独立到达过程，默认 constant，也可用 `arrival: poisson`。
  满并发时立即记录 `concurrency_limit` 丢弃，不排队，不创建等待 slot 的 Task。
- `request_rate: inf`：空出 slot 即补充请求，吞吐是测量结果；不创建虚拟用户与 think-time 模型。
- `max_concurrency` 必填且为正整数。HTTP 池按整个 LoadPlan 的最大并发配置。
- `duration_s` 是发压时长；`drain_timeout_s` 是停止发压后等待已有请求完成的上限。
  超时取消并等待任务退出后，才释放借用的连接和外部资源。
- `warmup_s` 只决定统计窗口起点，不改变调度。`seed` 控制本语言内的可重现随机选择，
  不承诺 Python 与 TypeScript 生成完全相同的随机序列。
- `abort_on_error_rate` / `breaker_min_n` 根据已完成请求的独立 Judge 结果熔断。

有限速率以累计到达量反解计划时刻，第一条到达从 t=0 开始。卡顿不会重置到达时钟。
截止前积压的到达逐条处理；截止后仍未处理的计划到达记为 `scheduler_deadline`，绝不补发。
在途 Task 数受并发上限控制；保留的请求证据仍随请求总数增长，不声称总内存恒定。

## Stage

每段显式配置速率与并发上限，不使用单位随模式变化的 level 输入。

```yaml
load:
  request_rate: 0
  max_concurrency: 8
  duration_s: 90
  stages:
    - {kind: ramp, duration_s: 30, request_rate: 4, max_concurrency: 32}
    - {kind: hold, duration_s: 60, request_rate: 4, max_concurrency: 32}
```

ramp 从上一段终值线性变化，并发向下取整且至少为 1；hold 直接使用本段值。
各段时长之和必须等于 duration_s；单个 LoadPlan 不混合有限速率与 inf。
降并发只停止补充，不取消已有请求，所以降档期间实际 inflight 可暂时高于新的上限。
重复阶段名称允许存在，`stage-0` 等 window_id 仍唯一。

## 生命周期与边界

`setup → 发压 → drain/cancel → deactivate → cooldown → cleanup → 释放客户端`。
Observer 在排空与 deactivate 期间继续采样；cooldown 只负责停用后的资源观测。
TypeScript Runner 必须响应 AbortSignal，Python Runner 必须传播 CancelledError；框架不会在
请求还使用客户端时强行关闭池。生命周期异常保留 phase_errors，终止 sweep，不冒充业务失败。

指标与落盘口径见 [结果语义](result-semantics.md) 和
[跨语言契约](../../../../spec/perf-contract.md)。本次为破坏性重构，不保留旧 LoadProfile、
Trial、perf Workload、Schedule/Pacing 或 schema 4 的兼容入口。
