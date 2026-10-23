# Harness 工具箱

> 本文描述可被多个 Harness 复用的环境操作与观测能力。工具箱回答“如何可靠地操作和观察”，
> 不拥有 Case、Dataset、EvaluationRun 或 Verdict，也不决定业务目标、执行时机和通过条件。

## 1. Client、ClientProvider、DataSource 与 Transport

TypeScript 平台工具箱位于 `sdks/typescript/toolbox`，独立发布为 `@compforge/harness-toolbox`；
Python 位于 `sdks/python/toolbox`，独立分发为 `harness-toolbox`。
它可供诊断宿主、业务适配与测试执行方复用，不要求调用方创建 Case 或依赖某个 Harness。

- **Client**：拥有协议资源和操作，负责初始化与幂等销毁，包括初始化失败后的部分资源清理。
- **ClientProvider**：通过 client_key / clientKey 标识客户端配置，以 create_client / createClient 构造 Client；构造阶段不执行外部访问。
- **DataSource**：具有数据来源语义的 ClientProvider，例如数据库、日志和指标，不代表所有环境操作。
  可选 `description` 用于说明来源用途与数据范围，供能力发现和报告展示；不得包含凭据，也不参与客户端复用身份。
- **ClientManager**：在一次根执行中按 ClientProvider key 复用初始化中的异步任务和成功的 Client。
  初始化失败完成清理后允许重试；结束时取消并等待进行中的初始化，再按依赖顺序逆序销毁。
- **ConnectionSource**：解析协议连接信息，并声明适用的 Transport；环境配置语义由调用方提供。
- **Transport**：提供直连、端口转发、Host-native worker 或 Pod Python 等访问路径，并拥有相应通道的创建、失效和关闭。

调用方创建根 ClientManager，将只含 get 的借用视图传给嵌套或并发工作，并在根执行结束时统一 dispose。
ClientProvider key 必须覆盖影响复用的协议、目标、配置与凭据；用 client_key / clientKey 生成摘要，
避免凭据出现在可观察 key 中。
共享容量和策略在一个根执行中保持一致，不能用同一个 key 请求相互冲突的策略。

ClientProvider 是“可提供 Client 的对象”的共同契约；可访问的具体 Environment 和 DataSource 都实现它。
ClientManager 接收它们：clients.get(environment) / clients.get(datasource)，并负责复用和释放。
通用 Environment 只表达身份，不强迫没有访问实现的环境提供空客户端。构造时接收只含 get 的借用视图，初始化期间通过它取得
依赖客户端；依赖图须无环，消费者不释放借用对象。TS 额外接收根 AbortSignal。
初始化失败且清理成功后允许重试；清理失败则保留失败身份、禁止重建，并在根释放时报告错误。

Client 是执行期稳定的协议操作入口；Transport 内部的 worker、隧道可以失效和替换。
数据库认证、事务与 HTTP 连接池等协议会话仍归 Client / 原生库。这里没有跨协议的公共 Session。
通道中断使本次请求失败并淘汰该通道，下一个独立请求才可以重连；已发送的业务操作不自动重放。
最终 dispose / scope exit 后不允许重开，借用者不关闭共享通道。

Kubernetes port-forward 缓存同时保留资源 UID 和子进程存活状态。Service UID 不变而
后端 Pod 重启时，已退出的隧道也必须重建；每次新建连接仍检查目标身份。

MySQL、Redis、OpenSearch、S3 和 Kubernetes 客户端实现这些原语。连接、排队、取消、资源释放属于工具箱；
授权、业务 SQL、Redis key、索引规则、采集时机和结果解释仍属于消费方。MySQL 只在初始化阶段切换地址，建连网络错误可以切换
Transport，认证错误或已开始执行的 SQL 错误不得触发重放。相互独立的语句可用 `queryBatch` 合并提交：
Pod Transport 下进程启动、exec 握手与认证只发生一次，语句按提交顺序执行，任一失败即整批失败（fail-fast）。

