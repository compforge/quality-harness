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
| node / batch detector 的判读逻辑 | 后序逐 Node / 每 Dataset 调用，提供证据与运行生命周期 |
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
