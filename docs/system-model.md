# 系统对象与身份

本主题说明产品、代码组件与运行服务如何关联，以及身份的唯一性范围。全仓职责与执行主流程见
[开发约定](../AGENTS.md)；静态声明不证明目标已部署或可访问。

## 对象关系

| 概念 | 语义 |
|---|---|
| **Forge** | 托管代码 Repository 的系统，例如 GitHub、GitLab 或企业内部代码平台。 |
| **PullRequest** | Repository 内的代码合入提案，身份为目标 Repository 与仓内编号；状态和 revision 是可选平台事实。契约见 [PullRequest](pull-request.md)。 |
| **Repository** | Forge 下的一份代码仓，以 Forge 与仓内路径共同确定；可以包含一个或多个 Component。可选字符串 `forked_from`（TypeScript：`forkedFrom`）记录同一 Forge 实例内直接父仓的 path，不参与身份；它不表示本地 checkout 或 fork 网络根仓。省略/空值表示未声明，查询失败由查询方单独表达；跨 Forge 的导入、镜像来源由后续独立标记表达。 |
| **Product** | 面向业务的产品身份；可以由多个 Component 组成，公共 Component 也可以服务多个 Product，归属关系由 registry 维护。 |
| **Component** | 代码 Repository 内一个可独立构建或发布的稳定组件；可选声明使用的 Tool 与一个逻辑 Depot，不改变 Repository + name 身份。一个 Repository 可以包含多个 Component；不会运行为 Workload 的 Component 不产生 Service。 |
| **Tool / Depot / Bin** | Component 的开发工具与可恢复配套内容声明：Tool 描述使用的工具，Depot 聚合多个具有仓库相对路径的 Bin；安装、构建、使用保护与回收归消费方。见 [Component Depot](component-depot.md)。 |
| **Environment** | 一组部署与运行所共享的命名环境，可包含 Host；它识别证据产生在哪里，凭据和发布策略归部署领域。 |
| **Host** | Environment 的可选组成部分，描述主机身份及访问方式；在 Kubernetes 环境中是集群操作入口，不能代替目标 Pod 的执行事实。见 [Environment](environment.md)。 |
| **Service** | 一个 Component 在某个 Environment 中具名的运行体现；由 service name、Component 与 Environment 共同确定，不等同于代码 Repository 或具体平台 Workload。 |
| **Workload** | Service 在运行平台上的具名承载单元；Service 声明零个或多个 Workload 引用，具体实例及当前状态在执行期查询。它不是一次 Deployment 发布记录，也不是 perf 领域的请求负载模型。 |
| **Operation** | Service 对外提供的一项具名能力，也是 Case 所绑定的服务能力；不包含特定传输协议的访问细节。 |
| **HttpOperation** | `Operation` 的 HTTP 子类，以 method 与 path 表达协议契约；base URL 仍属于运行态 Service。 |
| **Deployment** | 把某个 Component 发布到 Environment、从而创建或更新 Service 的一次部署记录；同一 Service 可以经历多次 Deployment。 |
| **Deployer** | 执行 Deployment 的协议无关端口；Helm、Docker 等是具体实现。 |
| **Knowledge** | Product / Service 持有的离线业务知识，描述概念、流程和专题，不等于执行计划或运行事实。 |
| **Resource** | Service 持续管理或使用的资源集合声明；运行时实例和数量由独立快照记录。 |

Service 是逻辑运行服务，不与 Kubernetes Service 或其它平台资源一一对应。
Service.workloads 声明运行承载；映射变化不改变逻辑 Service 身份。实例发现与访问边界见
[Workload](workload.md)，持续管理的资源集合与运行快照见 [Resource](resource.md)。
Product / Service 的说明和离线业务知识见 [Knowledge](knowledge.md)。

## Identity 与序列化

Identity 是公共身份协议，提供返回字符串的 `id()`（Go：`ID()`）。具体模型定义标识格式与唯一性范围；
同一业务身份返回相同标识，描述性元数据不参与标识。
Repository 实现 Identity，标识为 `forge.name + "/" + path`，例如 `github/compforge/quality-harness`；
`forked_from` 不参与标识。该标识由当前 Forge 名称与仓库路径派生，改名或迁移会改变标识，
不承诺平台原生 ID 的永久稳定性。TypeScript 的 `id()` 可委托公共函数 `repositoryId(this)`；
JSON 只保存数据字段，消费方加载后恢复方法。
Service 与 Operation 也实现 Identity：Service 的图内 ID 使用服务名，Operation 使用所属服务名与操作名。
这是环境内 catalog 的局部标识，完整 Service 身份仍包含 Component 与 Environment；环境范围由图或运行记录提供。
Operation 必须声明 service_name（TypeScript：serviceName）；TypeScript 提供具体 Service / Operation 类以统一 id() 实现。

Python 与 TypeScript 共用关系与落盘契约，语言 API 保持各自习惯；消费方扩展业务能力。
Service / Operation 的 ID 格式、名称转义、所有权校验和环境作用域见 [Graph](graph.md#scope-and-identity)。

## 声明的服务关系

TypeScript Service 的可选 upstream 引用调用当前 Service 的标准名称，由消费方目录解析。
省略表示关系未声明，空数组表示声明无上游；该关系不改变身份，也不表示某次请求实际经过的路径。

Common Graph 用带 kind 的 Node / Relation 表达 Service、Operation 和调用关系。
拓扑声明与请求执行事实独立，结构、查询和证据关联边界见 [Graph](graph.md)。