TypeScript RedisClient 持有完整的 single / sentinel / cluster 配置。endpoints 在三种模式下分别表示
单机、Cluster 或 Sentinel 的种子地址，Sentinel 与数据节点的身份分别保存。所有节点使用同一组有序
TCP Transport；只在建连网络失败时切换，鉴权失败不切换，命令和 pipeline 不自动重放。connectTimeoutMs
约束单条路径的地址映射与握手，默认使用命令的 timeoutMs；acquireTimeoutMs 约束全部候选路径的总期限，
默认按单条路径预算乘候选数量。总预算耗尽不再启动下一条路径。根 signal 取消建连与在途命令；失效连接在下一次获取时重建，最终 dispose 后不可重开。
拓扑发现实现属于 toolbox，Client.topology() 按需刷新；诊断采样、业务 key 与结果缓存策略由消费方决定。

TypeScript MySQL 的有界查询 helper 为调用方提供的 SQL 追加经过整数校验的 LIMIT，并通过额外一行
判断截断；表结构、过滤条件和查询优先级仍由业务方拥有。`queryReadonly` 继续负责独立只读会话的
行数、字节与时间预算，两种能力不混用语义。

TypeScript 数据库适配器 `withReadScope` 将读取接入同一个 DataLoader，按完整数据库身份、SQL
与参数类型隔离。适配器借用 scope 和数据库，关闭权归调用方；底层查询的取消与超时仍归 Client。
相同读取共享结果和失败，不能视作数据库一致性快照。批量操作复用命中项，将缺失项有序交给数据库。

PodLogClient 以物理 Pod/container 身份及绝对时间窗口共享采集源，向并发和晚到的消费者回放原始日志。
相对窗口或缺少实例身份的请求不能复用。消费者保有独立过滤与原始文件；根并发池和字节预算只约束真实
网络采集，不约束本地回放。容量、预算和访问期限显式传入，不将某个产品的现场默认值作为通用策略。

### S3 对象访问

TypeScript 的 S3DataSource 把调用方已解析的连接配置、凭据、访问路径身份和容量策略绑定为复用身份。
S3Client 基于官方 SDK 提供只读 metadata、单页列表和有界对象读取；业务 bucket/key 规则、扫描总预算、
授权与证据解释归消费方。MinIO 等平台管理指标不是 S3 数据面接口，不进入此 client。

并发槽覆盖完整请求及响应流消费，截止时间覆盖排队；取消或销毁会终止读流并释放连接池。列表不自动
遍历后续页，正文达到预算时关闭读流并标记截断。SDK 错误保留状态，HEAD 权限拒绝不能伪装成对象不存在。

Transport 只映射连接地址，不改变签名 Host 或 TLS 身份；映射通道使用 path-style，并保持证书校验。
调用方提供私有 CA 时只改变信任来源，不关闭验证。client 初始化仅准备协议资源和访问路径，不用
ListBuckets 预检强加账号级权限，也不自动重放失败请求。

### 与 Environment / Service / Workload 的关联

Environment 与 Service 描述逻辑运行目标；Service 通过 `workloads` 声明零个或多个运行载体。
Workload 是部署引用，不是实时实例列表，不保证目标此刻存在。部署配置负责提供和更新映射，
映射变化不改变逻辑 Service 身份。多个 Service 也可以引用同一个 Workload。

Python common 提供 `Workload` 与 `KubernetesWorkload`。后者以 namespace/kind/name 精确定位
Deployment、StatefulSet、DaemonSet 或 Pod；container 是可选操作配置。Kubernetes Service 是网络
入口，不属于 Workload。执行方根据声明查询 Pod 快照，再选择采样或聚合策略。

Toolbox 的范围是通用基础设施访问与操作，包括数据库、OpenSearch、Transport、进程与 Kubernetes，
不以 Workload 为中心。Workload 操作只是其中一组能力；直接访问数据库无需构造 Service 或 Workload。
`harness_toolbox.environment` 适配逻辑环境的访问配置，`ServiceDataSource` 可关联 Service 与独立 DataSource；
数据库执行入口是 DataSource/Client，逻辑服务解析不进入协议客户端。

