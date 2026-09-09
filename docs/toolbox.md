# Harness 工具箱

> 本文描述可被多个 Harness 复用的环境操作与观测能力。工具箱回答“如何可靠地操作和观察”，
> 不拥有 Case、Dataset、EvaluationRun 或 Verdict，也不决定业务目标、执行时机和通过条件。

## 1. Kubernetes

Kubernetes Driver 是面向 e2e、perf 等多个 Harness 的中立工具，不是独立的 Kube Harness。Go
实现位于 `sdks/go/toolbox/kube`，Python async 实现位于 `sdks/python/harness_toolbox/kube`；两端使用语言惯用
API，共享以下控制与观测语义：

- 从显式 kubeconfig 或 Pod 内身份创建 client，并显式配置 request timeout 与语言对应的 client 容量
  （Go QPS / burst，Python connection pool）；
- 按 label selector 获取确定顺序的 Pod 快照；
- 以 Pod name + UID 锁定物理实例，避免延迟动作误操作同名替代 Pod；
- 按正常终止流程或零宽限强制删除指定 Pod，等待替代实例、Ready 或 Unschedulable 状态；
- 按 Pod UID 采集 Kubernetes Event，作为报告或失败分析证据。

Python 使用者通过 `quality-harness[kube]` 安装可选的 `kubernetes-asyncio` 依赖。两种实现都要求调用方
显式提供 namespace、请求超时和客户端容量参数；Go 使用 context 控制等待期限，Python 使用 async
方法的 `timeout_s` 控制等待期限。

Driver 只返回平台事实和执行结果。消费方仍负责提供 namespace、selector、动作时机和超时，并由所属
Harness 或被测项目判断这些事实表示恢复成功、容量不足还是其它结果。例如，恢复 e2e 可以组合删除
Pod、等待 replacement 和业务请求验证；perf 可以在发压期间采集 Pod 状态与 Event，但二者不因此
共享故障 Case、负载模型或 Verdict 规则。

环境、凭据、目标 revision、操作窗口和授权由部署领域持有。工具箱提供 API 或 Job 可调用的原语，
不意味着调用方可以绕过这些约束。

## 2. 故障注入后端

Chaos Mesh、ChaosBlade、Toxiproxy、AgentChaos 等可以作为工具箱中的具体故障注入后端；它们负责执行
和撤销受控故障、返回后端证据，不拥有故障意图、恢复标准或评估结论。

LitmusChaos 已经包含 Workflow、Probe 和 Result 等平台模型。接入这类后端时，应把它们视为执行协议
和证据来源，避免与 quality-harness 的 Case、EvaluationRun 和 Verdict 重复建模。

只有至少两个真实消费方需要同一种能力时，才从具体 Driver 中收敛公共接口；单一 Harness 或单一
产品专用的操作继续留在消费方，避免把工具箱演变成无边界的公共包。

## References

- 跨 Harness 通用内核：[`kernel.md`](kernel.md)
- e2e Target Driver 边界：[`e2e-harness.md`](e2e-harness.md)
- Go Kubernetes 实现：[`../sdks/go/toolbox/kube`](../sdks/go/toolbox/kube)
- Python Kubernetes 实现：[`../sdks/python/harness_toolbox/kube`](../sdks/python/harness_toolbox/kube)
- Perf 跨语言契约：[`../spec/perf-contract.md`](../spec/perf-contract.md)
