# perf_harness（资源约束下的容量与资源画像）

## 项目定位与边界

不测对错（`e2e_harness`）、不测质量（`eval_harness`），测**容量与资源画像**：把服务放到某个资源档下、施加压力、观察它随时间的表现，回答"在 xx 资源 + xx qps 下扛得住多少、cpu/内存/错误率如何"。**自给自足**，不 import 另两个 SDK；k8s 只活在 `deploy.py`（HelmDeployer）+ `observe/k8s.py` 两处。

本目录是 Perf Harness 跨语言契约的 Python 实现，不是其它语言实现的 canonical source。共享名词、
调度语义、对齐键和落盘字段以 `../../../spec/perf-contract.md` 及对应 schema 为准。

### 脊柱

```
Experiment 把 ResourceProfile × LoadProfile 解析为命名 Arm
每个 Arm 执行一次 Trial；Stage 规划负载，Window 统一切请求与资源事实
report / SLO / analyze 都按 (arm_id, window_id, <family>{labels}.<stat>) 查询
```

## 代码地图与核心模块

顶层目录顺序即数据流（model → drive → observe → metric → 消费者）：

```
perf_harness/
├── cli.py · config.py · sh.py   # 入口与装配；config 解析 + fail-fast 校验
├── model.py        # 名词层（纯数据）：Environment/Service/Arm/TrialRecord/Window/Outcome/Verdict/Run/TrialStop
├── deploy.py       # 可选部署执行器；HelmDeployer 是唯一碰 helm 处
├── engine.py       # 纯编排：扫网格，每格 apply → drive → observe → reduce
├── drive/          # 怎么压（扩展点①）
│   ├── load.py     #   LoadProfile：model(open/closed) × Schedule(强度随时间) × Pacing
│   ├── workload.py #   TrialContext/FireContext + fire/judge 协议适配与注册
│   └── scheduler.py#   驱动循环：开/闭环 + 熔断 DECIDE + drain/cancel ENACT → TrialStop
├── observe/        # 看什么（扩展点②）；FamilySpec 单表声明 metric 元数据
│   ├── base.py     #   Probe ABC + client/Prombed 探针 + observe_loop（采样循环）
│   └── k8s.py      #   top/rss/restart/limits（kubectl 系探针，纯函数解析器可单测）
├── metric/         # 收腰：唯一的那张表
│   ├── family.py   #   纯模型：MetricFamily(side/value_kind)+summary union+Missing+寻址
│   ├── store.py    #   MetricStore：消费方唯一读面（query/pivot/rows）
│   └── reduce.py   #   铸币点：outcomes/series → typed summary + caveats
├── slo.py · verdict.py   # run 级门（三态）；跨 harness verdict.json 出口
├── runio.py        # raw/model 两层落盘 + load_run（离线重建）
├── report/         # 视图层：render（md/html/csv）+ palette（配色=显示策略，不进模型）
└── analysis/       # 四个确定性透镜（capacity/resource/latency/validity）→ Observation
```

## 关键约定

### 脊柱（动它先想清楚）

- **metric 是收腰**：producer 产 metric、分析/报告读 metric，只经 `MetricStore` 按 `<family>{labels}.<stat>` 寻址；service / facet 是实体 label，时间只由 Window 选择，不能再伪装成 metric label。模型细节见 `docs/metric-model.md`。
- **Stage ≠ Window**：Stage 是计划的负载控制段；Window 是实际观测边界，请求按发射时刻归窗，request/resource 使用同一 start/end。重复 stage 名仍有不同 `window_id`。
- **加压是 x 轴不是 metric**：响应面 `metric = f(Arm, Window, slice)`；Arm 是命名配置，Trial 是该 Arm 的一次真实执行。

### 扩展点（业务接入只碰这两个）

