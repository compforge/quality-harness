# Diagnosis Harness

> 设计草案，尚未实现。先以 Python 实现和真实消费方沉淀接口；跨语言 spec、schema 与
> conformance 暂缓。本文描述拟议模型，不表示仓库已具备这些能力。

## 理念 / 概念

Diagnosis Harness 沉淀面向 Service 的可观测方法：服务声明自己能提供哪些方面的证据，
采集入口根据当前问题选择所需方面与服务，并返回可追溯结果。人或 AI 根据已取得的证据，
决定还缺哪方面信息，再发起下一次采集。

例如用户提供 conversation ID，希望知道请求对应哪些 trace、运行在哪个 Pod。Chat 的贡献
解释会话与 trace 的业务关系，Sandbox 服务的贡献解释会话与运行载体的关系；通用采集层
组织查询和汇总，业务表结构与配置解析保留在各自贡献中。

Diagnosis 是独立领域 SDK，拟使用 Python 包名 `diagnosis_harness`。它复用
[公共内核](kernel.md) 的 Service、Environment 和执行资源语义，直接采集现场时无需构造 Case。
首版聚焦证据采集；证据是否支持故障假设、是否达到验收标准，由消费方解释。

### 核心模型

| 概念 | 职责 |
|---|---|
| Service | 被观察的逻辑运行服务，复用 `harness_common.Service`；环境与 workload 映射由部署配置提供。 |
| Facet | 一个观测方面，定义输入、贡献协议与采集流程，例如 data、inspect、store、log。 |
| Contribution | Service 为某个 Facet 提供的查询实现或目标声明，拥有业务语义。 |
| Catalog | 注册和发现 Service 的贡献，支持指定服务或全部适用服务的选择。 |
| Evidence | 具有来源信息的采集证据，包含事实、记录、关联关系及采集结果状态。 |

主线是：**Service 按 Facet 注册 Contribution，采集入口选择 Facet 和 Service，汇总 Evidence。**

Facet 是诊断领域的观测方面，与 Case 中用于分类的 facets 分属不同语境。公共 Service 保持
中立，诊断能力注册由 diagnosis 的 Catalog 持有；无需给 common.Service 增加诊断专属字段。

### Facet 的贡献深度

| Facet | 回答的问题 | Service 的贡献 |
|---|---|---|
| data | 一个业务 ID 关联哪些数据与运行对象？ | 接受的 Identity 类型、固定只读查询、记录与已证明的关系。 |
| inspect | 服务当前运行与配置情况如何？ | workload 引用、配置来源、运行时信息的投影规则。 |
| store | 服务实际使用哪些存储，访问与运行情况如何？ | Store 的身份、有效 DataSource，以及私有配置到连接配置的投影。 |
| log | 指定范围内有哪些相关日志？ | 日志目标和业务筛选语义。 |

每个 Facet 拥有自己的窄协议。DataContribution 接受业务 Identity 并返回数据，StoreContribution
提供数据源，声明式的目标贡献也可以交给通用采集器执行。无需把这些差异压成一个接收任意字典的
`execute()` 接口。某个 Service 可以只支持其中一部分 Facet。

## 流程

```text
问题 / 已有证据
  → 调用方选择 Facet、输入与 Service 范围
  → Catalog 发现适用 Contribution
  → Contribution 借用执行上下文访问数据
  → 按 Service 汇总 Evidence
  → 调用方解释结果并选择下一次采集
```

Catalog 的发现与描述只读取声明。实际执行时才解析目标、创建连接或访问远端。指定 Service
时只在指定范围内采集；选择全部时遍历该 Facet 的适用贡献，缺少某项能力应明确可见。

### Data Facet

Data 作为首个完整实现，用业务 ID 汇集场景校准贡献协议与结果表达：

1. 输入带类型的 Identity；只有原始字符串时以 `biz_id` 交给贡献解释。
2. 从选定 Service 中找到接受该类型的贡献，执行固定只读查询。
3. 各贡献返回记录，以及查询已证明的关联 Identity。
4. 若调用方开启关系扩展，将新 Identity 交给选定范围内适用的贡献。
5. 汇总每次查询结果，保留其服务、输入与证据来源。