ClientManager 属于根执行，按实际访问配置、凭据与容量复用客户端并集中释放。环境名、服务名或
Workload 名不能代替连接身份。EnvironmentContext 绑定本次环境、借用入口与预算，不自行创建或销毁连接。
Python 的 Environment、EnvironmentContext、Service、Client、ClientProvider、DataSource、
ClientManager、ServiceDataSource 与配置 key 归独立的 harness-common；具体协议客户端、Transport、
地址解析、具体 KubernetesEnvironment 及其配置解析归 harness-toolbox。依赖方向为 toolbox → common → 标准库；common 的
LLM HTTP 客户端通过独立 extra 引入 httpx。原 toolbox 生命周期符号保留转导出。

共享层按语言独立组织：Python 位于 `sdks/python/common`，TypeScript 位于
`sdks/typescript/common`，分别发布 `harness-common` 与 `@compforge/harness-common`。
TypeScript common 承载 Client、ClientProvider、DataSource、ClientManager、EnvironmentContext、ServiceDataSource
与配置 key；Service 关联保留消费方的 Service 类型，不引入 Doctor 或平台专用定义。
两种语言不要求功能覆盖完全相同；TypeScript toolbox 同样只转导出中立生命周期符号。

`ConnectionSource.addresses` 可声明 `AddressPolicy(environment, namespace, fallback_hosts)`。
IP 直接使用；Kubernetes 短名和指定 namespace 的两段 Service 名，以及 `name.namespace.svc[.<cluster-domain>]`
通过对应环境的 API 获取 ClusterIP（headless 使用 ready endpoints），不回退本机短名 DNS。
跨 namespace 使用带 `.svc` 的明确名称；其余域名按 DNS 返回顺序尝试 IP，候选去重。
未声明 AddressPolicy 时，名称解析仍由 Transport 所在的访问位置负责。

MySQL 以认证并选择 database 成功、OpenSearch 以初始化 HEAD 成功作为选中依据，保留客户端供后续请求使用。
地址初始化失败可以尝试下一个候选，鉴权或 database 错误不在同一地址上换通道重试。
SQL/搜索执行失败不重放。TLS 保留原始 servername 与 HTTP Host，不因 IP 或隧道映射关闭证书校验。
诊断分别提供配置 target、各次 endpoint/source/transport、失败原因与 selected_endpoint，不包含凭据。

## 2. Kubernetes

TypeScript 的根 `KubernetesClient` 直接提供 `get(ref)` / `list(query)`，返回资源对象和结构化
`KubernetesError`。查询声明 API version 与 resource 的 REST 复数名，适用于内置资源与 CRD；
不解析 kubectl argv，不把 API 结果转换成 stdout/stderr。列表保留分页 metadata，调用方显式选择
后续页。根客户端使用配置的 namespace，查询可覆盖；`inNamespace()` 返回固定 namespace 的借用视图。

根客户端拥有一个长期存活的 HTTP transport，跨 namespace 与资源类型复用连接；Workload 解析走
相同 get/list 入口。每次请求重新应用 kubeconfig 认证，不缓存查询结果。认证配置与连接生命周期
分别由 KubeConfig 和 transport 持有；SDK 的 Agent 创建细节只存在于 transport 的认证适配处。
TLS/代理身份变化时旧池待在途读取结束后回收。期限覆盖排队、认证与响应读取，根客户端关闭时取消
在途任务并销毁池。借用视图没有关闭权，外部注入的资源读取器由其创建方管理。

