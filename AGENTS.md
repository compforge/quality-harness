# quality-harness

## 项目定位与边界

面向软件与 Agent 的质量执行与证据分析仓库，覆盖 e2e、eval、perf、trace、trajectory。
共同模型连接 Repository 的代码描述、具体运行目标、测试资产、执行证据与验证结论；当前交付领域 SDK、
共享契约、基础设施工具箱和离线报告组件，支持主动测试与已有运行证据分析。

项目级 Quality Harness 的能力发现与执行决策仍是上层规划，见 [设计](docs/quality-harness.md)。
构建、发布、环境凭据、测试资产与交付门禁由相应消费方持有。

## 核心模型与主流程

```text
系统描述：Repository / Component / Product 与 Service / Operation 的关系
    ↓ 选择 revision 或 Service + Environment，保留目标身份与版本
测试意图：Spec / Case / Experiment 与项目拥有的验收标准
    ↓ 借用访问能力
主动执行 → Outcome ─────────────┐
已有 trace / trajectory → 采集 ─┴→ Observation
    → Unit facts / Dataset
    → EvaluationRun / Worksheet：Finding / Measurement / Evaluation
    → Artifact / Report / Verdict，保留来源、覆盖范围与未知项
```

这条主线按需要进入：代码组件可以直接验证而不部署，已有运行证据可以直接分析而不补造 Case，
同一批 Observation 可以反复评估而不重发请求。测试负责产生实际行为与证据，验证依据契约解释事实。

| 环节 | 核心关系与责任 | 改动归属 |
|---|---|---|
| 系统描述 | Repository 位于 Forge，包含 Component；Product 与 Component 可多对多；Service 是 Component 在 Environment 中的具名运行体现，对外提供 Operation | common `system`；产品目录与业务声明归消费方 |
| 运行目标 | Service 引用 Workload、管理或使用 Resource；Graph 声明拓扑，实例和当前状态由独立观测记录 | common 契约，toolbox 访问，消费方选择目标并保留覆盖范围 |
| 测试资产 | Spec 表达契约，Case 固定输入与判定数据；Experiment 组织本次验证意图与条件 | canonical Case 归 spec-case；项目持有资产、适配与验收标准 |
| 执行与采集 | ClientManager 复用和回收客户端；EnvironmentContext 传递环境、借用入口与预算；ExperimentRun 通过领域 Execution 组织 OperationRun / Outcome | common `access` / `execution`；toolbox 提供 I/O；领域 Engine 拥有调度和生命周期 |
| 证据与评估 | Observation 与适用的 Case / Annotation 组成 Unit；Dataset 固定 facts；EvaluationRun 记录本次组件配置并向 Worksheet 追加结果 | 领域拥有 grain、schema、规则、Judge 和聚合语义 |
| 结果交付 | Reducer 从已记录事实产生 Artifact；Report 呈现，Verdict 按领域策略投影机器结论 | 公共产物契约、领域归约与报告适配、共享 `report-kit` 浏览器组件 |

e2e 验证对外契约，eval 评估产出质量，perf 记录负载下的延迟、吞吐和资源，trace 分析调用链，
trajectory 分析 Agent 决策与行动。它们是不同质量视角，可复用同次运行的 response、Outcome、trace 或 trajectory。

同一 Dataset 的 Unit grain 必须一致；事实变化产生新版本，换规则、Judge 或模型产生新的 EvaluationRun。
Finding 是带证据的发现，Measurement 是中性度量，Evaluation 是依据契约的判断；是否生成 Verdict 由领域策略决定。
报告只消费已准备的数据，不访问远端、不重发请求、不重新执行 Judge。

Identity 由模型定义格式与唯一性范围，描述性元数据不参与身份。Service 完整身份包含 Component 与 Environment；
Graph / Run 的 Service、Operation 局部 ID 由环境 catalog 限定。静态声明、实例存在和健康结论分别表达。

## 代码地图与核心模块

```text
quality-harness/
├── VERSION              # 仓库整体版本，各 SDK 包版本独立管理
├── scripts/             # 仓库维护脚本
├── report-kit/          # 跨语言共享的离线报告浏览器组件、vendor 与测试
├── sdks/                # 按语言组织的独立工程
│   ├── python/          # 五类领域 SDK 与中立共享包
│   ├── go/              # e2e SDK 与平台工具箱
│   └── typescript/      # perf / trace SDK、中立 common 与平台工具箱
├── spec/                # 跨语言运行与结果契约
├── schema/              # 语言中立的数据 schema
├── conformance/         # 各语言共同消费的行为 fixture
├── examples/            # 项目接入示例
└── docs/                # 共享设计与领域设计
```

