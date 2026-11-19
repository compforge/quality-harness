# 系统描述

系统声明把代码仓、产品和运行服务联系起来，为选择测试目标与关联证据提供共同身份。
common 保存这些中立契约；仓库发现、构建、部署、现场访问和业务目录由消费方负责。
本主题描述对象及其关系；实例发现和实测状态见 [运行目标](runtime.md)，执行与质量判断分别见
[执行](execution.md) 和 [评估](evaluation.md)。

## 对象与关系

```text
Forge → Repository → Component ← Product 的组件关联
                         ↓ 在 Environment 中具名运行
                       Service → Operation
                         ├─ Workload 引用 → 执行期发现实例
                         └─ manages / uses → Resource 集合

Graph 声明 Service / Operation 及其关系；Knowledge 描述产品与服务的业务含义。
```

Product 可以由多个 Component 组成，公共 Component 也可以服务多个 Product；关联由 registry 维护。
不会运行为 Workload 的 Component 不产生 Service。构建或部署是消费方执行的过程，声明本身不证明已部署或可访问。

| 概念 | 语义 |
|---|---|
| Forge | 托管 Repository 的系统，例如 GitHub、GitLab 或企业内部代码平台。 |
| Repository | Forge 下的一份代码仓，包含一个或多个 Component。 |
| Component | Repository 内一个可独立构建或发布的稳定组件。 |
| Product | 面向业务的产品身份，通过 registry 关联 Component。 |
| Service | Component 在 Environment 中具名的运行体现，完整身份包含三者。 |
| Operation | Service 对外提供的具名能力，Case 可绑定这项能力。 |
| Deployment / Deployer | Deployment 记录一次 Component 到 Environment 的部署；Deployer 是其协议无关执行端口，Helm、Docker 等由具体实现承接。 |

