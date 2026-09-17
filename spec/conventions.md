# e2e-harness 约定

## Case 规范（canonical case）

case 是**可积累、可分享**的资产：同一份 case，e2e 看对错、eval 看效果、perf 看压力下表现，一次执行多面观测；而且能**把它给别人、或拿别人的 `case.yaml` 来驱动你自己的 e2e/eval/perf**（像一份 conformance / benchmark 语料）。canonical Case/CaseSet、load/validate/case_hash 与 binding 契约归 [`spec-case`](https://github.com/compforge/spec-case)；本仓 [`case-schema.yaml`](case-schema.yaml) 是运行时兼容投影，输出契约见 [`verdict-schema.yaml`](verdict-schema.yaml)。

1. **id 不可变、集内唯一**。id 是跨 run / 跨 harness 对齐结果的主键（输入 `case.id` == 输出 `verdict.case_id`）；语义变了换新 id，不复用旧 id。意图漂移检测用 spec-case `case_hash`。
2. **case 无环境**。不写 base_url / token / 模型名——环境归 experiment config。同一 case 必须能打到任意环境/SUT。
3. **case 无实验参数**。混流权重、并发、重复次数归 experiment；case 只描述单次请求长什么样。
4. **判定按面分区（`judge.e2e/eval/perf`）且全部可选**。缺某面判定 = 该面只观测不判定（"观测面 ⟂ 判定面"）。公共段（id/input/facets/requires）演进走 spec/，判定段字段各 harness 自治。
5. **facets 必须有词表 schema**。约束 values + ordered + `open` 逃生舱；自由字符串列会腐烂。
6. **case 声明依赖，不执行准备**。case 用 `requires` 按名引用素材；素材本体（`sources`）与 case 同文件积累、进 git；provision（上传/建库）、复用（key = 素材内容 hash）、清理（复用制下退化为按 key 的 GC）都是 experiment 运行时的事。
7. **case 是 git 里的平文件**。yaml 进消费方仓库（`cases/<domain>/*.yaml`），可 diff、可 round-trip；不进 DB。框架仓库只放 schema + mock case。
8. **唯一 canonical Case，各家行为直接消费**。Case 模型由 spec-case 持有；三面（含 e2e）的行为各自直接读它——runner/solver/workload 读 `input`、报告读 `facets`、各面读自己那段 `judge.<面>`——不存在 per-harness Case 类。

> e2e 的 `@case` / `+case` 注解是 canonical case 的 **co-located 编写前端**：Python casegen 将 NL intent 编译为结构化 CaseSet；Go casegen 静态检查每个 marker 都有唯一 `caserun.Ref(caseset, case_id)`。编写位置不同，运行身份统一为 CaseSet + case id。

## 核心概念

| 概念 | 职责 |
|------|------|
| **Case** | 声明测试意图：输入 + 期望 |
| **Runner** | 协议适配：将 input 变成请求，收集响应 → Outcome |
| **Outcome** | Runner 和 Judge 之间的标准化契约 |
| **Judge** | 对 Outcome 做判定：硬断言（assert）或软评分（metric） |
| **CaseRun** | 一条 case 的运行生命周期、阶段预算和执行证据 |

## 生命周期

所有 case 遵循四阶段：

```
prepare → execute → judge → cleanup
```

- `prepare`：创建前置资源（可选）
- `execute`：Runner 发请求收响应，产出 Outcome；也可承载多步操作
- `judge`：Assert（硬 pass/fail）和/或 Metric（软评分 0~1）
- `cleanup`：总会执行并使用独立预算；失败记为 error，不能把资源泄漏隐藏成 pass

每个阶段都记录状态和耗时。长时间等待放在 execute/judge 内，使用阶段 context/deadline 的 poll、retry、consistently；不要用固定 sleep。Case 是稳定资产，不承载 prepare/cleanup 过程代码。

## Outcome 结构

Runner 输出、Judge 输入的统一中间表示：

```yaml
status_code: int
body: dict | null          # JSON 解析后
headers: dict
duration_ms: int
metadata: dict             # runner 特有的提取物（trace_id, message_id 等）
raw: bytes                 # 原始响应
```

Outcome 可序列化，支持阶段可恢复（保存 outcome → 重跑 judge 不重跑 runner）。

## 文件命名

| 语言 | 测试文件 | 隔离机制 |
|------|---------|---------|
| Go | `*_e2e_test.go` | `//go:build e2e` |
| Python | `test_*_e2e.py` | `@pytest.mark.e2e` 或 conftest 注册 |

## 目录结构（服务接入侧）

测试目录 mirror 服务 API 结构：

```
<service>/tests/e2e/
├── config.yaml            # 服务级配置
├── cases/                 # YAML case 定义（可选）
├── <domain>/
│   ├── test_xxx_e2e.py    # Python
│   └── xxx_e2e_test.go    # Go
└── conftest.py            # Python fixtures
```

## Config 格式

```yaml
service:
  name: <service-name>
  component:
    repository:
      forge: <forge-name>
      path: <repository-path>
    name: <component-name>
  environment:
    name: <environment-name>
  base_url: ${ENV_VAR:-default}
  headers:
    Authorization: Bearer ${API_TOKEN}
    X-Tenant-ID: ${TENANT_ID}

runtime:
  http_timeout_s: 120
  poll_interval_ms: 500
  poll_timeout_s: 60

profile: ${ENV_PROFILE:-full}

custom:                    # 服务自定义扩展区，框架透传
  key: value
```

插值规则：
- `${VAR}` — 必须存在
- `${VAR:-default}` — 缺失时用 default

## Runner 类型

| Runner | 协议 | sync/async | 状态 |
|--------|------|-----------|------|
| `JSONRunner` | HTTP JSON | sync | 已实现 |
| `AsyncJSONRunner` | HTTP JSON | async | 已实现 |
| `SSERunner` | HTTP SSE | sync | 已实现 |
| `AsyncSSERunner` | HTTP SSE | async | 已实现 |

所有 runner 产出相同的 Outcome 结构（SSE events 进 `metadata['events']`），judge 层无需关心协议差异。

## Judge 类型

| 类型 | 适用 | 输入 | 输出 |
|------|------|------|------|
| 结构化 e2e assertion / Go `judge.Assertion` | 硬断言 | Outcome | pass / fail reason |
| `OutcomeMetric` 子类 | 软评分（Outcome） | Outcome | MetricResult (0~1) |
| `BaseLLMJudge` 子类 | LLM-as-judge | EvalSample | MetricResult (async) |
| 同步 metric 模块 | 关键词 / 模板匹配 | EvalSample | MetricResult |

四类都收敛到 `MetricResult(name, score, judgement)`。Assert 和 Metric 可在同一 case 并存：assert 全过 + metric 全达标 → case pass。

## 使用模式（五类 Harness）

五类 Harness 都映射到同一条 Kernel 数据主线：

```text
Case → Observation → Unit → versioned Dataset
Dataset + Detectors / Evaluators / Measurers / Policy → EvaluationRun / Worksheet → Verdict / Report
```

Dataset 保存可复用的 Case、Observation 和 Annotation facts；Worksheet 保存某次评估追加的
Finding、Evaluation 与 Measurement。实现可以融合执行、填表和 checkpoint，但必须保留 Dataset facts 与本次运行结果的边界，以便
在不重新执行 Case 的情况下换规则、Judge、SLO 或分析侧重点。

- **e2e（API 测试）**: canonical CaseSet + CaseRun 四阶段 + sync/async runner + 结构化 assertion / OutcomeMetric。
- **eval（效果测试）**: `EvalEngine` 驱动大表 + reconciler 填表，per-case async runner + builder + metric set。
- **perf（压力测试）**: `Engine` 把资源档 × 负载档解析为 Arm，逐 ArmRun 采样并按 Window 聚合，出容量与资源画像。
- **trace（链路分析）**: raw span 经 assemble 形成 Node Observation / Unit Dataset，detector profile
  通过 detect 追加 Finding，再按 node、trace 或 cohort grain 生成 Worksheet 与报告。
- **trajectory（轨迹分析）**: Dataset Builder 将 Case、Trajectory Observation 与 annotation 固化为
  版本化 Unit Dataset；Measurements 从 Trajectory 确定性派生，二者共同输入 Detector / Verifier。
  Detector 与 Verifier 均可面向 cost / effect，也均可使用 hard / soft 规则，并生成各自 AnalysisRun、Worksheet、Metric 与报告。

五者**互不 import、不抽领域公共依赖**（耦合成本 > 省下的重复）：e2e/eval 各自复制一小撮 L2 原语
（Outcome 形状 / runner / SSEParser / build_headers），perf 自带 httpx 发压栈。唯一例外是
业务无关的 `common` / `report_kit`。一致性落在 Kernel 语义、`spec/` 数据格式与 conformance，
不落在共享领域代码。

## ExperimentRun、Artifact 与 verdict 出口（跨 harness）

五类 harness 的 ExperimentRun 产物布局对齐到同一骨架，让消费方（devloop 等）"读 verdict 自纠偏"时
一个 glob + 一份 schema 全收：

```
runs/<scope>/<run-id>/
├── verdict.json        # 统一判定出口（必产）—— schema 见 verdict-schema.yaml
└── <富产物>            # 各 harness 自有：results.csv / run.json / junit.xml / report/ …
```

`Artifact` 是 ExperimentRun 产生的领域数据，公共契约只记录其逻辑名称和相对路径；raw、model、
checkpoint 等内容及 schema 由各 Harness 持有。`Report` 是一个或多个 Artifact 的下游渲染，不能
在渲染阶段重新执行 Experiment 或补造源事实。

- **scope**：harness 中立的 Experiment 名（run 目录第一段）。`e2e` 通常采用套件名，
  `eval`/`perf`/`trace`/`trajectory` 通常采用实验或数据集运行名；名字不要求 Experiment
  一定具有多个对照 Arm。
- **run-id**：scope 内一次 run 的唯一标识，派生各 harness 自治，但语义分两类：
  perf/e2e/trajectory 用**时间戳或周期 id**（每次 run 留一份历史，累积不覆盖）；eval 用 **`experiment_hash`**
  （同配置 rerun 落同一 run 目录、原地 resume——内容寻址的复用 namespace，而非历史快照）。
  两类都落 `runs/<scope>/<run-id>/`，消费方一律 glob `runs/**/verdict.json`。
  （eval 早期是扁平 `runs/<exp>/`，已对齐补上 run-id 层。）
- **verdict.json**：每个 run 目录必产一份；只承载"判定结论 + 对齐用最小结构"，富产物用
  `artifact_paths` 指过去，不复制内容。两类判定单元分开：per-case 判定走 `cases[]`（对齐键
  `case_id`，与「Case 规范」第 1 条同一主键），run 级断言门走 `checks[]`（perf SLO 是首个实例）。
  wire 只存这两份事实 + run rollup，**不存派生计数**——"多少 pass/fail"消费方读时自己 fold
  `cases[]`/`checks[]`（perf 无 per-case 行，结论落 `checks[]`）。字段见 [`verdict-schema.yaml`](verdict-schema.yaml)。

各 harness 的 verdict 投影落点（M1 实现，薄投影，不新建判定能力）：

| harness | verdict 投影落点 | 来源 |
|---------|------------------|------|
| e2e | `e2e_harness.engine` / Go `caserun.Recorder` | CaseRun 阶段结果 + 结构化 assertion |
| eval | `eval_harness/verdict.py`，`engine.run_experiment` 收尾调用 `write_verdict()` | `row_overall`（weighted）+ per-cell state |
| perf | `perf_harness/verdict.py`，`report.write_run` 收尾调用 `write_verdict()` | 每条 SLO check → `checks[]`；run status 按 per-check rollup，cooldown skip 失败关闭；**不取 `run.passed`** |
| trace | `trace_harness/verdict.py`，`corpus.experiment.run_experiment` 收尾调用 `write_verdict()` | experiment 显式声明的 `gates:`（对三表/算子结果的断言）→ `checks[]`；**Finding 是发现不是判定**、永不直接变 verdict；无 gates → `skipped`（同 perf 记录门原则） |
| trajectory | `trajectory_harness/verdict.py`，`TrajectoryHarness.run` 收尾写出 | Dataset/Runner 执行健康 + 领域显式 `TrajectoryVerdictPolicy` → `checks[]`；Finding 不自动成为 check；无 Policy → `skipped` |

> status 语义：e2e/perf 有 pass/fail/error/skipped；eval **无 fail**（质量不判对错）——status 表"执行是否完成"（pass/error/skipped），质量信号走 `score`（run 级 weighted overall）。trace/trajectory 的判定单元是 run 级 check 而非 case，分别断言遥测分析产物与轨迹聚合效果/成本指标；未声明 gate/policy 时只分析不判定。各家 error 优先于 fail：判不了 → 结果不可信 → 先暴露。
> perf 尤其要分清两个门：`run.passed` 是**运行门**（CLI 退出码 / `abort_on_fail` / `strict_slo` 宽松），默认放过普通 skip；`verdict.status` 是**记录门**，按 per-check 状态 rollup——SLO 声明了却全 skip（或没声明 SLO）→ `skipped` 而非 `pass`。cooldown 用于证明恢复，skip 在两个门都失败关闭。

## 对齐键（跨平面 join 的前提，各 harness 迭代必守）

一个执行波次（一批 case 主动驱动 / nightly）落下四个数据平面：verdict（对错）/ metrics
（延迟资源）/ traces（链路）/ findings（判读）。devloop 消费与数据挖掘（顶层 AGENTS.md
「愿景」）都靠对齐键把平面 join 起来——单平面数字不可行动（"fail 23 个"不知为何、
"churn 率 20%"不知伤不伤），join 后才出可行动结论（"fail 的 23 个里 18 个命中 tool_churn
→ 改该 tool 的 desc"）。四组键，**迭代时不得破坏，新增产物/列时优先携带**：

- **case_id**：输入 `case.id` == 输出 `verdict.case_id`（「Case 规范」第 1 条主键）——
  case ↔ 判定的对齐，也是跨 harness 按题对齐的主键。
- **arm_id**：Experiment 内命名配置的对齐键。一个 case 在多个 Arm 上执行时，`case_id`
  不变、`arm_id` 区分配置；perf 的 SLO check 同样携带 Arm。Arm 的配置形状由各 harness
  强类型定义，共享的是语义和键，不是一个通用配置袋。
- **trace_id**：黑盒产物 ↔ 遥测的桥。各 harness 的 run 产物应尽量记录——eval `results.csv`
  已有列、perf `Outcome.meta` 已记、e2e 可放 `Outcome.metadata`；trace 的 `sibling_run`
  source 靠它把坏 case / 慢 ArmRun join 到链路归因。
- **`runs/<scope>/<run-id>/` + verdict.json**：波次产物的定位骨架（上节），消费方按它
  glob 全收。

perf 在一个 ArmRun 内还用 `window_id` 对齐时间切片；它是 perf 模型内的局部键，不提升为所有
harness 都必须实现的运行层级。

对齐键断一处，跨平面结论就拼不出来；修改这三组键的形状属于 spec 级变更，需过本文件。