资源读取使用 kubeconfig 中的显式 `proxy-url`，不依据 shell 代理环境变量切换执行方式。失败直接
返回，不通过另一条路径重放。`exec` 与 `forward` 是独立流式操作；根客户端不提供 `run(argv)`。
TypeScript exec 直接连接 Pod exec WebSocket，不启动 kubectl、不预读 Pod，也不重放失败命令。
每次 exec 使用独立流式会话，根持有配置和容量，认证随请求刷新；取消根会终止在途和排队会话。
exec 使用独立槽位池，容量和字节预算取根 ResourceLimits；单次 timeout 可覆盖默认期限，
期限覆盖排队、认证、握手和执行。stdout/stderr 分流，按远端 status 判定退出码；
保留输出与单帧有字节上限，关闭 collectStdout 后由流式消费方控制总量。
无 stdin 时支持 v4/v5，有 stdin 时要求 v5 的输入半关闭语义。forward 仍由 kubectl 承载。
Bun 不暴露握手拒绝的 HTTP 状态，保持 unknown connection failure。Bun 代理 WebSocket 的
销毁和 TLS 名称覆盖存在限制，因此 kubeconfig proxy 下原生 exec 明确拒绝，需使用 Node Host
或调用方显式提供 Executor；不绕过代理、不关闭证书验证、不在命令可能执行后重放。
连接复用不改变采集调度并发度。

Kubernetes Driver 是面向 e2e、perf 等多个 Harness 的中立工具，不是独立的 Kube Harness。Go
实现位于 `sdks/go/toolbox/kube`，Python async 实现位于 `sdks/python/toolbox/harness_toolbox/kube`；两端使用语言惯用
API，共享以下控制与观测语义：

- 从显式 kubeconfig 或 Pod 内身份创建 client，并显式配置 request timeout 与语言对应的 client 容量
  （Go QPS / burst，Python connection pool）；
- 按 label selector 获取确定顺序的 Pod 快照；Python 也可从 Kubernetes Service 或 workload controller 的完整 selector 查询，缺失资源、无 selector 与权限错误分别处理；
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

### 原始资源与权限实验

Python KubernetesClient.resources 使用 `kubernetes-asyncio` 的动态 API，复用同一
连接池与请求预算，支持原始 manifest 的创建、读取、列举和带 UID 条件的删除。
Pod、ConfigMap 等命名空间资源限制在 client 的 namespace；Node、Namespace、admission
配置等集群资源仍受 API 权限约束。原始 manifest 保留 admission 后的字段，便于比较提交
配置与实际配置，不在 toolbox 复制各种产品的 Pod schema。

Pod 完成等待返回成功或失败的终态，日志通过原生 API 有界读取；二者均核验 Pod UID。
创建哪些资源、何时清理、如何保存证据与解释准入拒绝由消费方拥有。
### 本地与 Environment Host 上的统一资源访问

`KubernetesEnvironment` 自身提供 client_key / create_client，Options 是其访问配置的一部分。
`clients.get(environment)` 在本机借用
原生 Kubernetes 连接池，在 SSH Host 上通过持续存活的 `python3 -m harness_toolbox.kube.worker`
执行相同的原生资源操作。支持 manifest create / get / list / UID delete、删除等待、Pod 完成
等待及有界日志读取。namespace 校验、UID 保护和 API 错误状态在两端保持一致。
Host 上须安装相同版本的 `harness-toolbox[kube]`；只传配置路径与操作参数，凭据留在 Host。

`ResourceListDataSource` 提供只读列举视图，借用同一资源后端。读视图不拥有 worker，也不能
关闭共享后端；API 权限由 Host 身份和 Kubernetes RBAC 限定。资源创建与清理由消费方明确调用。

请求继承 Options 的 namespace、请求时间和连接池预算；远端消息受 `max_exec_bytes` 限制，
stderr 有界收集。取消、超时或协议断流会退役当前 worker，后续请求新建通道，避免读到残留响应。
正常 API 错误保留状态并返回给调用方，不销毁健康通道、不重放请求。Host 命令入口仍用于 Helm
等程序；Kubernetes 资源操作在 Host 上调用原生 API，不转换为逐项 kubectl 子进程。

## 3. 读取范围与 Prometheus

