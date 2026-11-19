# 执行与证据生成

公共层统一执行后的事实与产物；领域 Engine 拥有调度、调用和生命周期。
执行消费稳定的 [Case](case.md) 与选定的 [运行目标](runtime.md)，形成可追溯的 Outcome。
已有 trace / trajectory 可直接采集为 Observation；事实的复评见 [评估](evaluation.md)，产物与呈现见 [报告](report.md)。

## 执行对象

| 概念 | 语义 |
|---|---|
| **Experiment** | 一份具名、可复现的验证意图；公共层只拥有稳定名称，各 Harness 子类增加 Case、Arm、Workload、Metric 或 Policy 等领域配置。 |
| **ExperimentRun** | Experiment 的一次真实执行，以 experiment name、`run_id` 和创建时间标识；领域实现可以简称为 `Run`。 |
| **Execution** | ExperimentRun 内由领域定义的组织单元；E2E 的 CaseRun 与 perf 的 ArmRun 都是 Execution。公共层不规定调度器或 runner 的调用协议。 |
| **OperationRun** | 对某个 Service 的 Operation 的一次真实调用；直接拥有这次调用产生的 Outcome。 |
| **Outcome** | OperationRun 产生的原始领域证据。公共层只统一语义角色，具体字段由领域子类定义。 |
| **Reducer** | 只读取 ExperimentRun 已记录事实并产出 Artifact 的领域归约器；不得再次调用被测 Service。 |
| **Artifact** | ExperimentRun 产生的一份具名、可持久化产物；内容和 schema 归领域所有，公共层只记录逻辑名称与 run 目录内相对路径。 |

## Experiment 执行骨架

```text
ExperimentRun
  └─ Execution × N
       └─ OperationRun × N
            └─ Outcome

Reducer.reduce(ExperimentRun) → Artifact × N → Report / Verdict
```

OperationRun 是一次真实服务调用的基本单位。ExperimentRun 通过领域自己的 Execution 组织这些调用，
而不直接管理所有调用细节：E2E 可以按 CaseRun 分组，perf 的 ArmRun 则组织固定 Arm、目标 Service/Workload
和一批 Case 在一次加压过程中产生的 N 次调用，N 由加压器决定。简单 Case 的一次触发对应一个 OperationRun；
多步骤 Case 可以产生多个 OperationRun。

公共层统一的是执行后的事实，不统一执行机制：领域 Engine 可以使用 runner、scheduler、队列或其它方式
组织 OperationRun。因而 common 不提供 `Driver.run` / `DriverRun`。Outcome 与 Artifact 也不是一一对应：
一个 Artifact 可以汇总大量 Outcome，同一批 Outcome 也可被多个 Reducer 投影成不同 Artifact。公共 Artifact
不枚举 Outcome ID；需要精确 provenance 时，由领域使用 Execution identity、selector 或时间窗口表达。

## Operation 与 Case

Operation 静态描述 Service 提供的 API 或能力及其关系；Case 描述具体输入与验证场景。
多个 Case 可以针对同一个 Operation，例如：

```text
Operation: chat/chat
  ├─ Case greeting: query="你好"
  └─ Case python:   query="用 Python 计算下 1+1"
```

两个 Case 的入口能力都是 Chat API。第二个 Case 可能触发 Python 工具；静态 `mayCalls`
只声明这种可能性，不能证明某次执行发生了该调用。实际调用属于运行证据。
`calls`、`mayCalls` 和 `observes` 随 Operation 声明，输入变化不产生新的 Operation identity。

Case 的执行绑定由消费方提供。Doctor 的 `ServiceCaseSource.operation` 静态引用这一组 Case
共同针对的 Operation，runner 留在 Case source，负责执行准备和单次调用。
运行记录同时保留 Case identity 与 Operation identity；通用 Case 格式不强制一对一绑定，
多步骤 Case 仍可产生多个 OperationRun。

## 环境与 Case 的两层生命周期

Python 的 FixtureContext 在 EnvironmentContext 上增加 phase，供 prepare/cleanup 使用；
普通环境操作只依赖 EnvironmentContext，不需要知道编排阶段。

EnvironmentContext 不预设运行平台。对本地 Web 服务，HostEnvironment 配合 local Host 表达
运行位置；项目 Fixture 启动进程并等待就绪，领域执行回调运行 E2E，Fixture 最后停止自己启动的进程。
ClientManager 仅释放访问该服务的 HTTP 客户端等资源，不因连接释放而停止被测服务。
因此本地进程、远端主机和 Kubernetes 可以复用执行上下文与编排契约，不要求统一的平台 Client 基类。

Python `harness_common.access.fixture` 提供 `EnvironmentFixture` 和 `run_environment`。
项目先分配自己的 state，再实现异步 `prepare(ctx, state)` 与 `cleanup(ctx, state)`；
state 保存资源句柄、已采集事实和借用的 Client。准备过程中取得的句柄立即放入 state，
让部分准备失败时的清理仍能找到资源。ctx 提供所选 Environment、客户端借用视图与阶段剩余时间。

一次执行的顺序为：

```text
environment prepare → 前提校验与证据 → Harness 执行（含 Case 清理）
                    → environment cleanup → ClientManager dispose
```

环境共享层只调用一个领域执行回调，不识别 Case。E2E 的 CasePlan / Go Definition、perf 的
ArmRun setup / deactivate / cleanup 继续决定用例的准备、清理和时机。回调必须等待自己的
任务与清理全部结束，才能归还环境；共享环境资源由 EnvironmentFixture 持有。

