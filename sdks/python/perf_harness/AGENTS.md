# perf_harness（资源约束下的容量与资源画像）

## 项目定位与边界

不测对错（`e2e_harness`）、不测质量（`eval_harness`），测**容量与资源画像**：把服务放到某个资源档下、施加压力、观察它随时间的表现，回答"在 xx 资源 + xx qps 下扛得住多少、cpu/内存/错误率如何"。**自给自足**，不 import 另两个 SDK；Kubernetes I/O 由 toolbox 承担，部署、观测和请求通道在对应适配层装配。

本目录是 Perf Harness 跨语言契约的 Python 实现，不是其它语言实现的 canonical source。共享名词、
调度语义、对齐键和落盘字段以 `../../../spec/perf-contract.md` 及对应 schema 为准。

### 脊柱

```
Experiment 把 ResourceProfile × LoadPlan 解析为命名 Arm
每个 Arm 执行一次 ArmRun；Stage 规划负载，Window 统一切请求与资源事实
report / analyze 都按 (arm_id, window_id, <family>{labels}.<stat>) 查询
```

## 代码地图与核心模块

顶层目录顺序即数据流（model → drive → observe → metric → 消费者）：

```
perf_harness/
├── cli.py · config.py · sh.py   # 入口与装配；config 解析 + fail-fast 校验
├── model.py        # 名词层（纯数据）：Environment/Service/Arm/ArmRun/Window/Outcome/Run/ArmStop
├── deploy.py       # 可选部署执行器；HelmDeployer 是唯一碰 helm 处
├── engine.py       # 纯编排：扫网格，每格 apply → drive → observe → reduce
├── drive/          # 怎么压（扩展点①）
│   ├── connection.py # 显式请求 Transport 配置；Engine 仅借用 HTTP client
│   ├── load.py     #   LoadPlan：request_rate × max_inflight + 显式 Stage
│   ├── runner.py #   ArmContext/FireContext + fire 协议适配与注册；judge.py 独立判定
│   └── scheduler.py#   驱动循环：节奏与在途反馈 + 熔断 + cooldown/cancel → ArmStop
├── observe/        # 看什么（扩展点②）；FamilySpec 单表声明 metric 元数据
│   ├── base.py     #   Probe ABC + client 指标 + observe_loop（采样循环）
│   ├── history.py  #   压测后历史查询、证据投影与独立补采；configuration.py 装配配置
│   ├── prometheus.py # PromQL 描述与结果投影，供远端历史查询使用
│   ├── prometheus_query.py # 远端 Prometheus 结果到统一指标的映射
│   └── k8s.py      #   top/rss 的 kubectl 采样与 restart/limits/pods 的原生 API 采样
├── metric/         # 收腰：唯一的那张表
│   ├── family.py   #   纯模型：MetricFamily(side/value_kind)+summary union+Missing+寻址
│   ├── store.py    #   MetricStore：消费方唯一读面（query/pivot/rows）
│   └── reduce.py   #   铸币点：outcomes/series → typed summary + caveats
├── verdict.py     # 执行诊断到跨 harness artifact 的投影
├── runio.py        # raw/model 两层落盘 + load_run（离线重建）
├── report/         # 视图层：render（md/html/csv）+ palette（配色=显示策略，不进模型）
├── comparison.py   # 分析 / 容量 / 报告共用的扫描轴与固定条件分组
└── analysis/       # 四个确定性透镜（capacity/resource/latency/validity）→ AnalysisNote
```

## 关键约定

### 脊柱（动它先想清楚）

- **metric 是收腰**：producer 产 metric、分析/报告读 metric，只经 `MetricStore` 按 `<family>{labels}.<stat>` 寻址；service / facet 是实体 label，时间只由 Window 选择，不能再伪装成 metric label。模型细节见 `docs/metric-model.md`。
- **Stage ≠ Window**：Stage 是计划的负载控制段；Window 是实际观测边界，延迟按发射时刻归窗，吞吐按真实事件时刻计数，request/resource 使用同一 start/end。重复 stage 名仍有不同 `window_id`。
- **比较先固定条件**：有限速率扫 request_rate，饱和发压扫 max_inflight；另一轴与调度形态等条件不同就分组，分析 / 容量 / 报告共用 comparison。
- **加压是 x 轴不是 metric**：响应面 `metric = f(Arm, Window, slice)`；Arm 是命名配置，ArmRun 是该 Arm 的一次真实执行。

### 扩展点（业务接入只碰这两个）

