# quality-harness

## 项目定位与边界

面向软件与 Agent 的质量执行与证据分析仓库，覆盖 e2e、eval、perf、trace、trajectory。
当前交付领域 SDK、共享契约与平台工具箱，支持主动测试和已有运行证据分析。

长期计划在本仓库实现项目级 Quality Harness，负责理解目标、发现能力、选择验证和解释证据。
该上层实现尚未落地，设计见 `docs/quality-harness.md`。

## 代码地图与核心模块

```text
quality-harness/
├── VERSION              # 仓库整体版本，各 SDK 包版本独立管理
├── scripts/             # 仓库维护脚本
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

## 核心模型与主流程

被测入口决定从哪里验证，Harness 决定观察与判定视角。e2e 验证契约，eval 评估产出质量，
perf 记录负载下的延迟、吞吐与资源，trace 解释调用链，trajectory 分析 Agent 决策与行动。
多个视角应复用同一次执行的 response、Outcome、trace 或 trajectory。

| 职责 | 核心对象与关系 | 改动归属 |
|---|---|---|
| 描述对象 | Repository 位于 Forge，包含 Component；Product 与 Component 可多对多；Service 是 Component 在 Environment 中的具名运行体现，对外提供 Operation | common `system`；具体业务声明归消费方 |
| 描述运行承载 | Service 引用 Workload；Resource 声明持续管理的资源集合；Graph 描述 Service / Operation 拓扑；实际实例和状态由独立观测记录 | common 模型，消费方发现与解释 |
| 获取事实 | ClientProvider / DataSource 提供访问契约，根 ClientManager 复用和回收客户端；EnvironmentContext 传递环境、借用入口与预算 | common `access`；协议、Transport 与平台 I/O 归 toolbox |
| 组织执行 | Experiment 是验证意图；ExperimentRun 通过领域 Execution 组织 OperationRun，每次调用产生 Outcome | common `execution` 定义事实；领域 Engine 拥有调度与生命周期 |
| 复用和解释证据 | Observation 与适用的 Case / Annotation 组成 Unit；Dataset 固定一组 Unit facts；EvaluationRun 把本次 Finding / Evaluation / Measurement 写入 Worksheet | 领域拥有 grain、schema 与判断，common 不统一执行器 |
| 交付结果 | Reducer 读取已记录事实产生 Artifact；Report 渲染，Verdict 按领域契约投影机器结论 | 公共产物契约与报告结构，领域归约逻辑 |

```text
项目声明对象与验证意图 → 借用访问能力 → 执行或采集 → Observation
  → Unit facts / Dataset → EvaluationRun / Worksheet → Metric / Artifact / Report / Verdict
```

执行路径可以采用 `ExperimentRun → Execution → OperationRun → Outcome`，也可以直接采集已有
trace / recording；后者无需补造 Case。实际阶段可以融合，但事实与本次评估结果必须可分离。
同一个 Dataset 的 Unit grain 必须一致；换规则、Judge 或模型复用事实，事实变化才形成新版本。
Finding 是带证据的发现，Measurement 是中性度量，Evaluation 是依据契约的判断，三者不得混用。
报告只消费已准备的数据，不访问远端、不重发请求、不重新执行 Judge。

Identity 由具体模型定义字符串格式与唯一性范围，描述性元数据不参与身份。Service 的完整身份
包含 Component 与 Environment；Graph / Run 中的 Service、Operation 局部 ID 由环境 catalog 限定范围。
静态拓扑、知识与资源声明均不能代替运行事实。

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

Python 的 common、toolbox 与领域 SDK 由 `sdks/python/Makefile` 统一提供 format/lint/test/build；
TypeScript 工作区入口位于 `sdks/typescript/Makefile`。通过 `repocli validate` 选择对应工程验证，
具体命令和局部限制见各工程 AGENTS.md。不存在的校验目标应报告 unavailable，不视为通过。

## 版本管理

根目录 `VERSION` 使用 `MAJOR.MINOR.PATCH`，表示仓库整体版本。提交代码、契约或文档改动时，
一并更新该版本；默认在根目录运行 `make bump` 递增 patch，需要时用 `make bump PART=minor`
或 `make bump PART=major`。bump 只更新版本文件，发布与打 tag 通过发布流程执行。

各 SDK 保持独立包版本；涉及 SDK 发布内容的改动，还需按对应工程的约定更新包版本与锁文件。

## References

- 对象关系、身份范围与序列化：[系统对象与身份](docs/system-model.md)
- 产品与服务的离线业务说明：[Knowledge](docs/knowledge.md)
- 调整执行事实或产物归约：[Experiment 执行与产物](docs/execution.md)
- 调整事实复用、评估粒度或结果投影：[Dataset 与复评](docs/evaluation-data.md)
- [Environment、可选 Host 与环境证据](docs/environment.md)
- [项目级 Quality Harness 设计](docs/quality-harness.md)
- [共享平台工具箱](docs/toolbox.md)
- [Service 持有的资源与盘点快照](docs/resource.md)
- [跨语言约定](spec/conventions.md)
- [统一 Verdict 契约](spec/verdict-schema.yaml)
- [暂缓能力与启动条件](docs/backlog.md)
