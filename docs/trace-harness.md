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

单条分析以一次执行为范围，解释逻辑调用、度量与诊断证据；batch 分析以固定数据集为输入，
将同一套分析语义应用到多个 Unit，再聚合或对比结果。batch 改变调度、存储与聚合方式，
不另行定义 Node、Measurement 或 Finding。具体数据完整性和资源边界由相应分析流程说明。

- [单条分析](../sdks/python/trace_harness/docs/single.md)：采集、建模、转换、度量、判读和展示。
- [Batch 分析](../sdks/python/trace_harness/docs/batch.md)：数据集、有限内存执行、统计口径和版本对比；
  文中明确区分现有 corpus 能力与待实现设计。
- [Python SDK](../sdks/python/trace_harness/AGENTS.md)：代码地图和开发约定。
- [TypeScript SDK](../sdks/typescript/trace-harness/README.md)：接入与支持范围。