## 关键约定

- **资产与执行分工**：spec-case 拥有 canonical Case 格式；被测项目拥有用例、业务适配、资源生命周期与验收标准；部署工作流拥有环境、凭据、目标版本和触发策略。本仓库提供执行、分析与报告机制，不 import 被测服务的 internal 代码。
- **可验证交付**：项目随需求和行为变更维护 Spec / Case，验证时保留目标版本、环境和证据来源。API、CLI、Pipeline、Job 是可替换的触发适配。
- **证据与结论分离**：执行或采集形成 Observation / Dataset，评估形成独立结果；相同证据支持反复评估。各领域保留自己的评估粒度、调度和判据；直接分析运行记录不要求补造 Case。
- **领域独立**：领域 SDK 之间互不 import；确属跨领域的模型与工具才进入中立共享层，共享层不能反向依赖领域或 toolbox，common 仅依赖标准库。业务语义留在消费项目，通用包只提供机制；CLI、API、Pipeline、Job 仅负责触发。
- **跨语言对等**：各语言保持惯用 API，共享契约与 conformance fixtures。修改公共行为时同步相关实现和 fixture；各语言的功能覆盖可以不同。
- **文档按作用域组织**：中英文 README 面向读者，只保留定位、能力选择和最短接入路径；根 AGENTS.md 自包含全仓核心模型、主流程和约束，局部核心模型归对应目录的 AGENTS.md。详细机制归对应 `docs/<topic>.md`，完整使用步骤归 SDK 或示例指南。通过目录自动发现局部说明，不维护逐项 AGENTS.md / README 索引。

## 可验证交付与校验入口

需求、外部行为或缺陷变更先落实为随代码演进的 Spec / Case，再对指定 revision、deployment 和
environment 执行验证。Verdict 必须可追溯到资产版本、目标版本与运行证据；skipped / error 不能
解释为已验证成功。执行完成也不自动意味着业务达标。

Python 的 common、toolbox 与五个领域 SDK 各自提供 format/lint/test；
`sdks/python/Makefile` 只负责统一构建、版本和报告资源准备，不重复执行叶子校验。
TypeScript 工作区入口位于 `sdks/typescript/Makefile`。通过 `repocli validate` 选择对应工程验证，
具体命令和局部限制见各工程 AGENTS.md。不存在的校验目标应报告 unavailable，不视为通过。

## 版本管理

根目录 `VERSION` 使用 `MAJOR.MINOR.PATCH`，表示仓库整体版本。提交代码、契约或文档改动时，
一并更新该版本；默认在根目录运行 `make bump` 递增 patch，需要时用 `make bump PART=minor`
或 `make bump PART=major`。bump 只更新版本文件，发布与打 tag 通过发布流程执行。

各 SDK 保持独立包版本；涉及 SDK 发布内容的改动，还需按对应工程的约定更新包版本与锁文件。

## References

按主线深入阅读：

1. [系统描述](docs/system.md)：Repository、Component、变更提案、Service、Graph 与业务知识。
2. [运行目标与现场事实](docs/runtime.md)：Environment、Host、Workload 实例和 Resource 快照。
3. [测试资产](docs/case.md)：Case / Spec binding，以及 Casegen 编写入口。
4. [执行与证据生成](docs/execution.md)：环境与 CaseRun 生命周期、阶段预算、清理和执行状态。
5. [证据评估与复评](docs/evaluation.md)：Unit grain、Dataset version、EvaluationRun 与结果归属。
6. [产物与报告](docs/report.md)：Artifact、Verdict、离线组件和 Graph 呈现。

领域机制与扩展：

- [Toolbox](docs/toolbox.md)：连接生命周期、协议与平台采集。
- [Session](docs/session.md)：Agent Session、Job 与 Prompt 的观察视角。
- [E2E](docs/e2e.md)、[Trace](docs/trace.md)、[Trajectory](docs/trajectory.md)、[Diagnosis](docs/diagnosis.md)：领域模型、流程与边界。
- [Perf 契约](spec/perf-contract.md)、[跨语言约定](spec/conventions.md)、[Verdict schema](spec/verdict-schema.yaml)：精确运行与输出契约。
- [项目级 Quality Harness 规划](docs/quality-harness.md)：尚未实现的上层能力发现与执行决策。