`ClientManager` 共享连接与客户端状态；`DataLoader` 共享一轮读取的结果、错误和在途任务。
调用方显式创建读取范围，适配器用 DataSource 配置和完整查询参数决定共享键。
单个等待者取消不影响其它等待者；范围退出会取消并等待未完成读取，再释放结果。
范围应嵌套在 ClientManager 内；消费方不能修改共享结果。DataLoader 只做单轮缓存和去重，
不自动 batch，也不保证 Kubernetes 与 Prometheus 等不同来源处于同一原子快照。
`ResourceListClient.list(..., scope=scope)` 和 `PrometheusClient.read(..., scope=scope)`
都支持这个可选参数，不传时每次重新访问。
TypeScript DataLoader 显式声明条目数与序列化结果字节预算；超出容量的读取正常执行但不保留。
关闭时向 operation 发出 AbortSignal 并等待退出；operation 必须响应信号才能及时取消。

Python `harness-toolbox[prometheus]` 提供 `PrometheusDataSource`，描述 `/metrics` 地址、
显式请求头、HTTP 连接池、总抓取超时、响应体及历史容量。客户端通过独立 HTTP 池抓取，
用内嵌 Prombed 保存短期样本并执行 PromQL；它不是远端 Prometheus HTTP 查询 API 的客户端。
相同配置在 ClientManager 内复用抓取客户端和历史；同一 DataLoader 内只抓取一次，
各消费方分别执行查询，返回原始 Prometheus 类型与 labels。

读取频率与范围边界由调用方决定；toolbox 不运行采样循环，也不声明业务指标或 SLO。
perf 每个 ArmRun 持有 ClientManager、每轮观测创建 DataLoader，再把查询结果映射为指标。
这样下一轮可见新数据，内嵌 Prombed 的新 ArmRun 不会读到上一轮试验的历史。

直接抓取也可声明多个实例目标，或通过 `KubernetesScrapeDiscovery` 复用 Workload 解析发现 Pod。
实例 UID 进入 Prometheus instance 标签，替代 Pod 不与旧计数器拼接；消失实例记录 stale。
抓取并发、超时与响应预算归同一个客户端，任一实例失败不能表现为成功的服务级聚合。
`query_window` 只查询已保留的本地历史，历史缺失或采集不完整时显式失败；采集调度和业务 PromQL
仍归消费方。Pod 指标地址须从执行机可达，Kubernetes API 的 Host 路径不隐式代理 HTTP 抓取。

远端 Prometheus 使用 Python `PrometheusQueryDataSource`，通过现有 HTTPClientProvider
借用独立连接池，复用相同 ClientManager 生命周期。`read(expressions, timestamp=..., scope=...)`
调用 `/api/v1/query`；单轮 DataLoader 共享求值时间与相同表达式的结果或错误。端点、显式认证、
超时、响应字节数与序列上限属于 DataSource；超限失败，不请求服务端截断结果。
原始 labels、warnings 和 infos 保留在返回值中，perf Probe 将 warnings 视为观测失败。

远端历史归 Prometheus 管理，查询窗口可以包含压测开始前的数据。环境、租户、实例与时间窗口
由调用方的 PromQL 显式选择；toolbox 不推断业务归属。网络地址必须从执行机可达，
Service 名称只是 perf 报告的归属标签，不隐式添加查询条件或进行集群路由。

## 4. 故障注入后端

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


## 文件数据源

Common 的 `FileDataSource.paths` 声明一组可以共同获取的文件；一个来源就是一个获取单元。
`FileClient.read` 每次获取整组，按声明顺序逐路径返回成功字节或独立错误。Toolbox 的
`LocalFileDataSource` 与 `PodFileDataSource` 分别完成本机和同一 Pod/Container 的读取。
来源身份覆盖完整路径组、访问目标和容量策略，构造时冻结声明，声明来源不做 I/O。

调用方完成 Workload/Container 选择后创建来源；文件客户端借用根 Kubernetes Client，根按依赖顺序销毁。
Pod 文件组只发起一次 exec，要求 sh、head -c、base64、tr，以索引和编码记录传输原始字节及每文件状态；
路径通过 argv 传入，不插入 shell 源码，不写容器临时文件，也不在失败后改成逐文件重放。
本机来源只读常规文件（可经过 symlink），不因 FIFO 等非常规路径阻塞初始化。