- **Case 资产归 spec-case**：实验用 `caseset: <path>` 引用 canonical CaseSet，`cases:` 只做 case 选择/排序与实验本地 `weight`；`input/facets/judge/binding` 不在 perf 配置里覆盖。无 `caseset` 的内联 cases 仍是轻量实验入口。
- **Kernel 对齐**：request Outcome、Probe sample 及其 window-aligned 归约事实是 Observation；request、window、run 是不同 Unit grain，必须进入不同 Dataset / Worksheet，不能混成一张表。raw/model Run facts 构成可离线重算 Dataset，本次选择的 Workload judge、SLO 和 analysis 组件直接定义评估侧重点；换 SLO 或分析透镜不得重新发压，详见 [`../../../docs/kernel.md`](../../../docs/kernel.md#dataset-与反复评估)。
- **`Workload`**（怎么压 + 怎么判）：`TrialContext` 是整个 Trial 共用的不可变输入，单次并发请求通过 `FireContext(trial, case)` 组合请求 Case；`fire` 只记原始观测，`judge(outcome)→Verdict` 才裁决（纯函数，可离线重判）。各服务在自己项目写、`register_workload` 注册；Trial 固定按 `setup → measurement → deactivation → cooldown → cleanup` 运行，其中业务 hook 只有 `setup/deactivate/cleanup`。
- **`Probe`**（看什么）：`families` 单表声明元数据（FamilySpec：unit/value_kind/description，describe/summarize/Engine 共读），`sample()` 周期采样；Source 不绑 k8s，consumer 可通过 extension module + `register_probe` 扩展。Prometheus 来源由 `PrometheusProbe` 内嵌 Prombed，配置直接声明 PromQL 与输出 label 契约，不在 perf 内重复实现解析、存储和查询语义。
- **压后观测不污染容量口径**：`cooldown_s` 延长 raw series 以观察回收/缩容；默认 SLO 只读 measurement Window，`window: {kind: cooldown}` 显式读取 cooldown。

### 判定与可信度语义（细节见 docs/result-semantics.md + metric-model.md §3.6-3.8）

- 三层 verdict：per-request `judge` → per-slice `RequestStats` → per-run SLO（三态，skip ≠ pass、不计 capacity；typo 在解析期就死，进不了 skip）。
- 两类停止不合并：within-trial 错误率熔断（实时保护被压服务）vs between-trial `abort_on_fail` run 门；每个 Trial 以结构化 `TrialStop` 收尾，capacity 只读取 complete hold Window，已完成的低档 hold 不因后续提前停止而失效。
- 只有完成的请求才是延迟事实：drop（未发出）与 cancel（在途被切）绝不进延迟直方图——防 coordinated omission；caveat（co_biased/high_drop/…）随值走。
- 观测面 ⟂ 判定面：Probe/PromQL 结果默认只观测；影响成败的唯一通道是显式 SLO 引用。

### 产物与运行

- `Experiment` 扩展 common 的具名验证意图；`Run` 扩展 common `ExperimentRun`，`TrialRecord` 是 common `Execution` 的 perf 子类，每次真实请求记录为 `OperationRun → Outcome`。`PerfReducer` 只读这些事实并把 Artifact 落到 `runs/<experiment>/<run_id>/`，累积不覆盖。落盘三层 raw（outcomes.jsonl/timeseries.csv）→ 模型（run.json，schema 版本化，`load_run` 离线重建）→ 视图（report/CSV，纯下游）。
- `verdict.json` 是跨 harness 契约（spec/verdict-schema.yaml，devloop 消费），改动需五家对齐。
- 请求侧指标走客户端 Outcome（没 `/metrics` 的服务也能压）；`/metrics` 只喂资源侧。

## References

- 跨语言契约：[`../../../spec/perf-contract.md`](../../../spec/perf-contract.md)
- 落盘 schema：[`../../../spec/perf-run-schema.yaml`](../../../spec/perf-run-schema.yaml) / [`../../../spec/perf-outcome-schema.yaml`](../../../spec/perf-outcome-schema.yaml)
- 使用指南（user 视角）：[`README.md`](README.md)
- metric 模型（含 otel-collector 对照）：[`docs/metric-model.md`](docs/metric-model.md)
- 结果/SLO 语义：[`docs/result-semantics.md`](docs/result-semantics.md)
- 加压模型细节：[`docs/load-model-redesign.md`](docs/load-model-redesign.md)
- consumer 扩展与 trial 生命周期：[`docs/extensions.md`](docs/extensions.md)