各阶段有独立预算。prepare 失败、前提不满足或未知会阻止领域执行，仍运行环境清理；
领域错误与环境清理错误分别保存在 `EnvironmentRun.phases`。已有领域结果保留在 `result`，
环境执行的 `healthy` 仅反映外层健康，最终运行结论还需要结合领域 Verdict。
取消会在领域退出、环境清理和客户端释放完成后继续向上传播。异步回调须配合取消；
ClientManager 的最终释放采用协作式预算，等待释放完成并将超时记为错误，避免遗留后台释放任务。

`observe(state)` 只读取项目已采集的事实；入口在准备结束时深拷贝快照，再检查可选的 target
必需条件。清理后的观察独立追加，不能覆盖准备证据。项目负责把外层阶段与观察连同领域产物
持久化，不把环境失败伪装成一个 Case。环境身份与访问凭据仍和证据分开。

下面展示接入结构，资源创建与领域执行由项目实现：

```python
from harness_common import EnvironmentBudgets, run_environment

# state 在调用前分配，fixture 实现 prepare / cleanup。
execution = await run_environment(
    environment, state, fixture, run_harness,
    budgets=EnvironmentBudgets(prepare_s=60, run_s=300, cleanup_s=60, dispose_s=15),
    observe=lambda state: state.snapshot,
    required={"ptrace": "denied"},
)
# 保存 execution.phases / observations，并结合 execution.result 的领域结论报告。
```

## CaseRun

E2E 通常在 CaseRun 内立即应用 assertion 并写 Verdict，因此 Dataset build 与 EvaluationRun 可以落在
同一 Run 目录；语义上仍要保留原始 Outcome、Case/variant identity 和判断结果的边界。已有 Outcome Unit
应能使用另一版 assertion engine 或 soft metric 离线复判，而不重新调用被测系统。

Unit identity 可以在 Outcome 产生前由 `case_id + variant` 确定。prepare、execute 或 judge 失败时，
对应 Unit 仍应保留失败状态和阶段证据，不能为了得到“干净 Dataset”而静默丢弃。

| owner | 持有 | 不持有 |
|---|---|---|
| spec-case Case | input、facets、requires、`judge.<face>`、binding | base URL、凭据、weight/concurrency、prepare/cleanup 代码 |
| CaseRun | phase 顺序、typed state、variant、deadline、cleanup、phase evidence | canonical asset schema、服务专用协议字段 |
| Runner | 请求协议适配、Outcome | 业务判定与资源生命周期 |
| Judge | Outcome/观测事实到 pass/fail | 远端资源操作 |
| Verdict | 机器可消费的结论、对齐键、最小 metrics/artifact 指针 | 完整日志和富产物副本 |

cleanup 总会执行，并拥有不继承 execute 取消状态的独立预算。cleanup 失败表示执行不可信，结论是 `error`，不能作为 best-effort warning 被隐藏。业务上的“等待 GC/同步完成”属于 execute/judge 的 temporal assertion；测试卫生层面的删除资源属于 cleanup。

## Variant 与阶段预算

variant matrix 是同一 case 的运行展开，不复制 CaseRef。variant 的稳定 id 进入 `arm_id`，键值同时进入 facets，便于 Verdict 对齐和报告 pivot。

长时间 case 使用阶段 deadline 感知的 poll/retry/consistently：

- poll：等待条件最终成立；
- retry：只重试调用方明确分类的瞬态错误；
- consistently：要求条件在观察窗内持续成立；
- 不用固定 sleep 表达系统收敛。

Python 同步 step 通过 `PhaseContext.remaining_s` 协作传递 deadline，并在返回后检查超时；Go step 直接接收 `context.Context`。语义和 Verdict 一致，API 保持语言习惯。

## E2E 的环境前提

项目可以按环境组织配置目录，同时让同一用例函数接收环境参数：目录负责环境选择，
函数复用行为契约。二者不需要各复制一套用例。

项目 fixture 准备目标、收集实际环境与组件版本；E2E 执行入口检查必需条件，再运行
execute / judge，最终执行 cleanup。准备失败、必需条件未知或不满足、清理失败均为
error；产品行为不满足断言为 fail。缺失权限可以是被测条件，但不能自动算作跳过或通过。

Go 使用 `e2e/environment.Run`，Python 使用 `run_in_environment`，复用 CaseRun
已有的阶段预算和清理机制。Environment / profile 写入 variant，运行快照独立记录
runner、可选 Host 与 target 的实测值、来源和采集时间。快照跟随 CaseRun 输出到
`environments.json`，不包含 SSH 地址、kubeconfig 内容或连接凭据。
其中 `environment` 固定为准备阶段结束时的快照，`cleanup_environment` 保存清理后的事实；
准备失败也保留已收集事实，后续 state 变更不能改写历史。

Profile 是项目拥有的条件组合，例如限制 ptrace、使用某个 AppArmor 配置。
profile 名和部署声明不能代替真实探测；要求拒绝 ptrace 的测试必须证明拒绝发生在
目标执行上下文，再验证应用降级行为。

## 执行状态与质量判断

阶段失败、取消和清理结果属于执行事实，领域判断依据已获得的证据给出。运行完成不自动表示行为达标，
环境外层的 `healthy` 也不能替代领域结果。对缺失证据保留 error / unavailable 等状态，避免把未知表示为成功。

Verdict 是否存在及其判定标准由领域契约决定。Perf 当前报告执行状态与请求观测，业务判断单独保存；
具体见 [Perf 契约](../spec/perf-contract.md)。机器输出与人类报告见 [报告](report.md)。