- **Case 资产归 spec-case**：实验用 `caseset: <path>` 引用 canonical CaseSet，`cases:` 只做 case 选择/排序与实验本地 `weight`；`input/facets/judge/binding` 不在 perf 配置里覆盖。无 `caseset` 的内联 cases 仍是轻量实验入口。
- **证据与复评**：request Outcome、Probe sample 及其 window-aligned 归约事实是 Observation；request、window、run 是不同 Unit grain，必须区分分析粒度，不能混成一张表。raw/model Run facts 构成可离线重算 Dataset，本次选择的 独立 Judge 和 analysis 组件直接定义评估侧重点；换 Judge 或分析透镜不得重新发压，详见 [`../../../docs/evaluation-data.md`](../../../docs/evaluation-data.md#dataset-与反复评估)。
- **`Runner`**（协议适配）：`ArmContext` 是整个 ArmRun 共用的不可变输入，单次并发请求通过 `FireContext(arm_run, case)` 组合请求 Case；`fire` 只记原始观测，独立 `Judge(outcome)→RequestEvaluation` 才裁决（纯函数，可离线重判）。各服务在自己项目写、`register_runner` 注册；ArmRun 固定按 `setup → warmup → hold → cooldown → cleanup` 运行，其中业务 hook 只有 `setup/deactivate/cleanup`。
- **`Probe`**（看什么）：`families` 声明指标元数据；资源 Probe 周期采样，PrometheusQueryProbe 只声明压测后查询。实验顶层 `prometheus` 通过 toolbox PrometheusDataSource 访问已有历史，不自行抓取 `/metrics`。Query 时间与原始结果保存到 Run；补采不发压，报告不访问远端。详细配置和原因见 docs/service-metrics.md。
- **压后观测不污染容量口径**：`cooldown_s` 延长 raw series 以观察回收/缩容；默认汇总只读 measurement Window，`window: {kind: cooldown}` 显式读取 cooldown。

### 判定与可信度语义（细节见 docs/result-semantics.md + metric-model.md §3.6-3.8）

- 请求 Outcome 与独立 Judge 结果分开保存，RequestStats 归约事实，不提供 SLO 配置或业务验收。
- Run.status 只表示 completed / stopped / error；错误率熔断、超时、取消、排空仍是执行保护。
- 吞吐按完成事件分窗，code 是开放的非空字符串，仅 success 为成功；未判定独立于 code；中断与丢弃单列。
- 无已判定请求的错误率是 Missing；未判定不计为服务错误。缺失与 Probe 故障不能伪装为零。
- 下游 API 指标通过 Probe.operation 绑定 Common Operation.id()，采集不依赖 Graph。
  HttpApiProbe 按服务、method、路由模板生成查询；Run 保存 API 指标地址与 graph/case 快照。
  Call Graph 是按 Window 从 MetricStore 读取的 Operation 图视图，Service 仅作辅助信息。
  细节与分析指导见 [HTTP 指标与 Call Graph](docs/callgraph.md)。
- 既有分析透镜与事实记录分层；本层不新增瓶颈、容量或“压不上去”的自动结论。

- 请求通道通过 `Experiment.request_transport` 显式注入加压 HTTP pool，Runner 的 setup/fire/deactivate/cleanup 共用；观测客户端独立。通道初始化失败记录为 setup phase error，不发出业务请求。细节见 [请求通道](docs/request-transport.md)。

### 产物与运行

- `Experiment` 扩展 common 的具名验证意图；`Run` 扩展 common `ExperimentRun`，`ArmRun` 是 common `Execution` 的 perf 子类，每次真实请求记录为 `OperationRun → Outcome`。`PerfReducer` 只读这些事实并把 Artifact 落到 `runs/<experiment>/<run_id>/`，累积不覆盖。落盘三层 raw（requests.jsonl / evaluations.json/timeseries.csv）→ 模型（run.json，schema 7 版本化，`load_run` 离线重建）→ 视图（report/CSV，纯下游）。
- `verdict.json` 是跨 harness 契约（spec/verdict-schema.yaml，devloop 消费），改动需五家对齐。
- 请求侧指标走客户端 Outcome；外部 Prometheus 指标归资源侧，缺失与错误不能伪造为零。

## 开发与测试

从 `sdks/python` 运行无需外部服务或集群的 smoke 实验：

```bash
uv run python -m perf_harness.cli run perf_harness/examples/mock.yaml --out /tmp/ph
```

共享 CaseSet 的选择语义由仓库根目录 `conformance/case/` fixture 验证。

## References

- 跨语言契约：[`../../../spec/perf-contract.md`](../../../spec/perf-contract.md)
- 落盘 schema：[`../../../spec/perf-run-schema.yaml`](../../../spec/perf-run-schema.yaml) / [`../../../spec/perf-request-schema.yaml`](../../../spec/perf-request-schema.yaml)
- metric 模型（含 otel-collector 对照）：[`docs/metric-model.md`](docs/metric-model.md)
- 结果语义：[`docs/result-semantics.md`](docs/result-semantics.md)
- 加压模型细节：[`docs/load-model-redesign.md`](docs/load-model-redesign.md)
- consumer 扩展与 arm_run 生命周期：[`docs/extensions.md`](docs/extensions.md)

- 服务指标与汇总列：[`docs/service-metrics.md`](docs/service-metrics.md)
