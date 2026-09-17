# Perf 负载与执行模型

## 目标与归属

SSE 请求可以占用连接数分钟。发起节奏与同时在途数必须独立配置；4 request/s 持续一分钟，
在容量允许时最多发出约 240 个请求，不表示始终只有 4 个请求。`max_inflight` 限制从调用开始到完整响应结束的占用。

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

## LoadPlan 与五阶段

`setup → warmup → hold → cooldown → cleanup`。

```yaml
load:
  request_rate: 4
  max_inflight: 100
  warmup: {step_s: 5}
  hold_s: 60
  cooldown_timeout_s: 180
```

`request_rate` 控制有空位时的发起节奏，`max_inflight` 覆盖完整响应生命周期，包括 SSE。
有限速率默认为等间隔，也支持均值速率为 request_rate 的 poisson 间隔。满在途时暂停源头，
没有 pending queue、虚构到达或 catch-up 补发。`inf` 直接进入 hold，按空位补充。

- setup 准备 Runner、客户端和观测器。
- warmup 从 `min(1, request_rate)` 开始，每隔 step_s 倍增并截到目标速率。达到目标速率或
  首次达到在途上限即进入 hold；step_s 为零表示直接开始 hold。
- hold 始终以配置的目标 request_rate 为补位节奏，受到 max_inflight 的反馈控制。
  warmup 提前触顶不冻结低速档。hold_s 默认 60 秒，从实际进入 hold 时计时；SSE 可显式配置
  180–300 秒观察请求完成和补位。
- cooldown 到来后不再发新请求，最多等待 cooldown_timeout_s，超时取消并 join 剩余请求。
  取消完成之前不释放请求使用的客户端。可选的 Experiment.cooldown_s 在内部停用动作后延长资源观测。
- cleanup 清理业务任务和本轮持有的资源；异常路径同样执行。

总发压时长由实际 warmup 与 hold 决定，调用者无需手算总 duration_s。
错误率熔断使用已完成且独立判定的请求，异常和外部取消保留已发生的窗口与调用事实。
seed 控制本语言内的可重现选择，不要求不同语言生成相同随机序列。

## Stage 与实际 Window

Stage 表达计划；Window 记录实际边界。简单配置生成若干 warmup 段和一个 hold 段，提前触顶
可以跳过尚未执行的 warmup 段。Window 的 end_reason 记录 inflight_limit、target_rate、duration
或异常退出，limited_s 记录发起节奏已经允许、但被在途上限阻塞的时间。

需要阶梯资源上限或 spike 等高级实验时可显式声明 stages。每段声明 duration_s、request_rate、
max_inflight 和 kind；hold 使用本段目标，ramp 从前一目标线性变化。显式阶段自行定义时长，
不额外叠加默认 warmup 和 hold_s。降在途上限只暂停补位，不取消已有请求。

measurement 从第一个实际 hold 开始，warmup 和 cooldown 分别保留观测窗口；阶段窗口都保留
真实起止时刻。资源与请求使用同一边界。有限速率被在途限制阻塞时，实际发出速率可能低于目标，
不能把配置 QPS 当作已证明的容量；这类窗口不会用于目标速率容量确认或资源斜率外推。

Python Runner 必须传播 CancelledError，TypeScript Runner 必须响应 AbortSignal。
请求事实、统计和持久化见 [结果语义](result-semantics.md) 与
[跨语言契约](../../../../spec/perf-contract.md)。
