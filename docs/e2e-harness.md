# e2e_harness 设计

> 本文描述跨语言 e2e 领域：当前面向单服务公开契约的 Case 路径，以及产品级 Playbook / Target
> 功能测试的长期边界。

## 1. 核心概念

| 概念 | 语义 |
|---|---|
| **Playbook** | 用自然语言表达的产品功能测试剧本：用户目标、前置条件、有序动作、关键检查点和最终验收结果。它与 Web、Android、iOS、API 等执行目标解耦。 |
| **Script** | Playbook 针对某个 Target 编译出的可执行代码；可 review、可重放、可再生成，但不是产品需求的事实源。 |
| **Target** | Script 操作的产品边界，例如 Web、Android、iOS 或产品 API；决定使用的 SDK / Driver 和可采集证据。 |
| **Driver** | 某类 Target 的稳定动作、等待、资源生命周期与证据采集能力，不拥有产品域动作和验收标准。 |

`Scenario` 通常表示特定条件下的单个示例，容易与 Case 重叠；`Playbook` 更强调为了达成用户目标而
执行的有序动作。只有真实功能测试需要独立标识和编排多个变体时，Scenario 才值得成为下一层概念。

## 2. 主流程

```text
Playbook + Target
  → AI Compiler（authoring time）
  → reviewed Script
  → Target SDK / Driver
  → Observation（Outcome + screenshots / video / logs / traces）
  → Unit / Dataset / EvaluationRun / Worksheet
  → Verdict + Report
```

AI 只参与编写期编译。生成的 Script 进入被测产品仓库，经过 review 并随代码提交；运行时执行已确定的
Script，不临时调用 LLM 决定测试步骤。Script 保留 Playbook version/hash、Target、编译器与 SDK
版本等 provenance。

同一 Playbook 可以生成 Web、Android、iOS 和产品 API 等多份 Script。共享的是用户目标与验收结果，
不是一份跨平台的最低公分母脚本。

## 3. 测试边界

| 层级 | 意图锚点 | 执行边界 | 能覆盖 | 不能替代 |
|---|---|---|---|---|
| Web / App 功能测试 | Playbook 中的用户目标 | UI / 产品入口，可横跨多服务 | 客户端代码、导航、交互和后端整体链路 | 其它平台客户端 |
| API 功能测试 | 同一 Playbook 中的用户结果 | 产品入口 API，可串联多步、多服务 | 业务规则与后端整合 | Web / App 客户端代码与交互 |
| 服务 API 契约测试 | API 契约与 Case | 单个服务的 HTTP / SSE / RPC 边界 | 服务对外语义及其内部完整链路 | 产品级多步用户功能 |
| unit 测试 | 实现代码 | 函数 / 类 / 模块 | 局部行为和边界分支 | 对外契约与真实集成 |

API 功能测试是功能测试的一种 Target 投影，不是服务 API 契约测试的别名。只有 Web / App Script 能
直接覆盖客户端代码和交互。

## 4. Target SDK / Driver

每类 Target 的 SDK / Driver 至少负责：

- 会话、认证、前置数据和清理；
- 稳定等待、重试与超时；
- 平台动作和断言原语；
- screenshot、录屏、网络日志、设备日志和 trace 等失败证据；
- 与通用 Run / Verdict 契约对齐。

平台通用能力属于 quality-harness；“创建笔记”“邀请协作者”等产品域动作属于被测产品仓库。Web、
Android、iOS 和 API 共享 Playbook、Run 与 Verdict 语义，但各自持有会独立演进的 Driver。

跨 Harness 的 Kubernetes、故障注入等环境能力由 [Harness 工具箱](toolbox.md) 持有。Target Driver
可以组合这些能力，但不把平台操作改造成 e2e 领域模型。

CLI、API、Pipeline 和 Kubernetes Job 只是外部触发适配。被测服务不需要暴露“运行测试”的业务接口；
环境、凭据、执行窗口和授权仍由部署领域持有。

## 5. Owner 与状态

- spec-case 持有未来 Playbook 等稳定资产格式。
- quality-harness 持有 authoring compiler、Target SDK / Driver、Run 产物和 Verdict 契约。
- 被测项目持有 Playbook 实例、review 后的 Script、产品域动作与验收标准。

当前 Case / CaseSet 与服务 API e2e 已实现。Playbook schema、Playbook → Script 编译以及 Web、Android、
iOS、产品 API Target SDK 尚未实现；等待首个真实功能测试消费方校准后再建立，不预建空接口或无消费方
schema。

## References

- 通用数据与评估内核：[`kernel.md`](kernel.md)
- 跨 Harness 环境操作与观测：[`toolbox.md`](toolbox.md)
- Canonical Case 与 CaseRun：[`case-unification.md`](case-unification.md)
- 暂缓能力与启动条件：[`backlog.md`](backlog.md)
