# Perf Harness 跨语言契约

本契约是 Python、TypeScript 及后续语言实现共同遵守的稳定边界。任何一种语言实现都不是另一种
语言的 canonical implementation；实现可以按消费场景覆盖不同 feature，但同名概念、调度语义、
对齐键和落盘字段必须以本文件及 schema 为准。

## 核心名词

```text
Experiment 比较一组 Arm
Arm = ResourceProfile × LoadProfile 的命名配置
Trial = Arm 的一次真实执行
Stage = 计划中的负载控制段
Window = 实际观测与归约边界
Outcome = 一次已发请求的原始事实
Verdict = Workload 对 Outcome 的纯判定
```

按 quality-harness Kernel 语义，Outcome 和 Probe sample 是 Observation。Perf 必须为每张分析表声明一个
Unit grain：request、window、run 是不同 Unit，不能混成同一行。raw/model Run facts 构成可离线复用
Dataset；每次 EvaluationRun 直接记录所选 Workload judge、SLO 与 analysis 组件及其配置，并形成
对应 Worksheet。更换 SLO、gate 或分析透镜不得重新发压。具体顶层定义见
[`../docs/kernel.md`](../docs/kernel.md#dataset-与反复评估)。

- `case_id` 标识稳定输入资产；同一 Case 进入不同 Arm 时保持不变。
- `arm_id` 是 Experiment 内对照配置的稳定键。
- `window_id` 是 Trial 内时间切片的局部键。
- `trace_id` 等遥测关联键进入 `Outcome.meta`，不得只存在于人类报告。

## Case 与加压边界

Case / CaseSet 是稳定的输入资产，canonical schema 由 spec-case 维护。Case 可以声明 `id`、`input`、
`facets`、`requires`、`judge`、`binding` 等测试语义，但不携带环境地址、凭证、并发度或流量权重。
Harness 只消费执行所需的最小 runtime view（至少 `id` / `input`，可带 `facets`），不复制或重新定义
Case schema。

一次 Perf Experiment 可以从一个 CaseSet 选择一个或多个 Case，并在本次 Experiment 的 `case mix`
里声明权重。权重属于本次加压计划，而不是 Case 资产。Harness 负责按 mix 选择 Case、调度 dispatch、
控制并发/到达率、停止和归约，并在 `Window.by_case` 产出逐 Case 统计。

具体服务协议由 Workload/driver 实现。一次 Trial 的 Service、资源、负载与 run id 形成共享且不可变的
TrialContext；Harness 每次 dispatch 只调用一次 `fire(FireContext)`，其中 FireContext 在 TrialContext
之上组合本次 Case。driver 负责把 Case input 变成一次 HTTP/SSE 等请求并返回一次 Outcome，必须支持
Harness 发起的并发调用，不能修改共享 TrialContext 或在内部再启动隐藏的加压循环。这样，同一个 Case
runner 可被 Perf、单 Case 调试或其它 Case Harness 入口复用，而调度策略仍只有一个权威实现。各语言
可以用嵌套或扁平类型表达 FireContext，但字段语义和生命周期必须一致。

完整 Perf 是高影响验证，只能在合适环境和明确授权下运行。运行窗口、凭据、目标 revision 与发布
门禁由部署领域持有；Harness 提供 CLI、API 或 Job 入口，不表示可以绕过这些约束。

## 负载语义

- `closed` 表示 N 个虚拟用户循环 `fire → pacing → fire`，强度单位是并发用户数。
- `open` 表示独立于响应时间的到达过程，强度单位是 request/s。
- `Schedule` 由连续 Stage 组成；`ramp` 从进入该段的强度线性变化到 `to_level`，`hold`
  在整段保持 `to_level`。
- 请求按 dispatch 时刻进入 Window。不得按完成时刻把慢请求移动到后一个 Stage/Window。
- 未发出的 drop 和停止时被强制中断的在途请求不是延迟样本。
- `abort_on_error_rate` 是 Trial 内保护被测系统的熔断，不等同于 run 级 SLO。
- `max_requests`、`max_inflight` 是安全边界；执行入口不支持某项时必须显式拒绝对应运行配置，不能静默
  忽略。离线 reader 仍按产物契约忽略自己不认识的可选字段，以便读取其它实现的 IR。

## Workload 与判定

服务协议通过 Workload 扩展：

1. `fire(FireContext)` 只记录 HTTP/SSE、耗时、业务 ID 和异常等原始 `Outcome`，即 request Observation；
2. `judge(outcome)` 是单请求成功/失败的唯一权威，必须是纯函数；
3. Trial 生命周期固定为 `setup → measurement → deactivate → cleanup`，cleanup 总会尝试执行。

生命周期普通异常不是请求 Outcome、Probe failure 或 SLO fail。Harness 应把异常按发生顺序记录到
Trial 的可选 `phase_errors`，每项至少包含 `phase`、`error_type` 和 `message`；未进入或未完整执行
measurement 时使用 `stop.reason=aborted`。这类 Trial 的 run gate 必须失败，`verdict.json` 状态为
`error`，但 Harness 仍应写出 run、raw、report 与 verdict 产物。进程取消、键盘中断等控制流异常可以
继续向调用方传播。出现 phase error 后必须结束当前 sweep，不能把可能受残留状态污染的后续 Arm
当作可比较的性能 Observation。Phase error 仍让 run gate 失败，但不抹掉已完成的当前 measurement；
当前 Trial 是否能进入性能曲线由 `measurement.complete` 决定，而不是由 phase 名称决定。

资源观测与请求判定正交。Prombed、Prometheus、Kubernetes 等 Probe 可以由具体实现、消费方或
[Harness 工具箱](../docs/toolbox.md)提供；其缺失不能改变 Outcome 的原始事实。

## Window 与统计

每个 Trial 至少形成一个 `measurement` Window，并可形成对应 Stage 的 `ramp` / `hold` Window。
Window 使用半开区间 `[start_s, end_s)`。请求延迟、吞吐、错误率及 per-request metric 都从该区间
内 dispatch 的 Outcome 归约；资源时序也使用同一边界。

百分位采用 nearest-rank 约定：升序数组索引为 `min(n - 1, floor(q * n))`。样本不足、closed-loop
coordinated omission、drop 等可信度信息随 summary 放在 `caveats`，不能只写在报告文案。

## 产物契约

独立 Harness runner 的一次运行默认落在 `runs/<experiment>/<run-id>/`。被 Doctor 这类上层诊断 Bundle
嵌入时，上层可以直接提供本次 run 目录；以下三件产物仍必须同目录，并使用相对路径互相引用：

```text
run.json          # 模型层：Run / Trial / Window / 聚合统计
outcomes.jsonl    # raw：每行一个请求事实，包含 case_id / arm(trial) / t / meta
verdict.json      # 跨 harness 判定出口，沿用 verdict-schema.yaml
```

- `run.json` 遵守 [`perf-run-schema.yaml`](perf-run-schema.yaml)。
- `outcomes.jsonl` 每一行遵守 [`perf-outcome-schema.yaml`](perf-outcome-schema.yaml)。
- 字段使用 snake_case，方便跨语言直接交换。
- reader 必须忽略未知字段；删除字段或改变既有字段语义时提升 `schema` 主版本。
- `phase_errors` 是可选的加法字段，因此 schema 仍为 3；旧 reader 可忽略，支持它的实现必须保留
  phase 与异常摘要，不能把执行异常降格为请求错误或 SLO fail。
- 实现私有数据只能放新增可选字段，不能改变共享字段含义。

## 一致性与 feature 覆盖

跨语言一致性要求的是契约，而不是代码逐行翻译或 feature 同步发布。每个实现应在自己的 README
声明支持的 load model、Probe 和 renderer；对已支持 feature 产出的公共 IR 必须通过 `conformance/perf`
fixture 校验。公共 fixture 是契约的可执行样例：各实现都读取它，不能由某个实现的临时输出反向定义
契约。新增共享名词先修改本契约与 schema，再分别实现。
