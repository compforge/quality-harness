# Trace Harness

Trace Harness 将请求留下的遥测建模为逻辑事件，提供度量、诊断与证据展示，支持单条排障和
跨 trace 分析。本文介绍各语言实现共享的概念、职责和边界；详细流程归所属 SDK 的文档。

跨语言行为以 [规范](../spec/trace-harness.md)、`schema/trace/` 和 `conformance/trace/`
为准。Python 与 TypeScript 遵守共享契约，功能覆盖可以不同；下面的 SDK 设计不自动代表
其他语言已实现相同能力。

## 1. 问题与边界

Trace Harness 消费 OTel / Jaeger span，解释一次请求内部发生了什么、时间花在哪里、
哪些调用出现了异常。它提供建模、度量、判读和展示机制；服务名、业务阶段、模型配置含义
等知识由消费项目贡献。环境发现、凭据解析和部署信息查询也由消费项目负责。

输入可以来自线上 trace_id 或离线文件。主动测试留下的 trace_id 也进入相同分析流程，
驱动请求和捕获 trace_id 由调用方负责；本设计不把主动 CaseDriver 当作已交付能力。
trace 解释执行过程，eval 评估产出质量，perf 验证负载下的性能；各领域 SDK 互不 import。

**Node 是逻辑分析单元，tree 是按需构建的关系索引。** 一次逻辑调用可能对应多个物理 span，
不能用物理 span 数直接代替逻辑调用数。关系索引既服务展示，也可以服务 transform、
祖先查找和需要拓扑的分析；不要求所有分析先构造完整展示树。

## 2. 核心概念

| 概念 | 职责 |
| --- | --- |
| Source | 统一访问遥测存储，提供选择候选和读取证据的能力 |
| Dataset | 固定的一批输入与来源引用，供单条或批量分析复用；不持有本轮结果 |
| NormSpan | 统一 id、parent、时间、service 等物理骨架；保留原始证据，不预先判定业务 kind |
| Node | 由一个或多个物理 span 建模得到的逻辑事件，持有 facts、parent 边与 span 溯源 |
| KindSpec | 识别事件、认领关联 span、构造基础 facts，并声明 kind 的判读与投影 |
| FactTransform | 从已有 facts 和关系派生新的具名 fact |
| Measurement | 独立量化结果，包含指标定义、scope、单位、维度、状态与证据 |
| Finding | 有依据的诊断发现，保留来源、严重程度及锚点；不等于验收 Verdict |
| TraceContext | 单条 trace 的模型、证据与按需关系索引 |
| AnalysisContext | 引用原 trace，持有本轮 Measurement 与 Finding |
| TraceHarness | 分析配置的作用域 owner，显式组合业务贡献并执行分析和投影 |
| AgentRun IR | 从 trace 提取的 agent 语义表示，保留 Node / span 溯源 |

按照 [Kernel](kernel.md#dataset-与反复评估)，Node 可以作为 Observation，
`trace_id + node_id` 定义 node-grain Unit。固定的事实和来源构成可复评 Dataset；
本轮 Measurement / Finding 属于 EvaluationRun 的 Worksheet，不写回 Dataset。
提升到 request、trace 或 trajectory 粒度时，需要明确自己的 Unit key 和 Worksheet。

### 作用域与业务扩展

每个 `TraceHarness` 独立持有 specs、transforms、measurers、detectors、facets 和
agent_run_extractor。业务包通过 `TraceContributions` 提供确定性扩展，Host 在构造时
显式合并，避免 import 顺序或重复安装的包副本改变行为。

业务原始字段在 KindSpec 建模边界转成命名 facts；transform、分析与展示消费这些 facts
和关系，不自行猜测厂商原始字段。通用 kind 支持的标准约定以实现和共享规范为准，
厂商及服务专属映射留在业务包。

Trajectory Harness 拥有轨迹评估语义；两者通过有版本和来源的产物关联，不能因为需要
批量评估就让两个 SDK 相互依赖。训练数据导出需有具体消费协议后再设计。

## 3. 关键取舍

| 取舍 | 原因 |
| --- | --- |
| 保留 Node 建模 | 多个物理 span 可能是一件逻辑事件，统一归属避免每个指标重复拼装 |
| tree 按需索引 | 关系算法与展示复用同一结构，不强制所有分析承担展示成本 |
| 原始字段止于建模边界 | 通用度量与展示无需理解每个服务的协议方言 |
| facts、Measurement、Finding 分开 | 数据转换、量化和判断有不同生命周期，支持复评和离线还原 |
| Source 通用、环境装配留消费方 | 存储读取机制可以复用，环境与凭据知识不进入领域 SDK |
| 单条与 batch 共享语义 | 同一指标在批量统计和单条下钻中必须含义一致 |

## 4. 分析方式与阅读入口

Python SDK 已实现以下共享主线：

```text
Source ── select ──▶ Dataset ── detect ──▶ Findings
```

select 圈定固定成员，detect 执行选定的判读；measure 作为可选依赖按需准备，Measurement
仍独立保存并支持直接查看。Finding 可以是描述性观察或异常线索，不能将未执行或失败解释为
零问题。执行配置和结果由分析运行持有，不写回 Dataset。

单条和 batch 共享 Node / tree、事实访问与度量机制。结构和必要事实先准备，详细内容按使用
需求加载，缓存由 harness 管理；逻辑上可访问 tree 不等于整棵树的内容始终驻留内存。
单条可作为只有一个成员的 Dataset 进入同一分析入口，现有 Node detector 保留逐节点执行语义。
批量额外负责跨 trace 调度、聚合与对比，不另建一套懒加载引擎。
统一入口以加载策略选择按需或预加载，单条 / batch、是否生成 HTML 均不决定该策略；
预加载与按需加载共享数据语义、缓存和资源预算。

- [内核](../sdks/python/trace_harness/docs/kernel.md)：核心概念、业务接口与框架执行边界。
- [数据加载](../sdks/python/trace_harness/docs/load.md)：字段依赖、按需读取、预加载、缓存与资源生命周期。
- [单条分析](../sdks/python/trace_harness/docs/single.md)：单 trace 分析、call stack、HTML 与下钻。
- [Batch 分析](../sdks/python/trace_harness/docs/batch.md)：Dataset、有限内存执行、统计与版本对比。
- [Python SDK](../sdks/python/trace_harness/AGENTS.md)：代码地图和开发约定。
- [TypeScript SDK](../sdks/typescript/trace-harness/README.md)：接入与支持范围。
