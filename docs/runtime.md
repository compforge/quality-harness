# 运行目标与现场事实

验证针对一个明确的 revision 或运行目标。对于运行服务，先绑定 Service 与 Environment，再根据
Workload 引用发现实际实例，或读取 Service 管理和使用的 Resource 集合。每份现场证据保留目标身份、
来源、时间与覆盖范围；声明、实例存在和健康结论分别表达。

系统对象之间的关系见 [系统描述](system.md)；环境准备、领域执行和清理顺序见 [执行生命周期](execution.md#环境与-case-的两层生命周期)。

## Environment

Environment 表达服务运行及验证所针对的环境。local、devbox、devbox-k8s 是具名环境；
OS、发行版、kernel 和权限是实测事实，不从这些名字推断。相同环境可以供 E2E、perf、
trace 及部署工具使用，环境模型不依赖具体 Harness。

### 可选的 Host

Host 是 Environment 的可选组成部分，描述主机身份及 local / SSH 访问方式。
Host 环境直接在这台机器运行目标；Kubernetes 环境通过这台机器操作集群。
Kubernetes 的 kubeconfig 路径属于这台 Host 的文件系统，Host 不代表 Pod 所在节点。
省略 Host 时，操作在当前执行主机进行；这不改变环境本身的身份。

例如，一台 devbox 可以直接运行 Hostel，也可以作为另一个 Kubernetes 环境的访问入口：

```yaml
# service.environment
name: devbox-k8s
kind: kubernetes
host:
  name: devbox
  transport: ssh
  address: test-builder
kubeconfig: /etc/rancher/k3s/k3s.yaml
context: default
image_registry: registry.example.com/team  # 可选，仅保存镜像仓库信息
```

KubernetesEnvironment 可选的 image_registry（TypeScript 为 imageRegistry）只保存镜像仓库信息，
当前不校验仓库、不改写镜像、不触发登录或访问，也不参与客户端复用身份。

Runner 是执行测试代码的角色，它可以在本机、Host 或 Pod 内运行。部署主机、Runner、
目标进程可能拥有不同的文件系统与权限；采集证据时必须保留来源。
即使部署主机和 Pod 共享 kernel，也不能把部署主机探测到的 ptrace 权限作为 Pod 的事实。

## EnvironmentContext

Environment 是配置与身份声明，不持有连接；EnvironmentContext 绑定本次选定的 Environment、
只含 get 的客户端借用视图与截止时间，供 Service、Workload 操作及部署、诊断、测试流程共同使用。
它不依赖 Fixture，不执行 I/O、不拥有客户端释放权，也不自动取消操作。调用方将剩余预算
传给具体操作，并在根执行退出前等待子任务结束。

Python 使用单调时钟秒数 deadline / remaining_s；TypeScript 使用 performance.now() 的
毫秒数 deadlineMs / remainingMs，不使用墙钟时间。TypeScript 的 EnvironmentContext<E> 保留
common Environment 的具体扩展类型；Service<E> 引用同一环境，不为客户端访问重建环境模型。
具体可访问环境实现 ClientProvider 的 client_key / create_client；DataSource 扩展同一契约。
DataSource 和 Workload 的可选 `description` 为发现与展示补充用途、职责或数据范围；不参与客户端复用、
Workload 身份或定位规则，也不表示目标已部署或可访问。说明只能包含可公开展示的元数据，不包含凭据。
调用方直接 clients.get(environment) 或 clients.get(datasource)，共用初始化、复用和释放机制。
通用 Environment 保留身份声明；具体 KubernetesEnvironment 及配置解析归 toolbox。
不引入只有 init/dispose 的 EnvironmentClient 基类，也不为 HostEnvironment 虚构客户端。

## Workload

Workload 描述逻辑负载及其平台位置。可选字段 `service` 引用当前 Environment 中所属的逻辑 Service 名。
省略时由所在的 Service 上下文确定归属。跨 Service 贡献时显式填写归属，例如
`sandbox-server` 贡献一个 `service: "bedbox"` 的 Workload，用 `location` 指定具体 Pod。
WorkloadInstance 是执行期实际发现的实例。一个声明可以没有实例，也可以对应多个副本；
声明不会因为 Pod 重建而变化，实例身份则必须区分重建前后。

### 流程与职责

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

### 定位是 Workload 的内部描述

逻辑名称与平台资源名分离。Kubernetes 可通过明确的控制器/Pod、网络 Service 的 selector，
或非空 Pod labels 定位；网络 Service 只是发现入口，不是负载控制器。
定位结构是声明内部的判别联合，不是第三个独立的 Discovery 概念。

Environment 提供集群与访问上下文。namespace 可以由声明指定或由解析上下文提供；
省略不代表跨所有 namespace 搜索。解析器必须先形成明确的有效 namespace，再进行发现。
绑定策略由调用方确定，不能在实例发现后静默切换目标环境。

### 实例身份与访问目标

实例携带调用方环境台账提供的稳定 environment ID，并在解析时显式传入。它必须区分实际目标，
不能仅使用可能重名的展示标签；访问同一目标的 context 别名应映射到同一个 ID。
kubeconfig 路径与凭据不作为公开证据身份。Kubernetes 实例以
environment、namespace、Pod 名和 UID 区分同名跨环境目标与 Pod 重建。
toolbox 原样保留该 ID，不从 API 地址、kubeconfig 或 Host 派生它，也不以该 ID 选择连接。
台账负责将 ID 绑定到本次选定的访问配置；调用方不能把不同目标误标为同一个 ID。
ClientProvider key 则覆盖访问配置、凭据及容量策略：换访问方式可以换连接，但不应改写证据目标身份。

workload 字段记录所属逻辑声明名，在 Service 上下文中解释；相同实例被多个声明发现，
物理身份仍相同，消费者保留各自归属关系。实例不是持续有效的访问保证，操作失败由访问层报告。

container 是操作选择，不改变 Pod 身份；Client/Transport 的路由与采集缓存必须独立包含
所选 container。实例相等不等于访问请求或采集结果可以无条件复用。

Python 使用 Workload/WorkloadInstance 基类及 Kubernetes 子类；TypeScript 当前仅实现
Kubernetes 形态，使用 platform 判别字段。两端消费同一组 conformance/workloads.json，
约束定位变体、逻辑名称、实例归属和身份比较；并不宣称具有通用外部 JSON 解析器。

### 解析失败

查询成功但没有匹配 Pod 返回空列表；指定资源不存在、权限不足、超时与无 selector 分别报告错误。
控制器遵循完整 selector（包含 matchExpressions），不额外声称 ownerReference 归属；
解析结果不按 Ready、Running 或副本数量过滤，采集策略由调用方决定。
原生异常由 toolbox 适配为 KubernetesError，稳定 kind/code 用于程序判断，
cause 只供调试，不直接写入面向用户的报告。ToolboxError 属于 toolbox，而不是 common。

## Resource

A Resource describes a collection of resources that a running Service persistently manages or uses.
Its instances, count and scale can change without changing the declaration. Examples include Beds,
sandboxes and resident sessions. It is an inventory concept, not a universal base class for every
business record, Workload or DataSource.

### Ownership and observation

Common owns `Resource`, `ServiceResource`, `ResourceInstanceRef`, `ResourceInstance`,
`ResourceMeasurement`, `ResourceCoverage` and `ResourceSnapshot`. TypeScript and Python expose
matching semantics; `conformance/resources.json` supplies shared identity and coverage examples.

- Resource `id` identifies a collection across managing and consuming Services. `kind`, name and
  description describe it; declarations perform no I/O and prove no live instances.
- ServiceResource associates a Service and Resource with `manages` or `uses`. The association does
  not create a client, establish exclusive ownership or change the underlying resource identity.
- Instance identity is environment + resource collection ID + local instance ID. Providers include
  backend/namespace identity in the collection ID when local IDs can collide. Do not key by display
  label or a temporary access endpoint. Recreated objects need incarnation-aware IDs where available.
- ResourceSnapshot records the environment, observation time, source, explicit filter scope and
  coverage. `totalCount` / `total_count` is independently known for that filter scope; never infer it
  from the current page length. Omit unknown totals. A complete empty result differs from unavailable.
- Measurements have a name, finite numeric value, unit and `observed` or `total` scope. A sample's
  memory footprint is not a whole-collection total. Different units/measurements are not additive.
- Instances retain domain state and typed details. State is not an implicit healthy/unhealthy verdict.
  A partial page can retain instances, a known total, and an opaque continuation cursor. Sources,
  cursors and details must be safe serializable evidence; never put credentials into a snapshot.

Consumers extend the declarations with their native read operations and own authorization,
pagination, cancellation, budgets, schema validation and presentation. Common does not define a
Doctor Extension kind, call a business API, render a table, or supply business lifecycle operations.
Workload describes a runtime carrier; DataSource supplies access to data. Resource does not replace
those models. A consumer can link inventory instances to existing WorkloadInstance identities.

```ts
import type { Resource, ResourceSnapshot, ServiceResource } from "@compforge/harness-common";

const beds: Resource = { id: "sandbox/beds", kind: "bed", name: "Beds" };
// Service identity and environment continue to use Common's existing model.
// const relation: ServiceResource = { service, resource: beds, relationship: "manages" };
const snapshot: ResourceSnapshot<{ pod: string }> = {
  resource: beds, environment: "test-cluster", observedAt: "2026-10-23T00:00:00Z",
  source: "bed-api", scope: { tenant: "example" },
  coverage: { status: "partial", reason: "page budget" }, totalCount: 17,
  instances: [{ ref: { environment: "test-cluster", resource: beds.id, id: "bed-1" },
    state: "retained", details: { pod: "carrier-1" } }], nextCursor: "next-page",
};
```

## 访问与归属

- common 拥有 Environment、可选 Host 和中立事实；环境生命周期见 [执行](execution.md)，具体 I/O 由项目回调与 toolbox 实现。
- toolbox 提供主机命令、事实采集及 Kubernetes 操作，使用调用方的超时和资源边界。
- 各 Harness 拥有自己的 Case / ArmRun 生命周期、领域判断与产物出口；E2E 另检查 Case 特有的环境前提。
- 项目拥有环境配置、部署 fixture、权限 profiles、行为断言和清理。

toolbox 的 Host 命令支持 local / SSH，Helm 等命令行工具使用这条路径。
`KubernetesEnvironment.create_client` 选择本地池或 Host-native worker，提供统一的
原生资源操作。底层连接池 provider 是 toolbox 私有实现，只管理当前进程的 API 池，避免误读远端同名
kubeconfig。访问通道与恢复边界见 [工具箱](toolbox.md)。