同一个 Service 可以经历多次 Deployment。Service 与 Kubernetes Service 等平台资源的映射由
[Workload 声明](runtime.md#workload) 承接；Service 的逻辑身份不随 Pod 重建改变。

## Identity 与序列化

Identity 是公共身份协议，提供返回字符串的 `id()`（Go：`ID()`）。具体模型定义标识格式与唯一性范围；
同一业务身份返回相同标识，描述性元数据不参与标识。
Repository 实现 Identity，标识为 `forge.name + "/" + path`，例如 `github/compforge/quality-harness`；
`forked_from` 不参与标识。该标识由当前 Forge 名称与仓库路径派生，改名或迁移会改变标识，
不承诺平台原生 ID 的永久稳定性。TypeScript 提供具体的 `Repository` 类，
消费方可用 `new Repository(snapshot)` 从 JSON 字段恢复实例并调用 `id()`。
Service 与 Operation 也实现 Identity：Service 的图内 ID 使用服务名，Operation 使用所属服务名与操作名。
这是环境内 catalog 的局部标识，完整 Service 身份仍包含 Component 与 Environment；环境范围由图或运行记录提供。
Operation 必须声明 service_name（TypeScript：serviceName）；TypeScript 提供具体 Service / Operation 类以统一 id() 实现。

Python 与 TypeScript 共用关系与落盘契约，语言 API 保持各自习惯；消费方扩展业务能力。
Service / Operation 的 ID 格式、名称转义、所有权校验和环境作用域见 [Graph 的身份范围](#scope-and-identity)。

## Repository

Repository 的身份由 Forge 与仓内路径共同确定，不表示本地 checkout。
可选字符串 `forked_from`（TypeScript：`forkedFrom`）记录同一 Forge 实例内直接父仓的 path，
不参与身份，也不表示 fork 网络根仓。省略或空值表示未声明，查询失败由查询方单独表达；
跨 Forge 的导入、镜像来源由后续独立标记表达。

### PullRequest

PullRequest 是 Repository 内的一次代码合入提案，统一表示 GitHub PR 与 GitLab MR。common 保存身份及平台事实；Forge 访问、发现分页、评论线程和合并就绪判断属于仓库工具包，观察水位、变化比较和 Input 接纳属于消费方。

#### 身份与事实

`repository` 是编号所属的**目标仓库**，复用现有 Repository；`number` 是仓内正整数编号（GitLab `iid`），不是平台全局数据库 ID。`id()` / `ID()` 为 `Repository.id() + "#" + number`；TypeScript 用 `pullRequestId` 从 Repository 身份字段推导。平台适配负责校验编号及必需身份字段。

同一个 PR 追加提交、force-push、关闭/重开、合并后仍是同一身份。源仓、分支、目标仓 fork 来源均不参与比较；相同编号但 Forge 或仓库不同的 PR 不相等。Python 的相等和哈希按身份比较，Go 用 `SameIdentity`，TypeScript 比较 `pullRequestId`。对象相等不能用于判断快照是否发生变化，消费方应明确比较需要观察的事实字段。

可选事实包括 title、url、state、source_repository、source_branch、target_branch、head_sha、base_sha、updated_at、merged_at。TypeScript 使用 camelCase；Go 用惯用字段名，JSON/YAML 使用 snake_case。源仓也是 Repository；源仓缺失时不推断为目标仓。`head_sha` 是提案源分支 tip；`base_sha` 是平台报告的比较基线，不承诺是目标分支当前 tip。两者都不作为身份。

`state` 只有 `open`、`merged`、`closed`，其中 closed 表示未合并而关闭。未获取的 state 在 Python 为 None、TypeScript 为 undefined、Go 为零值；不能当作 closed。`updated_at` / `merged_at` 是平台 ISO 8601 时间，缺失不证明没有更新或合并；合并终态读取 state。采集时间与观测完整性留在调用方的观测记录中。

TypeScript 的 `RepositoryKey` / `PullRequestKey` 是仅需身份字段的结构化引用；`RepositorySnapshot` / `PullRequestSnapshot` 从同一对象模型派生出可序列化的事实形状，去掉 `id()`，包括 PR 内嵌的目标和来源 Repository。它们不是另一套对象身份，`repositoryId` / `pullRequestId` 对引用、快照和进程内对象给出同一结果。读取客户端可直接返回快照，消费方无需构造占位方法或自行维护字段投影。Python 数据类与 Go struct 已自然分离序列化字段和方法，共享身份及 JSON 事实语义不变。

#### 消费边界

common 不持有 token、HTTP 客户端、查询游标或轮询策略，也不解释业务授权、通知或合并决策。它不提供平台响应解析器；访问实现先校验远端响应，再构造公共对象。合并可行性和评审线程属于按需读取的观察事实，不能把共享身份对象当成持续有效的合并许可。

三个语言实现共同消费 `conformance/common/pull-requests.json`，验证目标仓归属、fork 与 revision 不改变身份、merged/closed/未知状态的区别，以及事实序列化往返。

#### 合并事实的独立维度

`state` 是 open / closed / merged 生命周期，只有平台已确认合并才是 merged。
`conflictState`（Python/Go wire 为 `conflict_state`）只描述代码冲突：none / conflicted / unknown。
`mergeReadiness`（wire 为 `merge_readiness`）描述平台当时报告的合并条件：ready、conflict、
discussions_unresolved、ci_blocked、needs_approval、draft、behind、blocked 或 unknown。
无冲突不能推出 ready，ready 不能推出 merged，也不代表调用者有合并权限。遗漏字段仍是未知。
这些事实不参与身份；平台读取、分页、游标和状态映射由 repocli 负责，common 不实现网络或扫描算法。

## Component

Component 可声明使用的 Tool 与一个逻辑 Depot；这些元数据不改变 Repository + name 身份。

### 工具与配套内容

A Component may declare the development tools it uses and one logical Depot of recoverable
supporting content. These are optional metadata alongside language and description; Component
identity remains Repository plus name. Common describes the contract without inspecting a checkout.

```text
Component: web
  language: typescript
  tools: [{name: pnpm, version: ">=9 <10"}, {name: vite}]
  depot:
    bins:
      - {path: node_modules, kind: dependencies}
      - {path: apps/web/dist, kind: output}
      - {path: apps/web/.vite, kind: cache}
```

#### Declarations

- **Tool** names a development tool, including package managers and build tools. Its optional
  version is a declared version or constraint, not the version installed on a machine. It does
  not select a command or executable; discovery evidence and conflict resolution belong to consumers.
- **Depot** groups a Component's recoverable supporting content. Within a selected checkout a
  Component has zero or one logical Depot; the same declaration can be resolved in several checkouts.
  Removing physical contents does not remove the declaration. An empty `bins` list is valid.
- **Bin** describes one content unit, normally a directory, with `path` and `kind`.
  `dependencies`, `output` and `cache` describe purpose, not current existence, readiness or
  cleanup eligibility. One preparation operation may restore multiple bins.

Paths are normalized POSIX paths relative to the repository checkout root, without a leading slash,
trailing slash, empty segment, `.` or `..` segment. They are literal locations, not globs. A workspace
installation may be outside the Component's own directory while remaining within its checkout.
A Bin path is unique within its Depot; distinct Components may declare the same path. Shared and
nested physical locations require consumer-owned deduplication and coordinated usage protection.
Global caches outside the checkout are not represented by this contract.

Omitted `tools` means undeclared; an empty list declares no tools. Omitted `depot` means no Depot
was declared, not proof that the checkout contains nothing recoverable. Consumers report discovery
coverage separately. The declarations are trusted typed data, not a filesystem validation API.
Consumers must validate external input and resolve filesystem containment, including symlinks,
before executing any operation.

#### Ownership and lifecycle

Common supplies Tool, Depot and Bin declarations and Component associations. It performs no I/O
and has no installation, build or deletion methods. RepoKit or another consumer owns discovery,
absolute path resolution, restoration recipes, installed versions, size/status observations,
locks, active usage and reclamation. Tool or language names alone do not prove a recovery recipe.

A Bin declaration is not deletion authorization. Consumers check tracked files, recovery evidence,
active use and shared locations before reclamation. The selected checkout disambiguates identical
relative paths in different worktrees. Reclaiming one logical Depot must not invalidate another
active consumer of a shared directory.

Validation consumers select the bins that a check reads. Dependency deletion or relevant content
changes can invalidate reuse, while output/cache writes performed by the check itself must not
create an unconditional invalidation loop. Bin kind alone does not determine input/output roles.

#### Language APIs and compatibility

Public names are `Tool`, `Depot` and `Bin`; Component fields are `tools` and `depot` (Go: `Tools`,
`Depot`). TypeScript uses readonly interfaces. Python uses frozen dataclasses, optional tuples
and keyword-only Component metadata, excluded from equality and hashing.

Go uses `Tools *[]Tool` and `Depot *Depot`. A nil Tools pointer means undeclared; a pointer to
an empty non-nil slice serializes as `[]`. Depot's Bins is a required slice; use an empty non-nil
slice when explicitly declaring an empty Depot. The pointers preserve Component comparability,
but `==` and map keys compare all fields, including metadata pointer addresses. Use `SameIdentity`
for identity comparisons; identity indexes must use Repository's forge/path plus Component name.

JSON omits undeclared metadata; the shared
`conformance/common/component-depots.json` fixture covers undeclared metadata, Node/Python, shared-location
and empty declarations in all three SDKs. Source identity is independent of this metadata.

## Service 与 Operation

Service 对外提供 Operation，协议访问细节由具体能力和运行绑定承接。
`HttpOperation` 用 method/path 表达 HTTP 契约，base URL 属于运行态 Service；`SSEOperation` 是
其流式子类，`response_mode=sse`，仍沿用相同身份规则。业务成功由消费方 Judge 判定。

TypeScript Service 的可选 upstream 引用调用当前 Service 的标准名称，由消费方目录解析。
省略表示关系未声明，空数组表示声明无上游；该关系不改变身份，也不表示某次请求实际经过的路径。

Common Graph 用带 kind 的 Node / Relation 表达 Service、Operation 和调用关系。
Service 可通过 `operations` 贡献 Operation；Operation 的 `calls` / `may_calls`（TypeScript：`mayCalls`）
引用下游 Operation ID，`observes` 声明该 Operation 的观测指标。`graph_from_services` / `graphFromServices`
将同一 Environment 的声明组装为 Graph，校验 owner、重复节点、悬空引用与冲突边；允许循环。
Service.operations 是声明源，Graph 和 Observation model 按需在内存组装，无需维护独立声明文件。
运行记录保存当次使用的拓扑、查询计划和观测事实，供离线复查；报告视图由这些事实临时生成。

### Observe

`ObservationDefinition` 定义测量值及测量方式，例如搜索完成速率的单位、分类和查询表达式。
将声明放在 Service、Operation 或 Workload 的 `observes` 中，该对象就是观测目标。
同一份声明可以复用。Workload 名称限定在所属 Service 内，查询表达式决定实际测量范围。
声明模型由 Common 提供，见 [TypeScript](../sdks/typescript/common/src/observe.ts) 和 [Python](../sdks/python/common/harness_common/observe.py)。

对 MetricDB 观测，Toolbox 按后端选择声明、绑定参数。每个指标在选定后端应有一个匹配的声明。
TypeScript Toolbox 还能根据 Kubernetes Workload 自动生成 CPU、内存用量和配额的观测，并按 Service 汇总。
Perf 使用实际运行的时间窗口查询 MetricDB，保存结果及其目标，供报告离线展示。

拓扑声明与请求执行事实独立，结构、查询和证据关联边界见 [Graph](#graph)。

## Graph

Common describes offline topology with `Graph`, `Node(id, kind)` and
`Relation(id, source, target, kind)`. Python and TypeScript implement the same wire
contract and consume `conformance/common/graphs.json`. Kinds are nonempty strings;
consumers can introduce additional kinds without introducing node subclasses.

### Open kinds

`NodeKind` and `RelationKind` expose built-in names while accepting custom nonempty
strings. Python uses open `StrEnum` values: `NodeKind.SERVICE`, `NodeKind.OPERATION`,
`RelationKind.BELONGS_TO`, `RelationKind.CALLS`, `RelationKind.MAY_CALL`; `NodeKind("model")` and
`RelationKind("uses")` preserve unknown values without registering new members.
Python Node/Relation constructors require the corresponding enum type. JSON decoding
constructs enum values at the boundary, including unknown custom kinds.

TypeScript exports frozen constants `NodeKind.Service`, `NodeKind.Operation`,
`RelationKind.BelongsTo`, `RelationKind.Calls`, `RelationKind.MayCall` and same-named open string types.
Their literal-plus-string unions retain editor completion while allowing custom kinds.

Both languages serialize kinds as the same plain strings. Unknown kinds roundtrip
unchanged, and known strings/constants follow identical validation rules. No enum
registration or wire schema change is required to extend the vocabulary.

### Properties

Node and Relation accept optional `properties` JSON objects. Attributes do not participate in
IDs; changing an observation does not create a different operation or relationship. Common
validates finite JSON values, copies them into immutable snapshots, and returns detached data
from serialization. Empty properties are omitted, preserving existing graph files.

Python uses `Node(id, kind, properties={...})` and `Relation(source, target, kind, properties={...})`.
TypeScript accepts `properties` on nodes/relations and as the optional fourth `createRelation`
argument. Domain consumers own property names and semantics; Common does not interpret HTTP,
metrics or confidence. Perf enriches copied nodes under `properties.perf` in its run snapshot;
static declarations remain unchanged.

### Scope and identity

A Graph is one immutable snapshot in a named environment. It has no fixed root.
Optional `source` and `version` identify where the declaration came from and which
revision it describes. They are provenance, not evidence that the declaration is
complete or matches current traffic. The wire format carries `schema_version: 1`;
`version` is the consumer's topology revision, not the format version.

Nodes use the IDs supplied by models implementing `Identity.id()`. Graph does not
own another identity algorithm or export service/operation ID helpers.

- `Service.id()`: `service:<escaped-name>`, scoped to one environment's service catalog.
- `Operation.id()`: `operation:<escaped-service>/<escaped-name>`, using its required
  `service_name` (Python) / `serviceName` (TypeScript) and `name`.
- `HttpOperation` inherits the identity; method/path are transport details.

Each segment uses UTF-8 percent encoding (only RFC 3986 unreserved characters remain
literal). Names must be nonempty when requesting an ID. Projects must keep service
names unique within the environment catalog. These local IDs are not global Service
identity: Component and Environment remain part of Service's full runtime identity,
and the graph/run supplies that scope. Renaming changes the local ID.

Python Service and Operation implement the existing Identity protocol. TypeScript
Service, Operation and HttpOperation are concrete classes implementing Identity;
construct them from their declaration fields, including when restoring JSON.
Methods are not persisted. OperationRun's Service is the runtime binding, and the
operation's service name must match it. Python checks this at construction. In Perf's
OperationRun record, the enclosing service supplies the owner when reconstructing the
operation; it is not duplicated inside the operation payload.

### Relations and validation

| Kind | Direction | Contract |
| --- | --- | --- |
| `belongs_to` | operation → service | Every operation has exactly one owner. |
| `calls` | operation → operation | Required call on the declared normal execution path. |
| `may_call` | operation → operation | Conditional call within the declared scope. |

Both call kinds allow cycles and shared callees. Required describes the normal path,
not a guarantee that a failed or cancelled request reached the callee. Projects declare
scope through their experiment/case binding; Common does not infer business branches.
The wire spelling is `calls`; reports display it as `call`.

Node IDs must be unique; relation endpoints must exist. Relations are unique by
`(source, kind, target)`. The relation ID is the compact JSON encoding of that triple,
with Unicode preserved; callers use Python `Relation(source, target, kind)` or
TypeScript `createRelation(source, target, kind)` to generate it. Loading rejects
mismatched IDs. Repeated calls or retries do not create parallel static edges;
counts, timings and individual spans belong to independent observations.

Unknown kinds are retained; built-in kinds keep their endpoint/ownership rules.
Graph construction validates the complete input atomically. Build node and relation
collections first, then construct a Graph; there is no separate Builder lifecycle.
An empty graph is valid and does not assert that the environment has no services.

### Build and query

```python
from harness_common import (
    Component, Environment, Forge, Graph, Node, NodeKind, Operation,
    Relation, RelationKind, Repository, Service,
)

environment = Environment("qa")
component = Component(Repository(Forge("github"), "example/product"), "backend")
chat_service = Service("chat-server", component, environment)
memory_service = Service("memory-server", component, environment)
chat = Operation("chat", service_name=chat_service.name)
retrieve = Operation("retrieve", service_name=memory_service.name)
graph = Graph(
    environment=environment.name, source="project:topology", version="revision-1",
    nodes=(Node(chat_service.id(), NodeKind.SERVICE),
           Node(memory_service.id(), NodeKind.SERVICE),
           Node(chat.id(), NodeKind.OPERATION), Node(retrieve.id(), NodeKind.OPERATION)),
    relations=(Relation(chat.id(), chat_service.id(), RelationKind.BELONGS_TO),
               Relation(retrieve.id(), memory_service.id(), RelationKind.BELONGS_TO),
               Relation(chat.id(), retrieve.id(), RelationKind.CALLS)),
)
reachable = graph.reachable_from(chat.id(), kind=RelationKind.CALLS)
view = graph.subgraph(node.id for node in reachable)
saved = graph.to_dict()
restored = Graph.from_dict(saved)
```

TypeScript exposes `new Graph({ environment, nodes, relations, source?, version? })`,
`node`, `relationsFrom`, `relationsTo`, `reachableFrom`, `subgraph`, `toJSON` and
`Graph.fromJSON`. Python uses the corresponding snake_case methods. Graphs copy
input collections and retain immutable nodes/relations; serialized results are detached.

Queries preserve declaration order. Reachability includes the root, visits each node
once and terminates on cycles. A missing root or requested subgraph node is an error;
a missing node lookup returns None/undefined, and missing-node adjacency is empty.
The `calls` property returns both `calls` and `may_call` operation edges, excluding
ownership and custom relations. Single-kind traversal remains available with
`reachable_from(..., kind=...)` / `reachableFrom(..., kind)`.
`subgraph` includes selected operations' service owners and all edges between retained
nodes, without pulling in unrelated operations belonging to those services.

### Evidence and ownership

The project owns topology declarations. Common owns the structure and deterministic
queries, not discovery, live access, metrics, diagnosis or a graph database.
Case describes test intent; it does not contain a topology. OperationRun is an actual
invocation and can be associated with an operation node through `operation.id()`.

Metrics are collected independently and later joined by node or relation ID, environment
and observation window. Missing metrics remain unobserved, not zero. Consumers save the
Graph snapshot alongside run evidence so analysis can be reproduced offline. Declared
relationships and trace-observed relationships need separately identified sources and
coverage; neither a path nor correlated metrics alone proves causality. Domain consumers
construct operations with the owning service name; Perf scheduling, metrics and evaluation
behavior belong to the Perf domain.

## Knowledge

Service 与 Product 可用可选的 description 提供简短介绍，并声明离线业务知识。
TypeScript 通过 knowledge 统一组织结构化内容；Python 与 Go 的已有说明字段保持各自 API。
Service 聚焦自身职责与交接边界，Product 承载跨服务的端到端模型；内容由消费项目维护并随版本交付。
这些元数据不参与身份，也不构成可执行计划或现场观测。Python 的相等与哈希忽略它们；Go Product
通过 SameIdentity 比较业务身份，结构体相等仍会比较所有字段。未提供表示知识缺省，Markdown 正文
由消费方保留；公共层不执行其中的命令。

### 内容模型与输出

TypeScript common 的 Product 与 Service 通过可选的 `knowledge` 贡献 `Knowledge`：Concept 解释核心对象、关系与约束，
Workflow 用具名 Step 描述业务过程，Article 用 Section 展开专题，Dictionary 保存可逐条查询的类型化词条。
知识是消费方维护的离线声明，拥有可引用的内容身份，不改变产品或服务身份，也不证明目标环境的运行状态。
Step 的 parentId 只表达包含关系，同级顺序由数组决定；词典允许同码多定义，稳定引用由词条 ID 承担。
知识内容接口统一继承 Markdown，以 `markdown(): string` 提供无 I/O 的文本表示；KnowledgeSource 仅记录来源。
common 提供结构约束与确定性 Markdown 转换函数，声明方法可委托这些函数；父对象调用子对象的 `markdown()` 组合输出，领域词条的额外字段由 Entry 自身展示。
JSON 只保留内容字段，消费方加载后须为各层知识对象补齐方法，再作为可调用的知识声明暴露。
发行、内容加载、查询、搜索与索引由消费方负责，词典定义来源版本独立于其分发版本。