关系扩展默认关闭。开启时，在一次采集中对每个 Service 与 Identity 组合去重，避免重复查询
同一个对象及循环关系造成的重复执行。去重不能限制无限产生新 Identity 的查询，因此贡献仍须
限定自身返回范围并如实标记截断。关系扩展不能隐式扩大用户选择的环境或 Service 范围。

调用方已经知道规范 Identity 时可以直接查询。某个 Service 查不到记录或查询失败，不影响其他
Service 已取得的证据；执行取消向上传播，由根调用方收口资源生命周期。

### Evidence 的表达

Data 内部用 Identity 表达“类型 + 值”，用以下三种事实表达结果：

- ValueFact：一个命名事实，例如服务返回的状态摘要。
- RecordFact：带稳定键的业务记录。
- RelationFact：证据证明的两个 Identity 之间的关系。

事实内容属于贡献方，通用层保留 Facet、Service、输入 Identity、采集时间和数据来源，
使消费方能够追溯结果。Evidence 对应公共内核中的观察事实及其来源；采集成功不代表业务执行成功。

采集结果区分成功、空结果、部分结果与失败。失败和截断须提供原因；unsupported 属于能力选择
结果，不能冒充查到空数据。具体 Python 类型和字段随实际接入调整，暂不冻结序列化协议。

## 关键设计

### 诊断领域与基础设施各有归属

| 所属层 | 负责内容 |
|---|---|
| harness_common | Service、Environment 等公共身份，以及 ClientProvider、ClientManager 等执行级资源原语。 |
| harness_toolbox | MySQL、OpenSearch、Kubernetes、日志与 Transport 等具体访问机制。 |
| diagnosis_harness | Facet 协议、贡献发现、采集流程与 Evidence 组织。 |
| 产品适配 / Service 贡献 | 业务 schema、固定查询、ID 关系、有效数据源投影和证据语义。 |
| 宿主 / 部署领域 | 环境与服务选择、凭据、授权、根执行生命周期，以及诊断入口。 |

具体产品配置由 Service 贡献解释，toolbox 消费已经投影好的访问配置。SQL、索引名、业务日志
筛选规则不会进入通用 Catalog 或客户端。连接参数、认证信息只进入执行态，证据返回脱敏来源。

根调用方持有 ClientManager，嵌套采集只借用 ClientProvider；连接复用与释放沿用
[toolbox](toolbox.md) 的契约。读取去重优先复用现有 DataLoader，diagnosis 仅拥有业务 Identity
的遍历去重，两者分别解决底层读取复用和业务查询编排问题。

### 证据与诊断判断分开

首版提供可重用的采集结果，消费方负责解释。既有 trace、trajectory 等领域分析维持自己的
模型和判据，由宿主组合这些 SDK；diagnosis 不反向导入兄弟领域 SDK。未来接入确定性分析时，
仍应遵循公共内核中 Observation 与 Finding / Evaluation 的分离约定。

### Python 实现范围

拟在 `sdks/python/` 按现有独立工程约定组织 diagnosis SDK，先完成 Catalog、Evidence 与 Data
Facet，并提供两个中立 Service 的可运行示例。以 chat / sandbox 作为首个产品接入场景，验证
服务选择、Identity 路由、关系扩展、失败隔离、取消传播和客户端复用。

inspect、store、log 作为已识别的观测方面，待真实接入时再确定具体协议。Doctor 的成熟采集与
Service contribution 设计用于校准语义，CLI、Profile 与 Plugin 分发继续归宿主。

当前暂缓项：

- Coverage 与 Budget 公共模型；具体访问继续遵守客户端超时、数量限制和截断约定。
- Probe / Detector 统一引擎、Finding 与报告编排。
- 多语言实现、跨语言 spec、schema 与 conformance；先通过 Python 消费方沉淀接口。

本设计不改变现有 SDK 的公共契约，也不表示 as-ops 或 Doctor 已经完成接入。
