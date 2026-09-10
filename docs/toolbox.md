# Harness 工具箱

> 本文描述可被多个 Harness 复用的环境操作与观测能力。工具箱回答“如何可靠地操作和观察”，
> 不拥有 Case、Dataset、EvaluationRun 或 Verdict，也不决定业务目标、执行时机和通过条件。

## 1. Client、DataSource 与 Transport

TypeScript 平台工具箱位于 `sdks/typescript/toolbox`，独立发布为 `@compforge/harness-toolbox`；
Python 位于 `sdks/python/toolbox`，独立分发为 `harness-toolbox`。
它可供诊断宿主、业务适配与测试执行方复用，不要求调用方创建 Case 或依赖某个 Harness。

- **Client**：拥有协议资源和操作，负责初始化与幂等销毁，包括初始化失败后的部分资源清理。
- **DataSource**：以稳定 key 标识客户端配置，并构造 Client；构造阶段不执行外部访问。
- **ClientManager**：在一次根执行中按 DataSource key 复用初始化中的异步任务 和成功的 Client。
  初始化失败完成清理后允许重试；结束时取消并等待进行中的初始化，再按依赖顺序逆序销毁。
- **ClientProvider**：只提供借用入口，子调用方无权关闭根调用方的共享资源。
- **ConnectionSource**：解析协议连接信息，并声明适用的 Transport；环境配置语义由调用方提供。
- **Transport**：提供直连、端口转发或 Pod Python 等访问路径，不改变目标身份和协议语义。

调用方创建根 ClientManager，将 ClientProvider 传给嵌套或并发工作，并在根执行结束时统一 dispose。
DataSource key 必须覆盖影响复用的协议、目标、配置与凭据；可用摘要避免凭据出现在可观察 key 中。
共享容量和策略在一个根执行中保持一致，不能用同一个 key 请求相互冲突的策略。

MySQL、Redis、OpenSearch 和 Kubernetes 客户端实现这些原语。连接、排队、取消、资源释放属于工具箱；
授权、业务 SQL、Redis key、索引规则、采集时机和结果解释仍属于消费方。MySQL 只在建连网络错误时切换
Transport，认证错误或已开始执行的 SQL 错误不得触发重放。

PodLogClient 以物理 Pod/container 身份及绝对时间窗口共享采集源，向并发和晚到的消费者回放原始日志。
相对窗口或缺少实例身份的请求不能复用。消费者保有独立过滤与原始文件；根并发池和字节预算只约束真实
网络采集，不约束本地回放。容量、预算和访问期限显式传入，不将某个产品的现场默认值作为通用策略。

### 与 Environment / Service 的关联

Environment 包含逻辑 Service；Service 是某个 Component 在环境里的运行服务，不对应唯一的
Kubernetes Service 或 Workload。一个逻辑 Service 可以由多个 Deployment、Pod 或其它平台实例
共同承载，具体映射由部署配置表达。

common 拥有运行目标语义，toolbox 拥有访问机制。Python 的 `harness_common.toolbox` 将
`KubernetesEnvironment` 转为 KubernetesDataSource；`ServiceDataSource` 记录逻辑 Service 与
一个 DataSource 的访问关联，不继承 Service，不拥有 client，也不推导 Kubernetes 资源名。
一个 Service 可以关联多个 DataSource，多个 Service 可以共享同一个 DataSource。

连接 key 使用实际访问配置与容量，不使用逻辑环境名或服务名代替。同名环境指向不同集群、context
或 namespace 时不能复用；不同逻辑 Service 的相同访问配置可以复用。Transport 从 client 的访问配置
派生，最终由使用它的协议 client 持有连接和释放职责。ClientManager 的生命周期属于根执行，
不属于 Environment 或 Service。

依赖方向是 common 适配依赖 toolbox；独立 toolbox 不 import common，不要求 Doctor 或 Skill
构造 Repository / Component 才能操作基础设施。部署台账到 common 对象及平台资源的转换仍由消费方负责。

## 2. Kubernetes

Kubernetes Driver 是面向 e2e、perf 等多个 Harness 的中立工具，不是独立的 Kube Harness。Go
实现位于 `sdks/go/toolbox/kube`，Python async 实现位于 `sdks/python/toolbox/harness_toolbox/kube`；两端使用语言惯用
API，共享以下控制与观测语义：

- 从显式 kubeconfig 或 Pod 内身份创建 client，并显式配置 request timeout 与语言对应的 client 容量
  （Go QPS / burst，Python connection pool）；
- 按 label selector 获取确定顺序的 Pod 快照；Python 也可从 Service / Deployment 的完整 selector 查询，缺失资源、无 selector 与权限错误分别处理；
- 以 Pod name + UID 锁定物理实例，避免延迟动作误操作同名替代 Pod；
- 按正常终止流程或零宽限强制删除指定 Pod，等待替代实例、Ready 或 Unschedulable 状态；
- 按 Pod UID 采集 Kubernetes Event，作为报告或失败分析证据。

Python 使用者通过 `harness-toolbox[kube]` 安装可选的 `kubernetes-asyncio` 依赖。两种实现都要求调用方
显式提供 namespace、请求超时和客户端容量参数；Go 使用 context 控制等待期限，Python 使用 async
方法的 `timeout_s` 控制等待期限。

Driver 只返回平台事实和执行结果。消费方仍负责提供 namespace、selector、动作时机和超时，并由所属
Harness 或被测项目判断这些事实表示恢复成功、容量不足还是其它结果。例如，恢复 e2e 可以组合删除
Pod、等待 replacement 和业务请求验证；perf 可以在发压期间采集 Pod 状态与 Event，但二者不因此
共享故障 Case、负载模型或 Verdict 规则。

环境、凭据、目标 revision、操作窗口和授权由部署领域持有。工具箱提供 API 或 Job 可调用的原语，
不意味着调用方可以绕过这些约束。

Python 同时提供 Pod 创建、exec、port-forward、日志采集及上述控制和等待能力；
TypeScript 提供 exec、port-forward、Pod/Service 投影与日志采集；Go 提供上述控制和等待能力。各语言的能力覆盖可以不同，共有的 namespace、实例身份、取消及资源容量语义应保持
一致。原始执行通道不替代宿主授权，不自动将低层 kubectl 命令升级为具有 UID 保护的语义操作。

## 3. 故障注入后端

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
- Python Kubernetes 实现：[`../sdks/python/toolbox/harness_toolbox/kube`](../sdks/python/toolbox/harness_toolbox/kube)
- Perf 跨语言契约：[`../spec/perf-contract.md`](../spec/perf-contract.md)
