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
│   └── typescript/      # perf / trace SDK
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
- **领域独立**：领域 SDK 之间互不 import；确属跨领域的模型与工具才进入中立共享层，共享层不能反向依赖领域。业务语义留在消费项目，通用包只提供机制。
- **跨语言对等**：各语言保持惯用 API，共享契约与 conformance fixtures。修改公共行为时同步相关实现和 fixture；各语言的功能覆盖可以不同。
- **文档按作用域组织**：中英文 README 面向读者，只保留定位、能力选择和最短接入路径；根 AGENTS.md 保留全仓约定，语言工程与领域细节放在对应目录的 AGENTS.md。详细设计归 `docs/`，完整使用步骤归 SDK 或示例指南。通过目录自动发现局部说明，不维护逐项 AGENTS.md / README 索引。

## 版本管理

根目录 `VERSION` 使用 `MAJOR.MINOR.PATCH`，表示仓库整体版本。提交代码、契约或文档改动时，
一并更新该版本；默认在根目录运行 `make bump` 递增 patch，需要时用 `make bump PART=minor`
或 `make bump PART=major`。bump 只更新版本文件，发布与打 tag 通过发布流程执行。

各 SDK 保持独立包版本；涉及 SDK 发布内容的改动，还需按对应工程的约定更新包版本与锁文件。

## References

- [通用内核与职责边界](docs/kernel.md)
- [项目级 Quality Harness 设计](docs/quality-harness.md)
- [共享平台工具箱](docs/toolbox.md)
- [跨语言约定](spec/conventions.md)
- [统一 Verdict 契约](spec/verdict-schema.yaml)
- [暂缓能力与启动条件](docs/backlog.md)
