# Workload 与 WorkloadInstance

Workload 是 Service 贡献的逻辑负载声明，包含平台定位描述，不代表已经部署。
WorkloadInstance 是执行期实际发现的实例。一个声明可以没有实例，也可以对应多个副本；
声明不会因为 Pod 重建而变化，实例身份则必须区分重建前后。

## 流程与职责

```text
Service.workloads + Environment
    → 平台解析与目标选择
    → WorkloadInstance[]
    → 日志、状态、网络或内存采集
```

common 提供声明与实例契约，不执行解析。toolbox 的平台能力负责 I/O，消费工具组织选择、
采样、预算和证据输出。声明与实例都不持有 Client 或 Transport，也不继承 DataSource；
访问机制继续复用既有客户端生命周期。toolbox 的 Kubernetes client 提供解析入口；
Python 的 Environment resources client 同时支持本机与 Host-native 后端。

## 定位是 Workload 的内部描述

逻辑名称与平台资源名分离。Kubernetes 可通过明确的控制器/Pod、网络 Service 的 selector，
或非空 Pod labels 定位；网络 Service 只是发现入口，不是负载控制器。
定位结构是声明内部的判别联合，不是第三个独立的 Discovery 概念。

Environment 提供集群与访问上下文。namespace 可以由声明指定或由解析上下文提供；
省略不代表跨所有 namespace 搜索。解析器必须先形成明确的有效 namespace，再进行发现。
绑定策略由调用方确定，不能在实例发现后静默切换目标环境。

## 实例身份与访问目标

实例携带调用方环境台账提供的稳定 environment ID，并在解析时显式传入。它必须区分实际目标，
不能仅使用可能重名的展示标签；访问同一目标的 context 别名应映射到同一个 ID。
kubeconfig 路径与凭据不作为公开证据身份。Kubernetes 实例以
environment、namespace、Pod 名和 UID 区分同名跨环境目标与 Pod 重建。
toolbox 原样保留该 ID，不从 API 地址、kubeconfig 或 Host 派生它，也不以该 ID 选择连接。
台账负责将 ID 绑定到本次选定的访问配置；调用方不能把不同目标误标为同一个 ID。
ClientFactory key 则覆盖访问配置、凭据及容量策略：换访问方式可以换连接，但不应改写证据目标身份。

workload 字段记录所属逻辑声明名，在 Service 上下文中解释；相同实例被多个声明发现，
物理身份仍相同，消费者保留各自归属关系。实例不是持续有效的访问保证，操作失败由访问层报告。

container 是操作选择，不改变 Pod 身份；Client/Transport 的路由与采集缓存必须独立包含
所选 container。实例相等不等于访问请求或采集结果可以无条件复用。

Python 使用 Workload/WorkloadInstance 基类及 Kubernetes 子类；TypeScript 当前仅实现
Kubernetes 形态，使用 platform 判别字段。两端消费同一组 conformance/workloads.json，
约束定位变体、逻辑名称、实例归属和身份比较；并不宣称具有通用外部 JSON 解析器。

## 解析失败

查询成功但没有匹配 Pod 返回空列表；指定资源不存在、权限不足、超时与无 selector 分别报告错误。
控制器遵循完整 selector（包含 matchExpressions），不额外声称 ownerReference 归属；
解析结果不按 Ready、Running 或副本数量过滤，采集策略由调用方决定。
原生异常由 toolbox 适配为 KubernetesError，稳定 kind/code 用于程序判断，
cause 只供调试，不直接写入面向用户的报告。ToolboxError 属于 toolbox，而不是 common。