每文件 maxBytes 独立计算，错误文件不交付部分字节，不丢弃成功的其它文件。maxTotalBytes 限制整个读取
保留的字节（包含在途文件）；整次 timeoutMs 包含排队、访问和完整传输。取消、总量超限、整次超时与
传输/协议失败拒绝整次操作。每次 read 重新读取路径，不缓存内容；共同获取不承诺原子文件系统快照。
路径和 Pod target 为来源元数据，配置解析、覆盖规则和证据持久化由消费方负责。

Common 0.5.4 与 Toolbox 0.4.2 用 paths 和逐文件结果替代旧的单 path/单字节返回接口，单文件也声明为
一个元素的组；先发布 Common，再发布依赖它的 Toolbox，最后由消费方升级。

## Kubernetes compute evidence

TypeScript `kubernetes/compute` exposes two read-only collectors over the existing ResourceAccess.
They do not construct clients or close borrowed transports. `KubernetesClient.clusterResources()`
provides an explicit root view, while existing get/list and namespace views keep their default scope.

```ts
import { collectClusterCompute, collectWorkloadCompute } from "@compforge/harness-toolbox/kubernetes/compute";

const options = { environment: "test-cluster", timeoutMs: 30_000,
  maxNodes: 1_000, maxPods: 10_000, pageSize: 500, signal };
const cluster = await collectClusterCompute(client.clusterResources(), options);
// instances are WorkloadInstances already discovered by the caller.
const workload = await collectWorkloadCompute(client, instances, options);
```

Cluster reads Nodes, all-namespace Pods and NodeMetrics independently. Workload reads exactly the
supplied Pod incarnations, retaining Workload provenance and deduplicating environment/namespace/name/UID
across aliases or container defaults. It accounts for whole Pods, including sidecars; it does not
claim the supplied instances exhaust a Service. Callers must retain their discovery coverage.

- CPU is cores and memory is bytes. Capacity and allocatable come from Nodes; requests and configured
  limits come from Pod specs. Requests use application sums, ordered native sidecar/init peaks,
  Pod-level overrides and overhead. Finished Pods remain in the inventory but not active totals.
  Pending unscheduled requests remain separate from scheduled requests. Configured limits are not
  an actual cap for containers with omitted limits; their counts are retained separately.
- The accounting basis is the declared spec. It does not read live cgroup limits or infer in-place
  resize allocation from status. Allocatable minus spec requests is not guaranteed scheduler headroom:
  placement, quotas, taints and actual resize state still matter.
- Metrics API CPU is an average over its supplied window; memory is working set. Keep timestamp/window
  per sample. Missing, forbidden or malformed metrics never become a successful zero usage value.
  Pod metrics are checked against current Pod identity, creation time and container coverage.
- Lists page under one deadline and explicit item budgets. Truncation and later-page errors preserve
  earlier evidence as partial. Each totals block covers only acquired evidence; coverage is mandatory
  when presenting it as a cluster or Service total. This is not an atomic multi-resource snapshot.
- RBAC denial is an expected `permission_denied` acquisition result, not a cluster health finding.
  A forbidden cluster view never falls back to namespace data under a cluster label. Independent
  namespace collection can still succeed. Metrics absence and resource-read failure are separate.
- Reads are sequential under the root transport's connection limits and response-byte bounds.
  Caller cancellation propagates its original reason; an expired budget records timeout coverage.
  Results carry safe source/category/code diagnostics, never raw transport errors or credentials.

The current scope is CPU and memory on Kubernetes. No GPU, disk, scheduler simulation or business
resource discovery is inferred. Common's Resource model remains platform-neutral.

Accounting references: [Kubernetes Pod resources](https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/),
[sidecar lifecycle and resource accounting](https://kubernetes.io/docs/concepts/workloads/pods/sidecar-containers/),
and [Metrics API semantics](https://kubernetes.io/docs/tasks/debug/debug-cluster/resource-metrics-pipeline/).
