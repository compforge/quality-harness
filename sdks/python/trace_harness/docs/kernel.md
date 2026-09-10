# Trace Harness 内核

Trace Harness 把物理 span 映射成逻辑事件，由业务贡献事实、度量与规则，框架执行数据准备、分析和呈现。
单条与批量共用这套模型；[加载](load.md)、[单条分析](single.md)、[批量分析](batch.md)各自展开具体机制。

## 概念与主流程

```text
Source ── select ──▶ Dataset ── detect ──▶ Findings
                        └─ 按需准备 tree、Fact、Measurement
```

| 概念 | 职责 |
|---|---|
| Source | 遥测后端适配，异步选择 trace、读取骨架及证据 |
| Dataset | 固定 trace 成员、查询条件与可复用证据，不保存运行结论 |
| Span | 带存储身份的物理观测，保留原始证据与关联 |
| Node / tree | 一个逻辑事件对应一到多个 span；平 Node 集与只读关系索引 |
| Fact | 业务映射与转换所得的命名事实 |
| Measurement | 独立量化结果，包含适用性、状态、数值与证据 |
| Finding | 带作用域和证据的观察；异常与结构信息均可表达 |
| AnalysisContext | 当前 trace 的分析状态与逻辑数据访问入口 |

`TraceHarness.open` 创建资源作用域，`select` 固定 Dataset，`tree` 取得有生命周期的分析上下文。
`detect` 流式遍历成员，写出独立 Run。相同 Dataset 可以执行不同的分析，不复用先前运行的 findings。
Measurement 不挂在 Node 上，通过 `context.measure(node, name)` 请求。

## 业务扩展与执行边界

业务通过 `TraceContributions` 显式装配扩展，不依赖进程全局注册。

| 业务贡献 | 框架执行 |
|---|---|
| KindSpec 的分类、关联、字段依赖与 build | 读取结构元数据、assemble、维护 Node 身份与父子边 |
| prepare_spans 的结构修正规则 | 在骨架准备后统一调用，业务不另行读取远端数据 |
| FactProducer 的证据引用、依赖和纯计算 | 合并读取、依赖解析、计算和缓存 |
| FactTransform 的 fact 依赖与纯转换 | 准备依赖后物化输出，检查重复生产者与循环 |
| Measurer 的依赖、计算范围与算法 | 按真实范围执行一次并保存 Measurement |
| Detector 的 id、requires 和判读逻辑 | 校验依赖并执行，后序逐 Node / 每 Dataset 调用，隔离结果与生命周期 |
| 投影、facet 与 AgentRun 提取 | 准备展示数据，生成显示树及序列化 |

业务使用 `await context.fact(node, name)` 和 `await context.measure(node, name)`。
Source、缓存路径、HTTP、预加载时机和并发由框架管理。
纯规则仍可同步返回结果；需要事实读取的规则使用 async。同一执行器处理两者，CLI 拥有 event loop。

`structure_fields` 必须覆盖分类和关联依赖。`detail_fields` / `detail_facts` 把同一纯 builder
中的详情输出推迟到请求时计算；更复杂的跨 span 事实由 FactProducer 表达。关联证据不要求认领
或重新挂接它所在的 span。`prepare=False` 的 assemble 仅建模，展示准备独立执行。

## 依赖与结果

EvidenceDependency 指定物理 span 与字段，FactDependency 指定目标 Node 与命名 fact。
依赖准备完成后才进入同步 compute；compute 不访问 Source。事实缺失不等于零，读取失败不变成
成功的空事实。Measurer 保持独立结果状态，计算失败形成 error Measurement。

tree lease 关闭后，context 的异步访问失效；需要再次访问时从 Dataset 重新取得。renderer 可以消费
已经准备好的纯数据，但不能自行加载或重新执行分析。批量 detector 不应将所有 tree 收集到列表，
应保留轻量引用或落盘的必要统计值。


## Detector 定义与依赖

Node 和 Dataset 分析共用 `Detector<Input, Context>`：显式 `id` 标识规则，`requires` 声明
直接依赖，普通 `detect(input, context)` 函数贡献观察。输入约束直接用泛型表达，不另设按粒度
命名的类型。Python decorator 或 TS 函数名均不定义该公共契约。

```typescript
interface Detector<Input, Context> {
  id: string;
  requires?: readonly string[];
  detect(input: Input, context: Context): Findings | Promise<Findings>;
}
const nodeRule: Detector<Node, AnalysisContext> = { id: "node_rule", detect: inspectNode };
const datasetRule: Detector<Dataset, BatchContext> = { id: "dataset_rule", detect: inspectDataset };
```

Python 提供 `Detector(id="summary", requires=("detail",), detect=summarize)`，支持同步与
异步函数。旧的普通函数注册是边界简写，统一转成定义对象；显式对象的身份不依赖函数名。

共享执行器展开依赖图、检查重复 id/未知依赖/环，按依赖顺序调用并保存 `DetectorResult`。
`context.result(id)` 只访问已声明的直接依赖，提供 `status`、`error` 和 `findings()` 迭代器，
不触发执行。成功空输出、执行失败和业务 coverage 不完整分别表达；普通异常保留部分结果，
无关 detector 继续执行。结果存储由粒度对应的执行入口提供，公共执行器不依赖文件路径。

| 执行粒度 | 依赖与去重范围 | 保存和访问 |
|---|---|---|
| Node | 一次分析中的同一 trace、同一 node | 节点间后序，节点内依赖顺序；该 node 执行完成后释放依赖输出视图 |
| Dataset | 同一 Run 的固定 Dataset | 落盘结果按 detector 读取，共享依赖执行一次 |

Node 的 requires 不隐式引用子节点或 Dataset detector；需要子树归因时读取后序累积的 findings，
需要跨 trace 聚合时由 batch detector 消费 Run 的结果。两种粒度使用各自的注册作用域，跨粒度
requires 在执行前报错。重新分析会形成独立结果，Source 字段缓存仍可复用。

单条完整分析和显式 detector 选择共用同一执行入口；完整分析额外包含现有 base/kind rules。
`analysis.detector_runs` 记录 node 执行状态，随分析快照保存；Dataset Run 的 `detector_runs.jsonl`
记录 node 和 dataset 执行，并标注作用域与 trace/node 身份。失败形成可见观察并使 Batch Run
标记为 partial，不能用 trace 成功读取代替 detector 成功执行。

业务负责依赖、结果含义和汇总，框架负责校验、调度、去重与结果生命周期；这些依赖不替代
Fact / Measurement 的证据加载依赖，也不交给 renderer。Python 已实现；TS 按共享契约接入，
尚未提供该能力。Batch 应用与结果协议见 [Batch：Detector 组合与依赖](batch.md#detector-组合与依赖)。
